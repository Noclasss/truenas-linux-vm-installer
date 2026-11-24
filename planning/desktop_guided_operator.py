"""One-command, private-input launcher for the reviewed Desktop create flow."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

from . import desktop_catalogue, desktop_seed_cli, desktop_readonly_preview as preview
from .desktop_autoinstall import DesktopAutoinstallError, build_desktop_autoinstall_user_data
from .desktop_catalogue import DesktopCatalogueError
from .desktop_integrity import DesktopIntegrityError, verify_desktop_artifacts
from .desktop_live_writer import DesktopWriterError
from .desktop_operator import OPERATIONS_ROOT, _HOSTNAME, _POOL_NAME, _TARGET_NAME, _UUID_JSON, _sha256_file
from .desktop_write_contracts import load_reviewed_desktop_fixture
from .installer_core.artifacts import ArtifactError, download_and_verify
from .installer_core.safe_filesystem import FilesystemSafetyError, write_private_new_file
from .readonly_preflight import PreflightError
from .ubuntu_desktop import (
    ARTIFACT_MAX_BYTES, ARTIFACT_SHA256, ARTIFACT_SIZE_BYTES, ARTIFACT_URL,
    CHECKSUM_MANIFEST_SHA256, CHECKSUM_MANIFEST_URL,
    CHECKSUM_SIGNATURE_SHA256, CHECKSUM_SIGNATURE_URL,
    IMAGE_FILENAME, SIGNING_KEY_FINGERPRINT,
    UBUNTU_ARCHIVE_KEYRING_SHA256, UBUNTU_ARCHIVE_KEYRING_URL,
    SEED_MEDIA_SIZE_BYTES,
)


class GuidedCreateError(RuntimeError):
    """A local input or safety gate refused the guided create flow."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--pool-name", required=True)
    parser.add_argument("--vcpus", required=True, type=int)
    parser.add_argument("--memory-mib", required=True, type=int)
    parser.add_argument("--disk-gib", required=True, type=int)
    parser.add_argument("--host-reserve-mib", required=True, type=int)
    parser.add_argument("--pool-reserve-mib", required=True, type=int)
    parser.add_argument("--source-id", required=True, choices=("ubuntu-desktop-minimal", "ubuntu-desktop"))
    parser.add_argument("--realname", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--locale", required=True)
    parser.add_argument("--keyboard-layout", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--reported-service-account-uid", required=True, type=int)
    parser.add_argument("--manifest-path")
    return parser


def _settings(args: argparse.Namespace, fixture: dict) -> tuple[dict, Path]:
    if not _HOSTNAME.fullmatch(args.expected_hostname):
        raise GuidedCreateError("expected hostname is invalid")
    if not _TARGET_NAME.fullmatch(args.vm_name) or not _POOL_NAME.fullmatch(args.pool_name):
        raise GuidedCreateError("VM or pool name is invalid")
    if args.pool_name.casefold() in {"boot-pool", "freenas-boot"}:
        raise GuidedCreateError("boot pools cannot be selected")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", args.service_account):
        raise GuidedCreateError("VM-process service account name is invalid")
    if type(args.reported_service_account_uid) is not int or args.reported_service_account_uid <= 0:
        raise GuidedCreateError("the operator-reported VM-process account UID must be a positive integer")
    if args.source_id not in {"ubuntu-desktop-minimal", "ubuntu-desktop"}:
        raise GuidedCreateError("Ubuntu source ID is not supported")
    request = preview._make_request(
        args.vm_name, args.pool_name, args.vcpus, args.memory_mib, args.disk_gib,
        args.host_reserve_mib, args.pool_reserve_mib,
    )
    preview._validate_request(request)
    build_desktop_autoinstall_user_data(
        vm_name=args.vm_name, realname=args.realname, username=args.username,
        password_hash="$6$validation$" + "A" * 86, source_id=args.source_id,
        locale=args.locale, keyboard_layout=args.keyboard_layout, timezone=args.timezone,
    )
    manifest = args.manifest_path or os.fspath(OPERATIONS_ROOT / f"{uuid.uuid4()}.json")
    manifest_path = Path(manifest)
    if manifest_path.parent != OPERATIONS_ROOT or not _UUID_JSON.fullmatch(manifest_path.name):
        raise GuidedCreateError("manifest path must be a UUID-named JSON file under the operations directory")
    if fixture.get("truenas_version") != "TrueNAS-26.0.0-BETA.2":
        raise GuidedCreateError("the reviewed TrueNAS build fixture is unavailable")
    return {
        "expected_hostname": args.expected_hostname,
        "expected_build": fixture["truenas_version"],
        "vm_name": args.vm_name,
        "pool_name": args.pool_name,
        "vcpus": args.vcpus,
        "memory_mib": args.memory_mib,
        "disk_gib": args.disk_gib,
        "host_reserve_mib": args.host_reserve_mib,
        "pool_reserve_mib": args.pool_reserve_mib,
        "source_id": args.source_id,
        "realname": args.realname,
        "username": args.username,
        "locale": args.locale,
        "keyboard_layout": args.keyboard_layout,
        "timezone": args.timezone,
        "service_account": args.service_account,
        "reported_service_account_uid": args.reported_service_account_uid,
        "manifest_path": os.fspath(manifest_path),
    }, manifest_path


def _download_inputs(cache_dir: Path) -> dict[str, Path]:
    pins = {
        "iso": (ARTIFACT_URL, ARTIFACT_SHA256, ARTIFACT_MAX_BYTES, ARTIFACT_SIZE_BYTES),
        "manifest": (CHECKSUM_MANIFEST_URL, CHECKSUM_MANIFEST_SHA256, 1024 * 1024, None),
        "signature": (CHECKSUM_SIGNATURE_URL, CHECKSUM_SIGNATURE_SHA256, 64 * 1024, None),
        "keyring": (UBUNTU_ARCHIVE_KEYRING_URL, UBUNTU_ARCHIVE_KEYRING_SHA256, 5 * 1024 * 1024, None),
    }
    return {
        name: download_and_verify(url, digest, cache_dir, limit, size)
        for name, (url, digest, limit, size) in pins.items()
    }


def _create_command(target_path: Path) -> list[str]:
    command = [sys.executable, "-B", "-m", "planning.desktop_operator", "create", "--target", os.fspath(target_path)]
    return command if os.geteuid() == 0 else ["sudo", "--", *command]


def _show_settings(settings: dict) -> None:
    print("Review the complete plan. No downloads or TrueNAS writes happen until you accept this review:")
    for key, label in (
        ("expected_hostname", "Expected TrueNAS host"), ("expected_build", "Required TrueNAS build"),
        ("vm_name", "VM name"), ("pool_name", "Storage pool"), ("vcpus", "vCPUs"),
        ("memory_mib", "RAM MiB"), ("disk_gib", "Virtual disk GiB"),
        ("host_reserve_mib", "Host reserve MiB"), ("pool_reserve_mib", "Pool reserve MiB"),
        ("source_id", "Ubuntu source"), ("realname", "Guest display name"),
        ("username", "Guest username"), ("locale", "Locale"),
        ("keyboard_layout", "Keyboard layout"), ("timezone", "Timezone"),
        ("service_account", "Reported VM-process account"),
        ("reported_service_account_uid", "Operator-reported account UID (must be reverified live)"),
        ("manifest_path", "Planned operation manifest"),
    ):
        print(f"  {label}: {settings[key]}")
    print("  Network: none; autostart: off; VM start: off")
    print("  VM display: SPICE bound to 127.0.0.1; no display password is required or set.")
    print("Previous host preflight/account results are operator-reported expectations, not current verification.")
    print("The create operator will rerun exact-build/schema, pool, collision, virtualization, RAM, and active-QEMU-account checks.")


_EDITABLE_SETTINGS = (
    ("expected_hostname", "Expected TrueNAS host"), ("vm_name", "VM name"),
    ("pool_name", "Storage pool"), ("vcpus", "vCPUs"), ("memory_mib", "RAM MiB"),
    ("disk_gib", "Virtual disk GiB"), ("host_reserve_mib", "Host reserve MiB"),
    ("pool_reserve_mib", "Pool reserve MiB"), ("source_id", "Ubuntu source"),
    ("realname", "Guest display name"), ("username", "Guest username"),
    ("locale", "Locale"), ("keyboard_layout", "Keyboard layout"),
    ("timezone", "Timezone"), ("service_account", "VM-process account"),
    ("reported_service_account_uid", "Operator-reported account UID"),
)


def _review_settings(settings: dict, fixture: dict, input_fn) -> dict | None:
    integer_fields = {"vcpus", "memory_mib", "disk_gib", "host_reserve_mib",
                      "pool_reserve_mib", "reported_service_account_uid"}
    while True:
        _show_settings(settings)
        choice = input_fn("Choose [A]ccept, [E]dit, or [C]ancel: ").strip().casefold()
        if choice in {"a", "accept"}:
            return settings
        if choice in {"c", "cancel"}:
            print("Cancelled at settings review; no temporary files or TrueNAS writes were created.")
            return None
        if choice not in {"e", "edit"}:
            print("Enter A, E, or C.")
            continue
        print("Editable settings:")
        for index, (_, label) in enumerate(_EDITABLE_SETTINGS, 1):
            print(f"  {index}. {label}")
        selected = input_fn("Setting number (or C to cancel): ").strip()
        if selected.casefold() == "c":
            print("Cancelled at settings review; no temporary files or TrueNAS writes were created.")
            return None
        if not selected.isdecimal() or not 1 <= int(selected) <= len(_EDITABLE_SETTINGS):
            print("That setting number is not valid.")
            continue
        key, label = _EDITABLE_SETTINGS[int(selected) - 1]
        raw = input_fn(f"{label} [{settings[key]}]; Enter keeps current value: ").strip()
        if not raw:
            continue
        try:
            value = int(raw) if key in integer_fields else raw
            edited = argparse.Namespace(**settings)
            setattr(edited, key, value)
            settings, _ = _settings(edited, fixture)
        except (GuidedCreateError, DesktopAutoinstallError, ValueError) as exc:
            print(f"Invalid {label}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None, *, input_fn=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    input_fn = input_fn or input
    try:
        if argv is None and not sys.stdin.isatty():
            raise GuidedCreateError("this flow requires an interactive terminal")
        fixture = load_reviewed_desktop_fixture()
        settings, manifest_path = _settings(args, fixture)
        settings = _review_settings(settings, fixture, input_fn)
        if settings is None:
            return 0
        with tempfile.TemporaryDirectory(prefix="truenas-linux-vm-desktop-inputs.", dir="/tmp") as directory:
            private_root = Path(directory)
            root_info = private_root.lstat()
            if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
                raise GuidedCreateError("temporary input directory is not private")
            cache_dir = private_root / "cache"
            cache_path = cache_dir.resolve()
            pool_root = (Path("/mnt") / settings["pool_name"]).resolve()
            if cache_path == pool_root or pool_root in cache_path.parents:
                raise GuidedCreateError("temporary artifact cache would be on the selected pool")
            print(f"Temporary workspace: {private_root}")
            print("Before the final CREATE approval, PREPARE will download four pinned inputs:")
            for label, digest in (
                ("ISO", ARTIFACT_SHA256), ("checksum manifest", CHECKSUM_MANIFEST_SHA256),
                ("detached signature", CHECKSUM_SIGNATURE_SHA256),
                ("Ubuntu archive keyring", UBUNTU_ARCHIVE_KEYRING_SHA256),
            ):
                print(f"  {label}: {cache_dir / f'sha256-{digest}.artifact'}")
            print(f"  private seed (mode 0600): {private_root / 'cidata.img'}; contains the guest password hash, not plaintext")
            print(f"  private target record (mode 0600): {private_root / 'target.json'}; no password")
            print("The whole temporary workspace is deleted on exit. PREPARE does not authorize any TrueNAS write.")
            if input_fn("Type PREPARE to download/build these temporary local inputs; anything else cancels: ").strip() != "PREPARE":
                print("Cancelled; the empty temporary directory will be removed. No download, seed, sudo, or TrueNAS create was attempted.")
                return 0
            cache_dir.mkdir(mode=0o700)
            print("Downloading and verifying pinned Ubuntu inputs in the private /tmp workspace.", flush=True)
            inputs = _download_inputs(cache_dir)
            artifact_report = verify_desktop_artifacts(inputs["iso"], inputs["manifest"], inputs["signature"], inputs["keyring"])
            if (
                not isinstance(artifact_report, dict)
                or artifact_report.get("signature_verified") is not True
                or artifact_report.get("iso_bytes_verified") is not True
                or artifact_report.get("filename") != IMAGE_FILENAME
                or artifact_report.get("sha256") != ARTIFACT_SHA256
                or artifact_report.get("size_bytes") != ARTIFACT_SIZE_BYTES
                or artifact_report.get("signing_key_fingerprint") != SIGNING_KEY_FINGERPRINT
                or artifact_report.get("manifest_sha256") != CHECKSUM_MANIFEST_SHA256
            ):
                raise GuidedCreateError("pinned Ubuntu artifact verification did not match the reviewed identities")
            catalogue_report = desktop_catalogue.verify_desktop_source_id(inputs["iso"], settings["source_id"])
            if (
                not isinstance(catalogue_report, dict)
                or catalogue_report.get("verified_from_iso") is not True
                or catalogue_report.get("source_id") != settings["source_id"]
            ):
                raise GuidedCreateError("selected Ubuntu source ID did not match the pinned ISO catalogue")
            seed_path = private_root / "cidata.img"
            seed_args = [
                "--output", os.fspath(seed_path), "--vm-name", settings["vm_name"],
                "--realname", settings["realname"], "--username", settings["username"],
                "--source-id", settings["source_id"], "--locale", settings["locale"],
                "--keyboard-layout", settings["keyboard_layout"], "--timezone", settings["timezone"],
            ]
            if desktop_seed_cli.main(seed_args) != 0:
                raise GuidedCreateError("private Ubuntu seed generation failed")
            seed_size, seed_sha = _sha256_file(seed_path)
            if seed_size <= 0 or seed_size > SEED_MEDIA_SIZE_BYTES or stat.S_IMODE(seed_path.lstat().st_mode) != 0o600:
                raise GuidedCreateError("seed image is empty or not private mode 0600")
            record = {
                "schema_version": 1,
                "expected_hostname": settings["expected_hostname"],
                "expected_build": settings["expected_build"],
                "vm_name": settings["vm_name"], "pool_name": settings["pool_name"],
                "vcpus": settings["vcpus"], "memory_mib": settings["memory_mib"],
                "disk_gib": settings["disk_gib"], "host_reserve_mib": settings["host_reserve_mib"],
                "pool_reserve_mib": settings["pool_reserve_mib"], "source_id": settings["source_id"],
                "network": "none", "cache_on_selected_pool": False,
                "iso_path": os.fspath(inputs["iso"]),
                "checksum_manifest_path": os.fspath(inputs["manifest"]),
                "signature_path": os.fspath(inputs["signature"]),
                "keyring_path": os.fspath(inputs["keyring"]),
                "seed_path": os.fspath(seed_path), "seed_sha256": seed_sha, "seed_size_bytes": seed_size,
                "service_account": settings["service_account"],
                "reported_service_account_uid": settings["reported_service_account_uid"],
                "guest_identity": {
                    "realname": settings["realname"], "username": settings["username"],
                    "locale": settings["locale"], "keyboard_layout": settings["keyboard_layout"],
                    "timezone": settings["timezone"],
                },
                "manifest_path": os.fspath(manifest_path),
            }
            target_path = write_private_new_file(
                private_root / "target.json", json.dumps(record, sort_keys=True, indent=2) + "\n",
                allowed_root=private_root,
            )
            print("Temporary inputs verified. Next, sudo runs the create-only operator; it rechecks live state and asks for the exact plan-bound CREATE phrase. No approval is assumed.", flush=True)
            result = subprocess.run(_create_command(target_path), cwd=Path(__file__).resolve().parents[1], check=False)
            return result.returncode
    except (ArtifactError, DesktopAutoinstallError, DesktopCatalogueError, DesktopIntegrityError,
            DesktopWriterError, FilesystemSafetyError, GuidedCreateError, PreflightError,
            OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"guided Ubuntu Desktop create refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
