"""Experimental Ubuntu Server artifact and NoCloud bootstrap helpers.

This module is offline unless a caller explicitly invokes the pinned-image
wrapper or seed builder. It does not create or configure a TrueNAS VM.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

if __package__:
    from .installer_core.artifacts import ArtifactError, download_and_verify, ensure_private_cache_dir
    from .installer_core.safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain, write_private_new_file
else:
    from installer_core.artifacts import ArtifactError, download_and_verify, ensure_private_cache_dir
    from installer_core.safe_filesystem import FilesystemSafetyError, write_private_new_file

PROFILE_ID = "ubuntu-server"
PROFILE_STATUS = "experimental"
UBUNTU_RELEASE = "26.04 LTS"
UBUNTU_RELEASE_DIRECTORY = "release-20261016"
IMAGE_FILENAME = "ubuntu-26.04-server-cloudimg-amd64.img"
ARTIFACT_URL = (
    "https://cloud-images.ubuntu.com/releases/server/26.04/"
    f"{UBUNTU_RELEASE_DIRECTORY}/{IMAGE_FILENAME}"
)
ARTIFACT_SHA256 = "5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5"
ARTIFACT_SIZE_BYTES = 859_090_944
ARTIFACT_MAX_BYTES = ARTIFACT_SIZE_BYTES
CHECKSUM_MANIFEST_URL = (
    "https://cloud-images.ubuntu.com/releases/server/26.04/"
    f"{UBUNTU_RELEASE_DIRECTORY}/SHA256SUMS"
)
CHECKSUM_SIGNATURE_URL = CHECKSUM_MANIFEST_URL + ".gpg"
SIGNING_KEY_FINGERPRINT = "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"
IMAGE_FORMAT = "qcow2"
IMAGE_VIRTUAL_SIZE_BYTES = 3_758_096_384
IMAGE_FORMAT_STATUS = "verified with qemu-img info on the pinned SHA-256-verified artifact"

_USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_VM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_SAFE_OUTPUT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SSH_KEY_TYPES = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}


class ProfileValidationError(ValueError):
    """Invalid Ubuntu Server profile or bootstrap input."""


class SeedImageError(RuntimeError):
    """NoCloud seed media could not be generated safely."""


def artifact_metadata() -> dict[str, object]:
    """Return static, reviewable provenance; this function performs no I/O."""
    return {
        "profile_id": PROFILE_ID,
        "status": PROFILE_STATUS,
        "release": UBUNTU_RELEASE,
        "release_directory": UBUNTU_RELEASE_DIRECTORY,
        "architecture": "amd64",
        "filename": IMAGE_FILENAME,
        "url": ARTIFACT_URL,
        "sha256": ARTIFACT_SHA256,
        "size_bytes": ARTIFACT_SIZE_BYTES,
        "max_bytes": ARTIFACT_MAX_BYTES,
        "checksum_manifest_url": CHECKSUM_MANIFEST_URL,
        "checksum_signature_url": CHECKSUM_SIGNATURE_URL,
        "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
        "signature_evidence": (
            "Detached signature validated against the documented key fingerprint; "
            "the development verifier did not check key revocation/self-signatures."
        ),
        "image_format": IMAGE_FORMAT,
        "image_virtual_size_bytes": IMAGE_VIRTUAL_SIZE_BYTES,
        "image_format_status": IMAGE_FORMAT_STATUS,
    }


def download_ubuntu_server_image(cache_dir: Path) -> Path:
    """Download only the pinned image and verify its exact size and SHA-256."""
    return download_and_verify(
        url=ARTIFACT_URL,
        sha256=ARTIFACT_SHA256,
        cache_dir=Path(cache_dir),
        max_bytes=ARTIFACT_MAX_BYTES,
        expected_size=ARTIFACT_SIZE_BYTES,
    )


def stage_ubuntu_server_raw_image(
    cache_dir: Path,
    output_path: Path,
    *,
    qemu_img: str = "qemu-img",
    timeout: int = 300,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Verify the pinned cloud image, inspect it, and safely create a raw copy.

    The source is obtained only through the existing digest-keyed downloader.
    qemu-img is invoked without a shell and with bounded timeouts. The raw
    output is created exclusively; pre-existing destinations are never changed.
    This is local artifact preparation only, not TrueNAS/zvol import validation.
    """
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ProfileValidationError("image conversion timeout must be from 1 to 1800 seconds")
    if not isinstance(qemu_img, str) or not qemu_img or any(c in qemu_img for c in "\x00\n\r"):
        raise ProfileValidationError("qemu-img executable must be a non-empty command path")
    raw_output = Path(output_path).expanduser()
    if ".." in raw_output.parts or not _SAFE_OUTPUT_NAME.fullmatch(raw_output.name):
        raise ProfileValidationError("raw image output must have a safe filename and no parent traversal")
    try:
        cache = ensure_private_cache_dir(Path(cache_dir))
        # Validate the caller-selected destination without changing its mode.
        output_dir = ensure_trusted_directory_chain(raw_output.parent, "raw image output")
    except (ArtifactError, FilesystemSafetyError, OSError) as exc:
        raise ArtifactError("image staging directory is not a trusted private directory") from exc
    target = output_dir / raw_output.name
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ArtifactError("could not inspect raw image output") from exc
    else:
        raise ArtifactError("refusing to overwrite an existing raw image")

    source = download_ubuntu_server_image(cache)
    temporary: Path | None = None
    try:
        info_result = runner(
            [qemu_img, "info", "--output=json", os.fspath(source)],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
        info = json.loads(info_result.stdout)
        if (not isinstance(info, dict)
                or info.get("format") != IMAGE_FORMAT
                or info.get("virtual-size") != IMAGE_VIRTUAL_SIZE_BYTES
                or info.get("backing-filename") not in (None, "")
                or info.get("dirty-flag") is not False):
            raise ArtifactError("pinned image has unexpected QCOW2 metadata")
        fd, temp_name = tempfile.mkstemp(prefix=".ubuntu-raw-", suffix=".tmp", dir=output_dir)
        os.close(fd)
        temporary = Path(temp_name)
        os.chmod(temporary, 0o600)
        runner(
            [qemu_img, "convert", "-n", "-f", IMAGE_FORMAT, "-O", "raw", os.fspath(source), os.fspath(temporary)],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
        converted = runner(
            [qemu_img, "info", "--output=json", "-f", "raw", os.fspath(temporary)],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
        raw_info = json.loads(converted.stdout)
        if (not isinstance(raw_info, dict) or raw_info.get("format") != "raw"
                or raw_info.get("virtual-size") != IMAGE_VIRTUAL_SIZE_BYTES):
            raise ArtifactError("converted raw image has unexpected format or virtual size")
        # Hard-link publication is exclusive and cannot replace a racing destination.
        os.link(temporary, target, follow_symlinks=False)
        return target
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError, TypeError) as exc:
        raise ArtifactError("qemu-img inspection or conversion failed") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_vm_name(vm_name: str) -> str:
    if not isinstance(vm_name, str) or not _VM_NAME.fullmatch(vm_name):
        raise ProfileValidationError(
            "VM name must start with an ASCII letter and contain only letters, digits, _ or -"
        )
    return vm_name


def _validate_username(username: str) -> str:
    if (
        not isinstance(username, str)
        or not _USERNAME.fullmatch(username)
        or username in {"root", "ubuntu"}
    ):
        raise ProfileValidationError(
            "username must be a lower-case Linux account name and cannot be root or ubuntu"
        )
    return username


def _validate_public_key(key: str) -> str:
    if (
        not isinstance(key, str)
        or not key
        or len(key) > 8192
        or key != key.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise ProfileValidationError("authorized key must be one unpadded OpenSSH public-key line")
    parts = key.split(None, 2)
    if len(parts) < 2 or parts[0] not in _SSH_KEY_TYPES:
        raise ProfileValidationError("authorized key has an unsupported OpenSSH public-key type")
    key_type, encoded = parts[0], parts[1]
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProfileValidationError("authorized key has invalid base64 data") from exc
    type_bytes = key_type.encode("ascii")
    if len(blob) <= 4 + len(type_bytes):
        raise ProfileValidationError("authorized key is truncated")
    declared_type_length = struct.unpack(">I", blob[:4])[0]
    if declared_type_length != len(type_bytes) or blob[4 : 4 + declared_type_length] != type_bytes:
        raise ProfileValidationError("authorized key data does not match its declared key type")
    return key


def build_cloud_config(username: str, ssh_authorized_keys: Sequence[str]) -> str:
    """Build key-only cloud-config as JSON, which is valid YAML 1.2 syntax."""
    username = _validate_username(username)
    if not isinstance(ssh_authorized_keys, (list, tuple)) or not ssh_authorized_keys:
        raise ProfileValidationError("at least one SSH public key is required")
    if len(ssh_authorized_keys) > 16:
        raise ProfileValidationError("no more than 16 SSH public keys may be supplied")
    keys = [_validate_public_key(key) for key in ssh_authorized_keys]
    if len(keys) != len(set(keys)):
        raise ProfileValidationError("duplicate SSH public keys are not allowed")
    config = {
        "disable_root": True,
        "ssh_pwauth": False,
        "users": [
            "default",
            {
                "name": username,
                "groups": ["adm", "sudo"],
                "shell": "/bin/bash",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "lock_passwd": True,
                "ssh_authorized_keys": keys,
            },
        ],
    }
    return "#cloud-config\n" + json.dumps(config, indent=2, sort_keys=True) + "\n"


def build_meta_data(vm_name: str) -> str:
    """Build deterministic NoCloud meta-data without interpolation hazards."""
    vm_name = _validate_vm_name(vm_name)
    normalized = vm_name.lower().replace("_", "-").strip("-")
    instance_id = "truenas-" + hashlib.sha256(vm_name.casefold().encode("utf-8")).hexdigest()[:32]
    metadata = {"dsmode": "local", "instance-id": instance_id, "local-hostname": normalized}
    return json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n"


def build_seed_iso(
    output_path: Path,
    vm_name: str,
    username: str,
    ssh_authorized_keys: Sequence[str],
    *,
    executable: str = "cloud-localds",
    timeout: int = 60,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Create private NoCloud media; the external command is never shell-expanded.

    A successful result is a local seed image only. Callers must not treat it as
    a VM/device creation request or as proof of guest first-boot behavior.
    """
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 300:
        raise ProfileValidationError("seed-image timeout must be an integer from 1 to 300 seconds")
    if not isinstance(executable, str) or not executable or any(c in executable for c in "\x00\n\r"):
        raise ProfileValidationError("cloud-localds executable must be a non-empty command path")
    raw_output = Path(output_path).expanduser()
    if ".." in raw_output.parts or not _SAFE_OUTPUT_NAME.fullmatch(raw_output.name):
        raise ProfileValidationError("seed output must have a safe filename and no parent traversal")
    user_data = build_cloud_config(username, ssh_authorized_keys)
    meta_data = build_meta_data(vm_name)
    try:
        cache_dir = ensure_private_cache_dir(raw_output.parent)
    except (ArtifactError, FilesystemSafetyError, OSError) as exc:
        raise SeedImageError("seed output directory is not a trusted private directory") from exc
    target = cache_dir / raw_output.name
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SeedImageError("could not inspect seed output path") from exc
    else:
        raise SeedImageError("refusing to overwrite an existing seed output")

    published = False
    try:
        with tempfile.TemporaryDirectory(prefix=".nocloud-", dir=cache_dir) as temporary:
            stage = Path(temporary)
            os.chmod(stage, 0o700)
            user_path = write_private_new_file(stage / "user-data", user_data)
            metadata_path = write_private_new_file(stage / "meta-data", meta_data)
            seed_path = stage / "seed.iso"
            environment = os.environ.copy()
            environment["TMPDIR"] = os.fspath(stage)
            arguments = [
                executable,
                "--disk-format",
                "raw",
                "--filesystem",
                "iso",
                os.fspath(seed_path),
                os.fspath(user_path),
                os.fspath(metadata_path),
            ]
            try:
                result = runner(
                    arguments,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=environment,
                )
            except FileNotFoundError as exc:
                raise SeedImageError(
                    "cloud-localds or a required ISO creation dependency is unavailable"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise SeedImageError("cloud-localds timed out while building seed media") from exc
            except subprocess.CalledProcessError as exc:
                raise SeedImageError(
                    "cloud-localds failed; verify cloud-utils and its ISO creation dependency"
                ) from exc
            if getattr(result, "returncode", 0) != 0:
                raise SeedImageError("cloud-localds returned a nonzero status")
            try:
                output_info = seed_path.lstat()
            except OSError as exc:
                raise SeedImageError("cloud-localds did not produce seed media") from exc
            if not stat.S_ISREG(output_info.st_mode) or output_info.st_size <= 0:
                raise SeedImageError("cloud-localds output is not a nonempty regular file")
            os.chmod(seed_path, 0o600, follow_symlinks=False)
            try:
                os.link(seed_path, target, follow_symlinks=False)
            except FileExistsError as exc:
                raise SeedImageError("refusing to overwrite an existing seed output") from exc
            published = True
            directory_fd = os.open(cache_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except SeedImageError:
        if published:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except (FilesystemSafetyError, ArtifactError, OSError) as exc:
        if published:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise SeedImageError("could not create seed media safely") from exc
    return target
