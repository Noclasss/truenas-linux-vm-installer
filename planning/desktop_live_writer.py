"""Isolated, create-only Ubuntu Desktop middleware adapter.

This module is deliberately not imported by a launcher or public CLI. It never
starts/stops a VM, deletes resources, changes host networking, downloads an
artifact, or performs automatic recovery. A live invocation still requires a
separately authorized target and exact-build read-only preflight.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import select
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, NoReturn

if __package__:
    from . import ubuntu_desktop as desktop
    from .desktop_integrity import DesktopIntegrityError, verify_desktop_artifacts, verify_staged_seed
    from .mock_install import LIVE_PROFILE_ALLOWLIST
    from .desktop_catalogue import verify_desktop_source_id
    from .desktop_manifest import ManifestInspectionError, find_prior_target_manifest, make_target_key
    from .installer_core.compatibility import CompatibilityError
    from .installer_core.safe_filesystem import (
        FilesystemSafetyError, atomic_replace_private_file,
        ensure_trusted_directory_chain, write_private_new_file,
    )
    from .installer_core.schema import (
        SchemaCheckedClient, SchemaError, schema_errors, validate_method_request,
    )
    from .readonly_preflight import (
        READ_ONLY_METHODS, PreflightError, ReadOnlyPreflightRequest,
        run_read_only_preflight,
    )
else:
    import ubuntu_desktop as desktop
    from desktop_integrity import DesktopIntegrityError, verify_desktop_artifacts, verify_staged_seed
    from mock_install import LIVE_PROFILE_ALLOWLIST
    from desktop_catalogue import verify_desktop_source_id
    from desktop_manifest import ManifestInspectionError, find_prior_target_manifest, make_target_key
    from installer_core.compatibility import CompatibilityError
    from installer_core.safe_filesystem import (
        FilesystemSafetyError, atomic_replace_private_file,
        ensure_trusted_directory_chain, write_private_new_file,
    )
    from installer_core.schema import (
        SchemaCheckedClient, SchemaError, schema_errors, validate_method_request,
    )
    from readonly_preflight import (
        READ_ONLY_METHODS, PreflightError, ReadOnlyPreflightRequest,
        run_read_only_preflight,
    )

WRITE_METHODS = frozenset({"pool.dataset.create", "vm.create", "vm.device.create"})
_FORMAT = "truenas-linux-desktop-create-only-v1"
_VM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,62}\Z")
_POOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_SOURCE_IDS = frozenset({"ubuntu-desktop-minimal", "ubuntu-desktop"})
_COPY_CHUNK = 1024 * 1024


class DesktopWriterError(RuntimeError):
    """Creation stopped safely; inspect the private operation record manually."""


@dataclass(frozen=True)
class DesktopCreateRequest:
    vm_name: str
    pool_name: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    host_reserve_mib: int
    pool_reserve_mib: int
    source_id: str
    cache_on_selected_pool: bool
    # The initial create-only slice intentionally does not add a NIC.
    bridge_name: str | None = None
    expected_hostname: str = ""


@dataclass(frozen=True)
class DesktopArtifactInputs:
    iso: Path
    checksum_manifest: Path
    signature: Path
    keyring: Path
    seed: Path


def capacity_floor(request: DesktopCreateRequest) -> dict[str, int]:
    """Return separately accounted selected-pool space requirements in bytes."""
    if type(request.cache_on_selected_pool) is not bool:
        raise DesktopWriterError("cache location must be explicitly classified before preflight")
    virtual_disk = request.disk_gib * 1024**3
    iso = desktop.ARTIFACT_SIZE_BYTES
    seed_cap = desktop.SEED_MEDIA_SIZE_BYTES
    cache_copy = iso if request.cache_on_selected_pool else 0
    retained_headroom = request.pool_reserve_mib * 1024**2
    return {
        "virtual_disk_bytes": virtual_disk,
        "staged_iso_bytes": iso,
        "seed_cap_bytes": seed_cap,
        "source_cache_copy_bytes": cache_copy,
        "retained_headroom_bytes": retained_headroom,
        "required_free_bytes": virtual_disk + iso + seed_cap + cache_copy + retained_headroom,
    }


def _sha256_file(path: Path, expected_size: int | None = None) -> tuple[int, str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DesktopWriterError("artifact input is not safely readable") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise DesktopWriterError("artifact input must be a regular non-symlink file")
        if expected_size is not None and before.st_size != expected_size:
            raise DesktopWriterError("artifact size does not match its fixed limit")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, _COPY_CHUNK)
            if not block:
                break
            total += len(block)
            if expected_size is not None and total > expected_size:
                raise DesktopWriterError("artifact exceeded its fixed size while being read")
            digest.update(block)
        after = os.fstat(fd)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after) or total != before.st_size:
            raise DesktopWriterError("artifact changed while being read")
        return total, digest.hexdigest()
    finally:
        os.close(fd)


def _effective_uid() -> int:
    return os.geteuid()


def _safe_read_probe(path: Path, service_account: str, *, runner=subprocess.run) -> bool:
    """Prove the selected host service account can open this exact staged file."""
    if not isinstance(service_account, str) or not service_account or any(c in service_account for c in "\x00\r\n"):
        return False
    try:
        account = pwd.getpwnam(service_account)
        runuser = shutil.which("runuser")
        if runuser is None:
            return False
        result = runner(
            [runuser, "-u", service_account, "--", "python3", "-c",
             "import os,sys; f=os.open(sys.argv[1],os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)); os.read(f,1); os.close(f)",
             os.fspath(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False, env={"PATH": os.defpath, "LC_ALL": "C"},
        )
    except (KeyError, OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0 and account.pw_uid >= 0


def _exact_mountpoint(value: Any, pool: str) -> Path:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise DesktopWriterError("created filesystem dataset did not return a safe mountpoint")
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise DesktopWriterError("dataset mountpoint is not a canonical absolute path")
    parts = path.parts
    if len(parts) < 5 or parts[:3] != ("/", "mnt", pool):
        raise DesktopWriterError("dataset mountpoint is outside the selected pool")
    return Path(value)


def _require_rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise DesktopWriterError(f"{label} read-back returned an invalid row set")
    return value


def _validate_write_result(methods: dict[str, Any], method: str, result: Any) -> None:
    returns = methods.get(method, {}).get("returns")
    if not isinstance(returns, list) or len(returns) != 1:
        raise DesktopWriterError(f"reviewed return contract is unavailable for {method}")
    errors = schema_errors(result, returns[0], f"{method}.result", methods[method])
    if errors:
        raise DesktopWriterError(f"{method} returned a value outside its reviewed contract")


def _without_device_password(attributes: Any) -> dict[str, Any] | None:
    if not isinstance(attributes, dict):
        return None
    return {key: value for key, value in attributes.items() if key != "password"}


def _device_attributes_match(actual: Any, expected: dict[str, Any]) -> bool:
    safe_actual = _without_device_password(actual)
    safe_expected = _without_device_password(expected)
    if safe_actual is None or safe_expected is None:
        return False
    if safe_expected.get("dtype") != "DISPLAY":
        return safe_actual == safe_expected
    for key, value in safe_expected.items():
        if key in {"port", "web_port"} and value is None:
            continue
        if safe_actual.get(key) != value:
            return False
    port, web_port = safe_actual.get("port"), safe_actual.get("web_port")
    return (
        type(port) is int and 5900 <= port <= 65535
        and type(web_port) is int and 5900 <= web_port <= 65535
        and port != web_port
    )


def _parse_qemu_process_owner(output: bytes) -> tuple[int, str]:
    try:
        text = output.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise DesktopWriterError("active process inventory encoding is invalid") from exc
    owners: set[tuple[int, str]] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(None, 1)
        if len(fields) != 2:
            raise DesktopWriterError("active process inventory is malformed")
        uid_text, command = fields
        if not (command.startswith("qemu-system-") or command == "qemu-kvm"):
            continue
        if not uid_text.isdecimal():
            raise DesktopWriterError("active QEMU process identity is malformed")
        try:
            account = pwd.getpwuid(int(uid_text))
        except (KeyError, OSError, ValueError) as exc:
            raise DesktopWriterError("active QEMU process UID is not mapped to a host account") from exc
        owners.add((account.pw_uid, account.pw_name))
    if len(owners) != 1:
        raise DesktopWriterError("exactly one active QEMU service-account identity is required")
    return next(iter(owners))


def _active_qemu_process_owner() -> tuple[int, str]:
    """Identify the one active QEMU owner without buffering unbounded output."""
    ps = shutil.which("ps")
    if not ps or not os.path.isabs(ps):
        raise DesktopWriterError("absolute ps is required to identify the active VM-process account")
    process: subprocess.Popen[bytes] | None = None
    data = bytearray()
    deadline = time.monotonic() + 10
    try:
        process = subprocess.Popen(
            [ps, "-eo", "euid=,comm="], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "LC_ALL": "C"}, bufsize=0,
        )
        if process.stdout is None:
            raise DesktopWriterError("active QEMU process inventory could not be read")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired([ps, "-eo", "euid=,comm="], 10)
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                raise subprocess.TimeoutExpired([ps, "-eo", "euid=,comm="], 10)
            chunk = os.read(process.stdout.fileno(), min(4096, 65537 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 64 * 1024:
                raise DesktopWriterError("active QEMU process inventory exceeded its output bound")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode != 0:
            raise DesktopWriterError("active QEMU process ownership query failed")
        return _parse_qemu_process_owner(bytes(data))
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopWriterError("active QEMU process ownership could not be queried") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.SubprocessError:
                    pass
            if process.stdout is not None:
                process.stdout.close()


def _verify_vm_process_account(
    service_account: str, *, runner: Callable[..., Any] | None = None,
) -> pwd.struct_passwd:
    """Verify local account and runuser execution without touching staged files."""
    if not isinstance(service_account, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", service_account):
        raise DesktopWriterError("selected VM-process service account name is invalid")
    try:
        account = pwd.getpwnam(service_account)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise DesktopWriterError("selected VM-process service account is unavailable on this host") from exc
    if account.pw_uid <= 0:
        raise DesktopWriterError("VM-process service account must be a non-root account")
    runuser = shutil.which("runuser")
    if not runuser or not os.path.isabs(runuser):
        raise DesktopWriterError("runuser is required at an absolute host PATH location for VM-process checks")
    execute = runner or subprocess.run
    active_uid, active_name = _active_qemu_process_owner()
    if (active_uid, active_name) != (account.pw_uid, account.pw_name):
        raise DesktopWriterError("selected service account does not match the active QEMU process owner")
    probe = (
        "import os; "
        f"assert os.geteuid() == {account.pw_uid}; "
        "fd = os.open('/dev/null', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)); "
        "os.close(fd)"
    )
    execute = runner or subprocess.run
    try:
        result = execute(
            [runuser, "-u", service_account, "--", "python3", "-c", probe],
            check=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env={"PATH": os.defpath, "LC_ALL": "C"}, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopWriterError(
            "runuser cannot execute local checks as the selected VM-process service account"
        ) from exc
    if result.returncode != 0:
        raise DesktopWriterError(
            "runuser cannot execute local checks as the selected VM-process service account"
        )
    return account


class _CaptureMethodsClient:
    """Capture the exact method table already read by the compatibility check."""
    def __init__(self, client: Any):
        self.client = client
        self.methods: dict[str, Any] | None = None

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        result = self.client.call(method, [] if params is None else params)
        if method == "core.get_methods" and isinstance(result, dict):
            self.methods = result
        return result


class DesktopCreateOnlyWriter:
    """A narrowly scoped adapter; no CLI or public launcher constructs it."""
    def __init__(
        self,
        client: Any,
        fixture: dict[str, Any],
        request: DesktopCreateRequest,
        artifacts: DesktopArtifactInputs,
        *,
        manifest_path: Path,
        service_account: str,
        reported_service_account_uid: int | None = None,
        guest_identity: dict[str, str] | None = None,
        access_probe: Callable[[Path, str], bool] = _safe_read_probe,
        artifact_verifier: Callable[..., dict[str, Any]] = verify_desktop_artifacts,
        catalogue_verifier: Callable[..., dict[str, Any]] = verify_desktop_source_id,
        target_registry_root: Path = Path("/var/lib/truenas-linux-desktop/targets"),
    ):
        self.client = client
        self.fixture = fixture
        self.request = request
        self.artifacts = artifacts
        self.manifest_path = Path(manifest_path)
        self.service_account = service_account
        self.reported_service_account_uid = reported_service_account_uid
        self.guest_identity = dict(guest_identity) if guest_identity is not None else None
        self._verified_service_account: dict[str, Any] | None = None
        self.access_probe = access_probe
        self.artifact_verifier = artifact_verifier
        self.catalogue_verifier = catalogue_verifier
        self.target_registry_root = Path(target_registry_root)

    def _require_root(self) -> None:
        if _effective_uid() != 0:
            raise DesktopWriterError("create-only adapter requires an authenticated root host process")

    def _require_vm_process_account(self) -> pwd.struct_passwd:
        return _verify_vm_process_account(self.service_account)

    def _check_inputs(self) -> tuple[dict[str, Any], dict[str, int], str, int, str, dict[str, Any], dict[str, Any]]:
        req = self.request
        self._require_root()
        account = self._require_vm_process_account()
        if (self.reported_service_account_uid is not None
                and account.pw_uid != self.reported_service_account_uid):
            raise DesktopWriterError("live VM-process account UID differs from the operator-reported UID")
        self._verified_service_account = {"name": account.pw_name, "uid": account.pw_uid}
        if not _VM_NAME.fullmatch(req.vm_name) or not _POOL_NAME.fullmatch(req.pool_name):
            raise DesktopWriterError("VM or pool name is invalid")
        if (
            not isinstance(req.expected_hostname, str)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", req.expected_hostname)
        ):
            raise DesktopWriterError("an exact expected TrueNAS hostname is required")
        if req.pool_name.casefold() in {"boot-pool", "freenas-boot"}:
            raise DesktopWriterError("boot pools cannot be selected")
        if req.source_id not in _SOURCE_IDS:
            raise DesktopWriterError("an explicit verified Desktop source ID is required")
        if req.bridge_name is not None:
            raise DesktopWriterError("this create-only slice omits NIC creation; bridge selection is not accepted")
        if not callable(self.access_probe):
            raise DesktopWriterError("VM-process file-access verification is required")
        if not isinstance(self.fixture, dict) or self.fixture.get("truenas_version") != "TrueNAS-26.0.0-BETA.2":
            raise DesktopWriterError("only the exact reviewed Beta.2 fixture is supported")
        if any(type(v) is not int or v < 1 for v in (req.vcpus, req.memory_mib, req.disk_gib, req.host_reserve_mib, req.pool_reserve_mib)):
            raise DesktopWriterError("resource sizes and reserves must be positive integers")
        floor = capacity_floor(req)
        preflight_request = ReadOnlyPreflightRequest(
            profile="ubuntu-desktop", vm_name=req.vm_name, pool_name=req.pool_name,
            bridge_name=None, vcpus=req.vcpus, memory_mib=req.memory_mib,
            disk_gib=req.disk_gib, host_reserve_mib=req.host_reserve_mib,
            pool_reserve_mib=req.pool_reserve_mib,
            staging_reserve_bytes=floor["staged_iso_bytes"] + floor["seed_cap_bytes"] + floor["source_cache_copy_bytes"],
        )
        if not callable(getattr(self.client, "call", None)):
            raise DesktopWriterError("middleware client is invalid")
        try:
            artifact_report = self.artifact_verifier(
                self.artifacts.iso, self.artifacts.checksum_manifest,
                self.artifacts.signature, self.artifacts.keyring,
            )
            if (
                not isinstance(artifact_report, dict)
                or artifact_report.get("signature_verified") is not True
                or artifact_report.get("iso_bytes_verified") is not True
                or artifact_report.get("filename") != desktop.IMAGE_FILENAME
                or artifact_report.get("sha256") != desktop.ARTIFACT_SHA256
                or artifact_report.get("size_bytes") != desktop.ARTIFACT_SIZE_BYTES
                or artifact_report.get("signing_key_fingerprint") != desktop.SIGNING_KEY_FINGERPRINT
                or not isinstance(artifact_report.get("manifest_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", artifact_report["manifest_sha256"])
            ):
                raise DesktopWriterError("source ISO verification did not confirm the pinned signer and exact artifact identity")
            catalogue_report = self.catalogue_verifier(self.artifacts.iso, req.source_id)
            if (
                not isinstance(catalogue_report, dict)
                or catalogue_report.get("verified_from_iso") is not True
                or catalogue_report.get("source_id") != req.source_id
                or not isinstance(catalogue_report.get("catalogue_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", catalogue_report["catalogue_sha256"])
            ):
                raise DesktopWriterError("source ID was not verified from the exact ISO catalogue")
        except Exception as exc:
            if isinstance(exc, DesktopWriterError):
                raise
            raise DesktopWriterError("source ISO signature, pinned-byte, or catalogue verification failed") from exc
        iso_size, iso_sha = _sha256_file(self.artifacts.iso, desktop.ARTIFACT_SIZE_BYTES)
        if iso_size != desktop.ARTIFACT_SIZE_BYTES or iso_sha != desktop.ARTIFACT_SHA256:
            raise DesktopWriterError("source ISO bytes differ from the fixed verified pin")
        seed_size, seed_sha = _sha256_file(self.artifacts.seed)
        if not 0 < seed_size <= desktop.SEED_MEDIA_SIZE_BYTES:
            raise DesktopWriterError("seed image is empty or exceeds its fixed staging cap")
        seed_info = self.artifacts.seed.lstat()
        if stat.S_IMODE(seed_info.st_mode) != 0o600:
            raise DesktopWriterError("source seed media must be private mode 0600")
        return asdict(preflight_request), floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report

    def _confirmation_plan(
        self, floor: dict[str, int], iso_sha: str, seed_size: int, seed_sha: str,
        artifact_report: dict[str, Any], catalogue_report: dict[str, Any],
    ) -> dict[str, Any]:
        shared_parent = f"{self.request.pool_name}/linux-vms"
        vm_dataset = f"{shared_parent}/{self.request.vm_name}"
        zvol = f"{vm_dataset}/disk0"
        target_key = make_target_key(
            self.request.expected_hostname, self.fixture["truenas_version"],
            self.request.pool_name, self.request.vm_name,
        )
        target_guard_path = self.target_registry_root / f"{target_key}.json"
        return {
            "build": self.fixture["truenas_version"], "host": self.request.expected_hostname,
            "vm": self.request.vm_name, "pool": self.request.pool_name,
            "vm_dataset": f"{self.request.pool_name}/linux-vms/{self.request.vm_name}",
            "zvol": f"{self.request.pool_name}/linux-vms/{self.request.vm_name}/disk0",
            "vcpus": self.request.vcpus, "memory_mib": self.request.memory_mib,
            "disk_gib": self.request.disk_gib, "autostart": False,
            "vm_start": False, "nic_create": False, "network": "none",
            "service_account": self.service_account,
            "service_account_identity": {
                "operator_reported_uid": self.reported_service_account_uid,
                "live_verified": self._verified_service_account,
            },
            "guest_identity": self.guest_identity,
            "source_id": self.request.source_id,
            "catalogue_sha256": catalogue_report.get("catalogue_sha256"),
            "artifact": {
                "iso_path": os.fspath(self.artifacts.iso), "iso_sha256": iso_sha,
                "iso_size_bytes": artifact_report.get("size_bytes"),
                "checksum_manifest_path": os.fspath(self.artifacts.checksum_manifest),
                "manifest_sha256": artifact_report.get("manifest_sha256"),
                "signature_path": os.fspath(self.artifacts.signature),
                "keyring_path": os.fspath(self.artifacts.keyring),
                "signing_key_fingerprint": artifact_report.get("signing_key_fingerprint"),
            },
            "seed": {"path": os.fspath(self.artifacts.seed), "sha256": seed_sha, "size_bytes": seed_size},
            "manifest_path": os.fspath(self.manifest_path),
            "target_guard_path": os.fspath(target_guard_path),
            "persistent_resources_to_create": {
                "datasets": [
                    {"id": shared_parent, "type": "FILESYSTEM", "create_only_if_absent": True},
                    {"id": vm_dataset, "type": "FILESYSTEM", "create": True},
                ],
                "zvol": {"id": zvol, "type": "VOLUME", "size_bytes": self.request.disk_gib * 1024**3},
                "staged_files": [
                    {"dataset_relative_path": "ubuntu-installer.iso", "sha256": iso_sha,
                     "size_bytes": artifact_report.get("size_bytes"), "mode": "0600"},
                    {"dataset_relative_path": "cidata.img", "sha256": seed_sha,
                     "size_bytes": seed_size, "mode": "0600", "sensitive": True},
                ],
                "vm": {"name": self.request.vm_name, "autostart": False,
                       "state_after_create": "STOPPED", "network": "none"},
                "devices": [
                    {"dtype": "DISPLAY", "type": "SPICE", "bind": "127.0.0.1",
                     "web": True, "wait": False, "resolution": "1024x768",
                     "port": None, "web_port": None, "password": None},
                    {"dtype": "DISK", "path": f"/dev/zvol/{zvol}",
                     "type": "VIRTIO", "create_zvol": False},
                    {"dtype": "CDROM", "dataset_relative_path": "ubuntu-installer.iso"},
                    {"dtype": "RAW", "dataset_relative_path": "cidata.img",
                     "exists": True, "boot": False, "type": "AHCI"},
                ],
                "private_state_files": [
                    os.fspath(self.manifest_path), os.fspath(target_guard_path),
                ],
                "private_state_directories_if_absent": [
                    os.fspath(self.manifest_path.parent), os.fspath(self.target_registry_root),
                ],
            },
            "capacity": floor,
        }

    def confirmation_phrase_from_values(
        self, floor: dict[str, int], iso_sha: str, seed_size: int, seed_sha: str,
        artifact_report: dict[str, Any], catalogue_report: dict[str, Any],
    ) -> str:
        """Bind confirmation to the exact validated target and input identities."""
        plan = self._confirmation_plan(floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report)
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return f"CREATE {self.request.vm_name} {digest[:12]}"

    def confirmation_phrase(self) -> str:
        """Return the plan-bound phrase to show and obtain from the operator."""
        _, floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report = self._check_inputs()
        return self.confirmation_phrase_from_values(
            floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report,
        )

    def create(self, *, confirmation: str) -> dict[str, Any]:
        if desktop.PROFILE_ID not in LIVE_PROFILE_ALLOWLIST:
            raise DesktopWriterError("profile is not allowlisted for live writes; no middleware write attempted")
        preflight_dict, floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report = self._check_inputs()
        confirmed_seed_size, confirmed_seed_sha = seed_size, seed_sha
        preflight_request = ReadOnlyPreflightRequest(**preflight_dict)
        capture = _CaptureMethodsClient(self.client)
        try:
            preflight_report = run_read_only_preflight(capture, self.fixture, preflight_request)
        except (PreflightError, CompatibilityError, SchemaError) as exc:
            raise DesktopWriterError("read-only exact-build preflight refused before mutation") from exc
        if preflight_report.system_hostname != self.request.expected_hostname:
            raise DesktopWriterError("verified host hostname differs from the target record; refusing before mutation")
        methods = capture.methods
        if not isinstance(methods, dict):
            raise DesktopWriterError("exact runtime method schema was not captured")
        checked = SchemaCheckedClient(
            self.client, methods=methods, allowed_read_methods=set(READ_ONLY_METHODS),
            allowed_write_methods=set(WRITE_METHODS),
        )
        vm_dataset = f"{self.request.pool_name}/linux-vms/{self.request.vm_name}"
        shared_parent = f"{self.request.pool_name}/linux-vms"
        zvol = f"{vm_dataset}/disk0"
        plan = self._confirmation_plan(floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report)
        plan_digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        expected_confirmation = self.confirmation_phrase_from_values(
            floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report,
        )
        if confirmation != expected_confirmation:
            raise DesktopWriterError("fresh confirmation does not match the reverified plan")

        # Capture the pre-existing shared parent from an exact read-only row.
        datasets = _require_rows(checked.call_read("pool.dataset.query", [[], {"select": ["id", "type", "mountpoint"]}]), "dataset query")
        shared_rows = [row for row in datasets if row.get("id") == shared_parent]
        if len(shared_rows) > 1 or (shared_rows and shared_rows[0].get("type") != "FILESYSTEM"):
            raise DesktopWriterError("shared parent state is ambiguous or not a filesystem")
        shared_existed = bool(shared_rows)
        target_key = make_target_key(
            self.request.expected_hostname, self.fixture["truenas_version"],
            self.request.pool_name, self.request.vm_name,
        )
        try:
            prior = find_prior_target_manifest(
                self.manifest_path.parent, target_key,
                self.request.pool_name, self.request.vm_name,
            )
        except ManifestInspectionError as exc:
            raise DesktopWriterError("could not establish that the target has no prior operation; refusing before mutation") from exc
        if prior is not None:
            raise DesktopWriterError(f"target already has an operation record at {prior}; inspect it read-only before any new action")
        # Recheck after all read-only host queries and before the guard/manifest or first middleware write.
        service = self._require_vm_process_account()
        if self._verified_service_account != {"name": service.pw_name, "uid": service.pw_uid}:
            raise DesktopWriterError("VM-process account identity changed after confirmation; no create was attempted")
        run_id = str(uuid.uuid4())
        guard_path = self.target_registry_root / f"{target_key}.json"
        guard: dict[str, Any] = {
            "format": "truenas-linux-desktop-target-guard-v1",
            "target_key": target_key,
            "manifest_path": os.fspath(self.manifest_path),
            "status": "preparing",
            "run_id": run_id,
        }
        try:
            ensure_trusted_directory_chain(self.manifest_path.parent, "Desktop operation directory", create=True)
            ensure_trusted_directory_chain(self.target_registry_root, "Desktop target registry", create=True)
            write_private_new_file(guard_path, json.dumps(guard, sort_keys=True, indent=2) + "\n")
        except (OSError, FilesystemSafetyError) as exc:
            raise DesktopWriterError("target is already claimed or the private target guard could not be created") from exc
        intended_resources = []
        if not shared_existed:
            intended_resources.append({"kind": "dataset", "identifier": shared_parent, "creation_method": "pool.dataset.create"})
        intended_resources.extend((
            {"kind": "dataset", "identifier": vm_dataset, "creation_method": "pool.dataset.create"},
            {"kind": "zvol", "identifier": zvol, "creation_method": "pool.dataset.create"},
            {"kind": "vm", "identifier": self.request.vm_name, "creation_method": "vm.create"},
            {"kind": "device", "identifier": f"{self.request.vm_name}:display", "creation_method": "vm.device.create"},
            {"kind": "device", "identifier": f"{self.request.vm_name}:disk", "creation_method": "vm.device.create"},
            {"kind": "device", "identifier": f"{self.request.vm_name}:installer", "creation_method": "vm.device.create"},
            {"kind": "device", "identifier": f"{self.request.vm_name}:seed", "creation_method": "vm.device.create"},
            {"kind": "staged_file", "identifier": "ubuntu-installer.iso", "creation_method": "filesystem.stage"},
            {"kind": "staged_file", "identifier": "cidata.img", "creation_method": "filesystem.stage"},
        ))
        manifest = {
            "format": _FORMAT, "run_id": run_id, "status": "preparing",
            "target_key": target_key,
            "plan": plan, "plan_digest": plan_digest,
            "inventory_before": {"shared_parent_exists": shared_existed},
            "intended_resources": intended_resources,
            "resources": [], "files": [], "pending": None, "observations": [],
            "policy": {"automatic_retry": False, "rollback": False, "deletion": False, "vm_start": False},
        }
        try:
            write_private_new_file(self.manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        except (OSError, FilesystemSafetyError) as exc:
            raise DesktopWriterError("private durable operation record could not be created") from exc

        def save() -> None:
            try:
                atomic_replace_private_file(self.manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
                guard["status"] = manifest.get("status")
                pending_record = manifest.get("pending")
                guard["pending"] = {
                    key: pending_record.get(key)
                    for key in ("method", "identifier", "api_id", "vm_id", "state")
                    if isinstance(pending_record, dict) and key in pending_record
                } if isinstance(pending_record, dict) else None
                atomic_replace_private_file(guard_path, json.dumps(guard, sort_keys=True, indent=2) + "\n")
            except (OSError, FilesystemSafetyError) as exc:
                raise DesktopWriterError("private operation record/target guard update failed; stop for manual reconciliation") from exc

        def pending(method: str, identifier: str, payload: Any) -> None:
            manifest["pending"] = {
                "method": method, "identifier": identifier,
                "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "state": "intent_recorded",
            }
            save()

        def record_returned_id(api_id: int, vm_id: int | None = None) -> None:
            pending_record = manifest.get("pending")
            if not isinstance(pending_record, dict):
                raise DesktopWriterError("successful write response has no durable pending intent")
            pending_record["api_id"] = api_id
            if vm_id is not None:
                pending_record["vm_id"] = vm_id
            save()

        def stop_ambiguous(message: str, exc: Exception | None = None) -> NoReturn:
            manifest["status"] = "reconcile_required"
            manifest["failure"] = message
            if isinstance(manifest.get("pending"), dict):
                manifest["pending"]["state"] = "ambiguous"
            try:
                save()
            except DesktopWriterError:
                pass
            error = DesktopWriterError("operation stopped; read-only reconciliation and human review required; no retry, rollback, or deletion")
            raise error from None

        def read_dataset(identifier: str, expected_type: str) -> dict[str, Any]:
            rows = _require_rows(checked.call_read("pool.dataset.query", [[["id", "=", identifier]], {"select": ["id", "type", "mountpoint", "volsize"]}]), "dataset query")
            exact = [row for row in rows if row.get("id") == identifier]
            if len(exact) != 1 or exact[0].get("type") != expected_type:
                raise DesktopWriterError("dataset exact identity/type read-back failed")
            return exact[0]

        def create_dataset(identifier: str, kind: str, size_bytes: int | None = None) -> dict[str, Any]:
            payload = {"name": identifier, "type": kind, "create_ancestors": False}
            if kind == "VOLUME":
                payload["volsize"] = size_bytes
            pending("pool.dataset.create", identifier, payload)
            try:
                result = checked.call_write("pool.dataset.create", [payload])
                _validate_write_result(methods, "pool.dataset.create", result)
                if not isinstance(result, dict) or result.get("id") != identifier or result.get("type") != kind:
                    raise DesktopWriterError("dataset create response identity/type mismatch")
                row = read_dataset(identifier, kind)
                if kind == "VOLUME":
                    volsize = row.get("volsize")
                    if not isinstance(volsize, dict) or type(volsize.get("parsed")) is not int or volsize["parsed"] != size_bytes:
                        raise DesktopWriterError("zvol read-back size is not the exact requested byte count")
                manifest["resources"].append({"kind": "dataset" if kind == "FILESYSTEM" else "zvol", "identifier": identifier, "created_by_run": True, "verified": True, "state": "confirmed", "size_bytes": size_bytes})
                manifest["pending"] = None
                save()
                return row
            except Exception as exc:
                stop_ambiguous("dataset create/read-back incomplete", exc)

        if not shared_existed:
            create_dataset(shared_parent, "FILESYSTEM")
        dataset_row = create_dataset(vm_dataset, "FILESYSTEM")
        create_dataset(zvol, "VOLUME", self.request.disk_gib * 1024**3)
        try:
            mountpoint = _exact_mountpoint(dataset_row.get("mountpoint"), self.request.pool_name)
            ensure_trusted_directory_chain(mountpoint, "Desktop staging dataset")
        except (DesktopWriterError, FilesystemSafetyError) as exc:
            stop_ambiguous("staging mountpoint could not be validated", exc)

        staged_iso = mountpoint / "ubuntu-installer.iso"
        staged_seed = mountpoint / "cidata.img"

        def stage(source: Path, target: Path, expected_size: int, expected_sha: str, label: str) -> None:
            pending("filesystem.stage", os.fspath(target), {"size_bytes": expected_size, "sha256": expected_sha, "owner_uid": service.pw_uid, "mode": "0600"})
            try:
                # All parent components are checked; file creation is exclusive and no-follow.
                ensure_trusted_directory_chain(target.parent, "Desktop staged media")
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                digest = hashlib.sha256()
                total = 0
                try:
                    os.fchown(fd, service.pw_uid, service.pw_gid)
                    os.fchmod(fd, 0o600)
                    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    source_info = os.fstat(source_fd)
                    if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
                        os.close(source_fd)
                        raise DesktopWriterError("staging source changed identity or size")
                    with os.fdopen(source_fd, "rb", closefd=True, buffering=0) as inp, os.fdopen(fd, "wb", closefd=True) as out:
                        fd = -1
                        while True:
                            block = inp.read(_COPY_CHUNK)
                            if not block:
                                break
                            total += len(block)
                            if total > expected_size:
                                raise DesktopWriterError("staged media exceeded its fixed byte limit")
                            digest.update(block)
                            out.write(block)
                        out.flush()
                        os.fsync(out.fileno())
                finally:
                    if fd >= 0:
                        os.close(fd)
                directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size or total != expected_size or digest.hexdigest() != expected_sha or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != service.pw_uid or info.st_gid != service.pw_gid:
                    raise DesktopWriterError("staged media identity, ownership, mode, size, or digest read-back failed")
                if not self.access_probe(target, self.service_account):
                    raise DesktopWriterError("VM-process account could not open the staged file")
                if label == "ISO":
                    self.artifact_verifier(target, self.artifacts.checksum_manifest, self.artifacts.signature, self.artifacts.keyring)
                manifest["files"].append({
                    "kind": label.lower(), "path": os.fspath(target),
                    "sha256": expected_sha, "size_bytes": expected_size,
                    "owner_uid": service.pw_uid, "owner_gid": service.pw_gid,
                    "mode": "0600", "state": "confirmed",
                    "access_probe": "runuser" if self.access_probe is _safe_read_probe else "injected_test_probe",
                    "selected_service_account_open_verified": self.access_probe is _safe_read_probe,
                    "live_vm_process_readable_verified": False,
                    "verified": True,
                })
                manifest["pending"] = None
                save()
            except Exception as exc:
                stop_ambiguous(f"{label} staging/access check incomplete", exc)

        stage(self.artifacts.iso, staged_iso, desktop.ARTIFACT_SIZE_BYTES, iso_sha, "ISO")
        staged_seed_sha = confirmed_seed_sha
        stage(self.artifacts.seed, staged_seed, confirmed_seed_size, staged_seed_sha, "seed")

        def query_vm(vm_id: int) -> dict[str, Any]:
            fields = ["id", "name", "memory", "cores", "vcpus", "threads", "bootloader", "enable_secure_boot", "autostart", "ensure_display_device", "display_available", "devices", "status"]
            rows = _require_rows(checked.call_read("vm.query", [[["id", "=", vm_id]], {"select": fields}]), "VM query")
            exact = [row for row in rows if row.get("id") == vm_id]
            if len(exact) != 1:
                raise DesktopWriterError("VM exact identity read-back failed")
            return exact[0]

        def query_devices(vm_id: int) -> list[dict[str, Any]]:
            rows = _require_rows(checked.call_read("vm.device.query", [[["vm", "=", vm_id]], {"select": ["id", "vm", "attributes"]}]), "VM device query")
            if any(row.get("vm") != vm_id for row in rows):
                raise DesktopWriterError("VM device query returned a row for an unexpected VM")
            return rows

        vm_payload = {
            "name": self.request.vm_name, "vcpus": 1, "cores": self.request.vcpus,
            "threads": 1, "memory": self.request.memory_mib, "bootloader": "UEFI",
            "enable_secure_boot": False, "autostart": False, "ensure_display_device": True,
        }
        pending("vm.create", self.request.vm_name, vm_payload)
        vm_result = None
        try:
            vm_result = checked.call_write("vm.create", [vm_payload])
            if isinstance(vm_result, dict) and type(vm_result.get("id")) is int and vm_result["id"] > 0:
                record_returned_id(vm_result["id"])
            _validate_write_result(methods, "vm.create", vm_result)
            if (
                not isinstance(vm_result, dict)
                or type(vm_result.get("id")) is not int or vm_result.get("id") <= 0
                or vm_result.get("name") != self.request.vm_name
                or vm_result.get("autostart") is not False
                or vm_result.get("ensure_display_device") is not True
                or not isinstance(vm_result.get("status"), dict)
                or vm_result["status"].get("pid") is not None
            ):
                raise DesktopWriterError("VM create response identity or stopped-state mismatch")
            vm_id = vm_result["id"]
            vm_row = query_vm(vm_id)
            expected_vm = {
                "name": self.request.vm_name, "memory": self.request.memory_mib,
                "cores": self.request.vcpus, "vcpus": 1, "threads": 1,
                "bootloader": "UEFI", "enable_secure_boot": False,
                "autostart": False, "ensure_display_device": True,
            }
            if any(vm_row.get(key) != value for key, value in expected_vm.items()):
                raise DesktopWriterError("VM property read-back mismatch")
            vm_status = vm_row.get("status")
            if not isinstance(vm_status, dict) or vm_status.get("pid") is not None:
                raise DesktopWriterError("VM status read-back did not confirm a stopped VM")
            response_devices = vm_result.get("devices")
            if not isinstance(response_devices, list):
                raise DesktopWriterError("vm.create response did not include its device list")
            actual_devices = query_devices(vm_id)
            # Beta.2 source stores ensure_display_device as VM metadata; it does
            # not create a DISPLAY device. A returned device here is unexpected.
            if (
                response_devices or actual_devices
                or vm_result.get("display_available") is not False
                or vm_row.get("display_available") is not False
            ):
                observed = []
                for source, rows in (("vm.create", response_devices), ("vm.device.query", actual_devices)):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        attrs = row.get("attributes")
                        observed.append({
                            "kind": "device", "identifier": f"{self.request.vm_name}:unexpected",
                            "api_id": row.get("id") if type(row.get("id")) is int else None,
                            "vm_id": vm_id,
                            "dtype": attrs.get("dtype") if isinstance(attrs, dict) and isinstance(attrs.get("dtype"), str) else None,
                            "observed_via": source, "state": "ambiguous",
                        })
                manifest["resources"].append({
                    "kind": "vm", "identifier": self.request.vm_name,
                    "api_id": vm_id, "created_by_run": True, "verified": True,
                    "state": "confirmed", "properties": expected_vm,
                })
                manifest["vm_id"] = vm_id
                manifest["pending"]["observed_resources"] = observed
                manifest["pending"]["observed_state"] = {
                    "vm_id": vm_id,
                    "response_display_available": vm_result.get("display_available"),
                    "readback_display_available": vm_row.get("display_available"),
                    "response_device_count": len(response_devices),
                    "readback_device_count": len(actual_devices),
                }
                stop_ambiguous("vm.create returned an unexpected device state; manual reconciliation required")
            manifest["resources"].append({
                "kind": "vm", "identifier": self.request.vm_name,
                "api_id": vm_id, "created_by_run": True, "verified": True,
                "state": "confirmed", "properties": expected_vm,
            })
            manifest["pending"] = None
            manifest["vm_id"] = vm_id
            save()
        except Exception as exc:
            if manifest.get("status") == "reconcile_required":
                raise
            stop_ambiguous("VM create/read-back incomplete", exc)

        devices_to_create = (
            ("display", {"dtype": "DISPLAY", "type": "SPICE", "bind": "127.0.0.1",
                          "web": True, "wait": False, "resolution": "1024x768",
                          "port": None, "web_port": None, "password": None}),
            ("disk", {"dtype": "DISK", "path": f"/dev/zvol/{zvol}", "type": "VIRTIO", "create_zvol": False}),
            ("installer", {"dtype": "CDROM", "path": os.fspath(staged_iso)}),
            ("seed", {"dtype": "RAW", "path": os.fspath(staged_seed), "exists": True, "boot": False, "type": "AHCI"}),
        )
        def device_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{
                "kind": "device",
                "api_id": row.get("id") if type(row.get("id")) is int else None,
                "vm_id": row.get("vm") if type(row.get("vm")) is int else None,
                "dtype": row.get("attributes", {}).get("dtype")
                if isinstance(row.get("attributes"), dict) else None,
                "state": "ambiguous",
            } for row in rows]

        def matches_device_inventory(rows: list[dict[str, Any]], expected: dict[int, dict[str, Any]]) -> bool:
            return (
                len(rows) == len(expected)
                and all(type(row.get("id")) is int and row["id"] > 0 for row in rows)
                and {row.get("id") for row in rows} == set(expected)
                and all(
                    row.get("vm") == vm_id
                    and _device_attributes_match(row.get("attributes"), expected[row["id"]])
                    for row in rows
                )
            )

        expected_devices: dict[int, dict[str, Any]] = {}
        for label, attributes in devices_to_create:
            try:
                before = query_devices(vm_id)
                if not matches_device_inventory(before, expected_devices):
                    manifest["observations"].append({
                        "source": "vm.device.query before create",
                        "resources": device_snapshot(before),
                    })
                    stop_ambiguous("unexpected VM devices appeared before the next create")
                params = [{"vm": vm_id, "attributes": attributes}]
                recorded_params = [{
                    "vm": vm_id,
                    "attributes": _without_device_password(attributes),
                }]
                if label == "seed":
                    try:
                        verify_staged_seed(staged_seed, {"sha256": confirmed_seed_sha, "size_bytes": confirmed_seed_size})
                    except DesktopIntegrityError as exc:
                        stop_ambiguous("staged seed failed its confirmed digest before attach; no seed attach", exc)
                pending("vm.device.create", f"{self.request.vm_name}:{label}", recorded_params)
                result = checked.call_write("vm.device.create", params)
                if isinstance(result, dict) and type(result.get("id")) is int and result["id"] > 0:
                    record_returned_id(result["id"], vm_id)
                _validate_write_result(methods, "vm.device.create", result)
                if (
                    not isinstance(result, dict)
                    or type(result.get("id")) is not int or result.get("id") <= 0
                    or result.get("vm") != vm_id
                    or not _device_attributes_match(result.get("attributes"), attributes)
                ):
                    raise DesktopWriterError("device create response identity or properties mismatch")
                expected_devices[result["id"]] = attributes
                after = query_devices(vm_id)
                if not matches_device_inventory(after, expected_devices):
                    manifest["pending"]["observed_resources"] = device_snapshot(after)
                    raise DesktopWriterError("device inventory contains unexpected or mismatched rows")
                manifest["resources"].append({"kind": "device", "identifier": f"{self.request.vm_name}:{label}", "api_id": result["id"], "vm_id": vm_id, "created_by_run": True, "verified": True, "state": "confirmed", "creation_method": "vm.device.create", "attributes": _without_device_password(attributes)})
                if label == "display":
                    manifest["display_device_id"] = result["id"]
                manifest["pending"] = None
                save()
            except Exception as exc:
                if manifest.get("status") == "reconcile_required":
                    raise
                stop_ambiguous(f"{label} device create/read-back incomplete", exc)

        manifest["status"] = "created_not_started_live_unverified"
        manifest["pending"] = None
        save()
        return {
            "run_id": run_id, "status": manifest["status"],
            "vm_id": manifest["vm_id"], "display_device_id": manifest["display_device_id"],
            "resource_count": len(manifest["resources"]),
            "files": [{k: row[k] for k in ("kind", "path", "sha256", "size_bytes", "mode", "access_probe", "selected_service_account_open_verified", "live_vm_process_readable_verified")} for row in manifest["files"]],
            "vm_started": False, "live_profile_enabled": False,
            "target_key": target_key,
            "manifest_path": os.fspath(self.manifest_path),
        }
