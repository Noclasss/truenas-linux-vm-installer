"""Explicit Ubuntu Desktop operator workflow: check, create, or inspect.

The launcher install.sh remains preview-only. The `check` command performs
allowlisted read-only middleware calls; only the explicit `create` subcommand
can invoke the create-only writer. No fake middleware is exposed by this CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import sys
from typing import Any, Callable
import uuid

from .desktop_catalogue import DesktopCatalogueError, verify_desktop_source_id
from .desktop_integrity import DesktopIntegrityError, verify_desktop_artifacts
from .desktop_live_writer import (
    DesktopArtifactInputs,
    DesktopCreateOnlyWriter,
    DesktopCreateRequest,
    DesktopWriterError,
    _safe_read_probe,
    _sha256_file,
)
from .desktop_manifest import (
    ManifestInspectionError,
    find_prior_target_manifest,
    inspect,
    make_target_key,
)
from .desktop_write_contracts import load_reviewed_desktop_fixture
from .installer_core.compatibility import CompatibilityError
from .installer_core.middleware import MiddlewareError, MidcltClient
from .installer_core.safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain
from .readonly_preflight import PreflightError, ReadOnlyPreflightRequest, run_read_only_preflight

OPERATIONS_ROOT = Path("/var/lib/truenas-linux-desktop/operations")
TARGET_REGISTRY_ROOT = Path("/var/lib/truenas-linux-desktop/targets")
_TARGET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,62}\Z")
_POOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UUID_JSON = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json\Z")
_MAX_TARGET_BYTES = 64 * 1024
_REQUIRED_FIELDS = {
    "schema_version", "expected_hostname", "expected_build", "vm_name", "pool_name",
    "vcpus", "memory_mib", "disk_gib", "host_reserve_mib", "pool_reserve_mib",
    "source_id", "network", "cache_on_selected_pool", "iso_path",
    "checksum_manifest_path", "signature_path", "keyring_path", "seed_path",
    "seed_sha256", "service_account", "manifest_path",
}


class OperatorWorkflowError(RuntimeError):
    """Invalid target, unavailable prerequisite, or safely refused operation."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_target(path: Path) -> dict[str, Any]:
    path = Path(path)
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            fd = None
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid not in {0, os.geteuid()}
                or before.st_mode & 0o077
                or before.st_size > _MAX_TARGET_BYTES
            ):
                raise OperatorWorkflowError("target record must be a bounded private regular file")
            raw = stream.read(_MAX_TARGET_BYTES + 1)
            after = os.fstat(stream.fileno())
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
            if identity(before) != identity(after) or len(raw) != after.st_size:
                raise OperatorWorkflowError("target record changed while being read")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except OperatorWorkflowError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise OperatorWorkflowError("target record is unreadable or malformed") from exc
    finally:
        if fd is not None:
            os.close(fd)
    if not isinstance(data, dict) or set(data) != _REQUIRED_FIELDS:
        raise OperatorWorkflowError("target record fields do not match the reviewed schema")
    return data


def _absolute_file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise OperatorWorkflowError(f"{label} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise OperatorWorkflowError(f"{label} must be canonical and absolute")
    return path


def _validate_manifest_path(value: Any, *, must_be_new: bool = True) -> Path:
    path = _absolute_file(value, "manifest_path")
    if path.parent != OPERATIONS_ROOT or not _UUID_JSON.fullmatch(path.name):
        raise OperatorWorkflowError(f"manifest_path must be a UUID-named JSON file directly under {OPERATIONS_ROOT}")
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise OperatorWorkflowError("manifest path cannot be inspected") from exc
    if must_be_new and info is not None:
        raise OperatorWorkflowError("manifest path already exists; choose a new path")
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077):
        raise OperatorWorkflowError("existing manifest path is not a private regular file")
    return path


def _validate_record_fields(record: dict[str, Any], fixture: dict[str, Any]) -> tuple[DesktopCreateRequest, DesktopArtifactInputs, Path]:
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise OperatorWorkflowError("unsupported target record version")
    if not isinstance(record.get("expected_hostname"), str) or not _HOSTNAME.fullmatch(record["expected_hostname"]):
        raise OperatorWorkflowError("expected_hostname must be the exact operator-selected host name")
    if record.get("expected_build") != fixture.get("truenas_version"):
        raise OperatorWorkflowError("expected_build must match the checked-in exact-build fixture")
    if not isinstance(record.get("vm_name"), str) or not _TARGET_NAME.fullmatch(record["vm_name"]):
        raise OperatorWorkflowError("VM name is invalid")
    if not isinstance(record.get("pool_name"), str) or not _POOL_NAME.fullmatch(record["pool_name"]):
        raise OperatorWorkflowError("pool name is invalid")
    if record["pool_name"].casefold() in {"boot-pool", "freenas-boot"}:
        raise OperatorWorkflowError("boot pools cannot be selected")
    for key, minimum, maximum in (
        ("vcpus", 2, 128), ("memory_mib", 6144, 1_048_576), ("disk_gib", 25, 65_536),
        ("host_reserve_mib", 1, 1_048_576), ("pool_reserve_mib", 1, 1_048_576),
    ):
        value = record.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise OperatorWorkflowError(f"{key} must be an integer from {minimum} to {maximum}")
    if record.get("network") != "none":
        raise OperatorWorkflowError("the first create attempt requires network=none; NICs and bridges are not accepted")
    if type(record.get("cache_on_selected_pool")) is not bool or record["cache_on_selected_pool"] is not False:
        raise OperatorWorkflowError("the first create workflow requires cache_on_selected_pool=false")
    if record.get("source_id") not in {"ubuntu-desktop-minimal", "ubuntu-desktop"}:
        raise OperatorWorkflowError("source_id must be an explicit supported ISO catalogue ID")
    if not isinstance(record.get("seed_sha256"), str) or not _SHA256.fullmatch(record["seed_sha256"]):
        raise OperatorWorkflowError("seed_sha256 must be a lowercase SHA-256 from the seed build/verification output")
    account = record.get("service_account")
    if not isinstance(account, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", account):
        raise OperatorWorkflowError("service_account must be an exact local account name")
    try:
        service = pwd.getpwnam(account)
    except KeyError as exc:
        raise OperatorWorkflowError("the selected VM-process service account is not present on this host") from exc
    if service.pw_uid <= 0 or shutil.which("runuser") is None:
        raise OperatorWorkflowError("a non-root service account and runuser are required for staged-file access checks")
    manifest_path = _validate_manifest_path(record.get("manifest_path"))
    artifacts = DesktopArtifactInputs(
        iso=_absolute_file(record.get("iso_path"), "iso_path"),
        checksum_manifest=_absolute_file(record.get("checksum_manifest_path"), "checksum_manifest_path"),
        signature=_absolute_file(record.get("signature_path"), "signature_path"),
        keyring=_absolute_file(record.get("keyring_path"), "keyring_path"),
        seed=_absolute_file(record.get("seed_path"), "seed_path"),
    )
    request = DesktopCreateRequest(
        vm_name=record["vm_name"], pool_name=record["pool_name"], vcpus=record["vcpus"],
        memory_mib=record["memory_mib"], disk_gib=record["disk_gib"],
        host_reserve_mib=record["host_reserve_mib"], pool_reserve_mib=record["pool_reserve_mib"],
        source_id=record["source_id"], cache_on_selected_pool=False, bridge_name=None,
        expected_hostname=record["expected_hostname"],
    )
    return request, artifacts, manifest_path


def _make_writer(
    record: dict[str, Any], fixture: dict[str, Any], *,
    access_probe: Callable[[Path, str], bool] = _safe_read_probe,
    artifact_verifier: Callable[..., dict[str, Any]] = verify_desktop_artifacts,
    catalogue_verifier: Callable[..., dict[str, Any]] = verify_desktop_source_id,
) -> DesktopCreateOnlyWriter:
    request, artifacts, manifest_path = _validate_record_fields(record, fixture)
    return DesktopCreateOnlyWriter(
        MidcltClient(timeout=120, expected_version=fixture["truenas_version"]),
        fixture, request, artifacts, manifest_path=manifest_path,
        service_account=record["service_account"], access_probe=access_probe,
        artifact_verifier=artifact_verifier, catalogue_verifier=catalogue_verifier,
        target_registry_root=TARGET_REGISTRY_ROOT,
    )


def _private_state_root_state() -> dict[str, Any]:
    result = {}
    for label, path in (("operations", OPERATIONS_ROOT), ("target_registry", TARGET_REGISTRY_ROOT)):
        try:
            info = path.lstat()
            result[label] = {
                "exists": True, "directory": stat.S_ISDIR(info.st_mode),
                "owner_uid": info.st_uid, "mode": oct(stat.S_IMODE(info.st_mode)),
                "trusted": stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not (info.st_mode & 0o022),
            }
        except FileNotFoundError:
            result[label] = {"exists": False, "will_be_created_privately_by_create": True}
        except OSError:
            result[label] = {"exists": False, "trusted": False}
    return result


def _existing_target_record(record: dict[str, Any], fixture: dict[str, Any]) -> str | None:
    target_key = make_target_key(
        record["expected_hostname"], fixture["truenas_version"],
        record["pool_name"], record["vm_name"],
    )
    guard_path = TARGET_REGISTRY_ROOT / f"{target_key}.json"
    try:
        guard_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise OperatorWorkflowError("target registry state cannot be inspected safely") from exc
    else:
        return os.fspath(guard_path)
    try:
        prior = find_prior_target_manifest(OPERATIONS_ROOT, target_key, record["pool_name"], record["vm_name"])
    except ManifestInspectionError as exc:
        raise OperatorWorkflowError("operation records could not be scanned safely; no create is allowed") from exc
    return os.fspath(prior) if prior is not None else None


def _check_target(
    target_path: Path,
    *,
    client_factory: Callable[..., Any] | None = None,
    access_probe: Callable[[Path, str], bool] | None = None,
    artifact_verifier: Callable[..., dict[str, Any]] | None = None,
    catalogue_verifier: Callable[..., dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], DesktopCreateOnlyWriter, dict[str, Any]]:
    client_factory = client_factory or MidcltClient
    access_probe = access_probe or _safe_read_probe
    artifact_verifier = artifact_verifier or verify_desktop_artifacts
    catalogue_verifier = catalogue_verifier or verify_desktop_source_id
    record = _read_target(target_path)
    fixture = load_reviewed_desktop_fixture()
    request, artifacts, manifest_path = _validate_record_fields(record, fixture)
    existing = _existing_target_record(record, fixture)
    if existing:
        raise OperatorWorkflowError(f"target already has an operation record at {existing}; inspect it read-only")
    writer = DesktopCreateOnlyWriter(
        client_factory(timeout=120, expected_version=fixture["truenas_version"]),
        fixture, request, artifacts, manifest_path=manifest_path,
        service_account=record["service_account"], access_probe=access_probe,
        artifact_verifier=artifact_verifier, catalogue_verifier=catalogue_verifier,
        target_registry_root=TARGET_REGISTRY_ROOT,
    )
    try:
        preflight_dict, floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report = writer._check_inputs()
        preflight_request = ReadOnlyPreflightRequest(**preflight_dict)
        preflight = run_read_only_preflight(writer.client, fixture, preflight_request)
    except (CompatibilityError, PreflightError, DesktopWriterError, DesktopIntegrityError, DesktopCatalogueError, OSError, ValueError) as exc:
        writer.client.close()
        raise OperatorWorkflowError(f"local prerequisite or read-only host check failed: {exc}") from exc
    if preflight.system_hostname != record["expected_hostname"]:
        writer.client.close()
        raise OperatorWorkflowError("verified system.info hostname differs from expected_hostname; no create is permitted")
    if preflight.truenas_version != record["expected_build"]:
        writer.client.close()
        raise OperatorWorkflowError("verified host build differs from expected_build; no create is permitted")
    phrase = writer.confirmation_phrase_from_values(
        floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report,
    )
    report = {
        "status": "passed",
        "operator_supplied": {
            "expected_hostname": record["expected_hostname"],
            "expected_build": record["expected_build"],
            "vm_name": record["vm_name"], "pool_name": record["pool_name"],
            "vm_dataset": f"{record['pool_name']}/linux-vms/{record['vm_name']}",
            "zvol": f"{record['pool_name']}/linux-vms/{record['vm_name']}/disk0",
            "vcpus": record["vcpus"], "memory_mib": record["memory_mib"],
            "disk_gib": record["disk_gib"], "host_reserve_mib": record["host_reserve_mib"],
            "pool_reserve_mib": record["pool_reserve_mib"], "network": "none",
            "source_id": record["source_id"], "cache_on_selected_pool": False,
            "service_account": record["service_account"],
            "manifest_path": os.fspath(manifest_path),
            "input_files": {
                "iso": os.fspath(artifacts.iso), "checksum_manifest": os.fspath(artifacts.checksum_manifest),
                "signature": os.fspath(artifacts.signature), "keyring": os.fspath(artifacts.keyring),
                "seed": os.fspath(artifacts.seed),
            },
        },
        "proposed_plan": writer._confirmation_plan(
            floor, iso_sha, seed_size, seed_sha, artifact_report, catalogue_report,
        ),
        "verified_facts": {
            "host": {"hostname": preflight.system_hostname, "build": preflight.truenas_version,
                     "matches_target_record": True},
            "pool": {"name": preflight.pool_name, "free_bytes": preflight.pool_free_bytes,
                     "healthy_online_and_capacity_passed": True,
                     "shared_parent_exists": preflight.shared_parent_exists},
            "system": {"physical_memory_bytes": preflight.physical_memory_bytes,
                       "available_memory_bytes": preflight.available_memory_bytes,
                       "maximum_supported_vcpus": preflight.maximum_supported_vcpus},
            "resource_conflicts": {"vm_name_absent": preflight.target_vm_name_absent,
                                   "target_storage_paths_absent": preflight.target_storage_paths_absent},
            "artifact": {key: artifact_report.get(key) for key in (
                "filename", "sha256", "size_bytes", "signing_key_fingerprint",
                "manifest_sha256", "signature_verified", "iso_bytes_verified",
            )},
            "source_catalogue": catalogue_report,
            "seed": {"path": os.fspath(artifacts.seed), "sha256": seed_sha,
                     "size_bytes": _sha256_file(artifacts.seed)[0], "mode": "0600",
                     "contents_logged": False},
            "read_only_checks": list(preflight.checks),
            "middleware_write_methods_called": [],
        },
        "state_paths": _private_state_root_state(),
        "staged_file_access": {
            "service_account": record["service_account"],
            "account_existence_verified": False,
            "staged_copy_probe": "pending; actual host runuser open check runs after local staging and before vm.create",
        },
        "confirmation_phrase": phrase,
        "outstanding_prerequisites": [
            "separate operator approval of this exact plan remains required",
            "typed confirmation is required by the explicit create action",
            "staged ISO/seed service-account readability will be rechecked after local staging and before VM creation",
            "VM remains stopped; installation, desktop login, and guest behavior are unvalidated",
        ],
        "policy": {"network": "none", "nic_create": False, "vm_start": False, "autostart": False,
                   "automatic_retry": False, "rollback": False, "deletion": False},
    }
    return report, writer, {"record": record, "fixture": fixture, "request": request}


def _output(value: Any, stream=None) -> None:
    print(json.dumps(value, indent=2, sort_keys=True), file=stream or sys.stdout)


def _read_only_access(path: Path, service_account: str) -> dict[str, Any]:
    try:
        readable = _safe_read_probe(path, service_account)
    except Exception:
        readable = False
    return {"path": os.fspath(path), "service_account": service_account, "readable": readable,
            "middleware_contacted": False, "write_performed": False}


def main(argv: list[str] | None = None, *, input_fn: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    validate_parser = commands.add_parser("validate", help="validate local target/artifact/seed inputs; no middleware contact")
    validate_parser.add_argument("--target", required=True, type=Path)
    check_parser = commands.add_parser("check", help="contact TrueNAS with read-only calls; never writes middleware or manifests")
    check_parser.add_argument("--target", required=True, type=Path)
    create_parser = commands.add_parser("create", help="explicitly create the approved stopped, no-NIC VM")
    create_parser.add_argument("--target", required=True, type=Path)
    inspect_parser = commands.add_parser("inspect", help="inspect a writer-v1 manifest; read-only")
    inspect_parser.add_argument("--manifest", required=True, type=Path)
    inspect_parser.add_argument("--live-inventory", action="store_true", help="also issue exact-build read-only inventory queries")
    access_parser = commands.add_parser("access", help="test whether a local file can be opened by the selected account")
    access_parser.add_argument("--path", required=True, type=Path)
    access_parser.add_argument("--service-account", required=True)
    commands.add_parser("new-manifest-path", help="print an unused private manifest path; no file or middleware call")
    args = parser.parse_args(argv)
    injected_input = input_fn is not None
    input_fn = input_fn or input

    try:
        if args.action == "new-manifest-path":
            _output({"candidate_manifest_path": os.fspath(OPERATIONS_ROOT / f"{uuid.uuid4()}.json"),
                     "created": False, "middleware_contacted": False})
            return 0
        if args.action == "access":
            result = _read_only_access(args.path, args.service_account)
            _output(result)
            return 0 if result["readable"] else 1
        if args.action == "inspect":
            fixture = load_reviewed_desktop_fixture() if args.live_inventory else None
            client = MidcltClient(timeout=120, expected_version=fixture["truenas_version"]) if fixture else None
            try:
                _output(inspect(args.manifest, client=client, fixture=fixture))
            finally:
                if client is not None:
                    client.close()
            return 0
        record = _read_target(args.target)
        fixture = load_reviewed_desktop_fixture()
        request, artifacts, manifest_path = _validate_record_fields(record, fixture)
        if args.action == "validate":
            verifier = verify_desktop_artifacts(artifacts.iso, artifacts.checksum_manifest, artifacts.signature, artifacts.keyring)
            catalogue = verify_desktop_source_id(artifacts.iso, request.source_id)
            seed_size, seed_sha = _sha256_file(artifacts.seed)
            if seed_sha != record["seed_sha256"] or stat.S_IMODE(artifacts.seed.lstat().st_mode) != 0o600:
                raise OperatorWorkflowError("seed digest or private mode does not match the target record")
            _output({"status": "passed", "middleware_contacted": False,
                     "artifact": verifier, "source_catalogue": catalogue,
                     "seed": {"path": os.fspath(artifacts.seed), "sha256": seed_sha, "size_bytes": seed_size, "mode": "0600"},
                     "manifest_path": os.fspath(manifest_path), "manifest_exists": manifest_path.exists()})
            return 0
        report, writer, _ = _check_target(args.target)
        if args.action == "check":
            writer.client.close()
            _output(report)
            return 0
        if args.action == "create":
            _output(report, stream=sys.stderr)
            if not injected_input and not sys.stdin.isatty():
                writer.client.close()
                raise OperatorWorkflowError("create requires an interactive terminal for exact plan confirmation")
            print(f"Type exactly: {report['confirmation_phrase']}", file=sys.stderr, flush=True)
            try:
                answer = input_fn("Confirm create: ")
            except (EOFError, OSError):
                writer.client.close()
                raise OperatorWorkflowError("confirmation input ended; no create was attempted") from None
            if answer != report["confirmation_phrase"]:
                writer.client.close()
                raise OperatorWorkflowError("confirmation did not match the verified target; no create was attempted")
            try:
                result = writer.create(confirmation=answer)
            finally:
                writer.client.close()
            _output({"status": "create_returned", "verified_plan": report, "result": result,
                     "manifest": inspect(result["manifest_path"]),
                     "note": "create-only result; VM not started and guest installation not verified"})
            return 0
    except (OperatorWorkflowError, DesktopWriterError, ManifestInspectionError, MiddlewareError,
            CompatibilityError, PreflightError, DesktopIntegrityError, DesktopCatalogueError,
            OSError, ValueError) as exc:
        print(f"desktop operator {getattr(args, 'action', 'command')} refused: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
