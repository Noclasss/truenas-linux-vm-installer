"""Local Desktop artifact verification; no downloads or TrueNAS operations.

This helper verifies caller-supplied files. It never enables a live profile.
Use a trusted workstation with gpgv already installed, not package installation
on TrueNAS. No GnuPG user configuration or default keyring is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

if __package__:
    from . import ubuntu_desktop as desktop
else:
    import ubuntu_desktop as desktop


class DesktopIntegrityError(RuntimeError):
    """Signature, manifest, or local image verification failed."""


def _open_regular(path: Path):
    # O_NONBLOCK prevents a supplied FIFO from blocking before fstat.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DesktopIntegrityError("input must be a regular non-symlink file")
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    return stream


def _read_small(path: Path, limit: int) -> bytes:
    with _open_regular(path) as stream:
        value = stream.read(limit + 1)
    if not value or len(value) > limit:
        raise DesktopIntegrityError("verification input is empty or exceeds its size limit")
    return value


def _check_status(status: str, expected_fingerprint: str) -> None:
    """Require a machine-readable VALIDSIG from the pinned primary key."""
    if not isinstance(status, str):
        raise DesktopIntegrityError("signature verifier returned no status")
    expected = expected_fingerprint.upper()
    valid = False
    rejected = {
        "BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG",
        "NO_PUBKEY", "NODATA", "FAILURE", "ERROR", "KEYEXPIRED", "SIGEXPIRED",
    }
    for line in status.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != "[GNUPG:]":
            continue
        if fields[1] in rejected:
            raise DesktopIntegrityError("signature verifier reported invalid or expired evidence")
        if fields[1] != "VALIDSIG":
            continue
        # VALIDSIG: fingerprint date timestamp expiry version reserved
        # public-key-algorithm hash-algorithm signature-class [primary-fpr].
        if len(fields) not in (11, 12):
            raise DesktopIntegrityError("malformed signature status")
        signer = fields[2].upper()
        primary = fields[11].upper() if len(fields) == 12 else signer
        if not re.fullmatch(r"[0-9A-F]{40}", signer) or primary != expected:
            raise DesktopIntegrityError("checksum manifest was not signed by the pinned Ubuntu key")
        if fields[9] not in {"8", "9", "10", "11"} or fields[10] != "00":
            raise DesktopIntegrityError("checksum signature must use SHA-2 and binary document class")
        valid = True
    if not valid:
        raise DesktopIntegrityError("missing valid signature from the pinned Ubuntu key")


def _manifest_digest(manifest: bytes, filename: str, pinned_digest: str) -> str:
    try:
        lines = manifest.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise DesktopIntegrityError("checksum manifest must be ASCII") from exc
    matches = []
    for line in lines:
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            raise DesktopIntegrityError("malformed SHA256SUMS entry")
        if match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise DesktopIntegrityError("manifest must contain exactly one exact Desktop filename")
    if matches[0] != pinned_digest:
        raise DesktopIntegrityError("signed checksum disagrees with the checked-in Desktop pin")
    return matches[0]


def _verify_signature(manifest: bytes, signature: bytes, keyring: bytes,
                      expected_fingerprint: str) -> None:
    # Snapshot inputs so gpgv and the parser examine the same manifest bytes.
    # A private homedir and explicit keyring avoid ambient trust/configuration.
    with tempfile.TemporaryDirectory(prefix="desktop-verify-") as directory:
        root = Path(directory)
        for name, data in (("SHA256SUMS", manifest), ("SHA256SUMS.gpg", signature),
                           ("trustedkeys.gpg", keyring)):
            with (root / name).open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
        try:
            result = subprocess.run(
                ["gpgv", "--homedir", str(root), "--keyring",
                 str(root / "trustedkeys.gpg"), "--status-fd", "1",
                 str(root / "SHA256SUMS.gpg"), str(root / "SHA256SUMS")],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                check=False, timeout=30,
                env={"PATH": os.defpath, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise DesktopIntegrityError("gpgv unavailable, failed, or timed out") from exc
        if result.returncode != 0:
            raise DesktopIntegrityError("gpgv rejected the checksum signature")
        _check_status(result.stdout, expected_fingerprint)


def _hash_image(path: Path, expected_size: int, expected_digest: str) -> None:
    with _open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size != expected_size:
            raise DesktopIntegrityError("ISO size disagrees with the checked-in Desktop pin")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise DesktopIntegrityError("ISO grew while being verified")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or total != expected_size:
            raise DesktopIntegrityError("ISO changed while being verified")
        if digest.hexdigest() != expected_digest:
            raise DesktopIntegrityError("ISO SHA-256 disagrees with the signed Desktop pin")


def verify_staged_iso(path: Path) -> None:
    """Re-hash a staged ISO copy against the pinned size and SHA-256 before attach.

    shortcut: no live writer calls this yet; the offline fake cannot read host files, so wire it in before the first CDROM attach.
    """
    try:
        _hash_image(Path(path), desktop.ARTIFACT_SIZE_BYTES, desktop.ARTIFACT_SHA256)
    except OSError as exc:
        raise DesktopIntegrityError("could not safely read staged ISO") from exc


def verify_desktop_artifacts(iso: Path, manifest: Path, signature: Path,
                             keyring: Path) -> dict[str, object]:
    """Verify local bytes against fixed Desktop pins; no caller pin override.

    Results describe bytes read during this invocation, not an authorization
    token. A future writer must verify its actual staged bytes again.
    """
    try:
        manifest_bytes = _read_small(Path(manifest), 1024 * 1024)
        signature_bytes = _read_small(Path(signature), 64 * 1024)
        keyring_bytes = _read_small(Path(keyring), 5 * 1024 * 1024)
        _verify_signature(manifest_bytes, signature_bytes, keyring_bytes,
                          desktop.SIGNING_KEY_FINGERPRINT)
        digest = _manifest_digest(manifest_bytes, desktop.IMAGE_FILENAME,
                                  desktop.ARTIFACT_SHA256)
        _hash_image(Path(iso), desktop.ARTIFACT_SIZE_BYTES, digest)
    except DesktopIntegrityError:
        raise
    except (OSError, ValueError) as exc:
        raise DesktopIntegrityError("could not safely read local verification inputs") from exc
    return {
        "profile": desktop.PROFILE_ID,
        "filename": desktop.IMAGE_FILENAME,
        "sha256": digest,
        "size_bytes": desktop.ARTIFACT_SIZE_BYTES,
        "signing_key_fingerprint": desktop.SIGNING_KEY_FINGERPRINT,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature_verified": True,
        "iso_bytes_verified": True,
        "source_id_verified": False,
        "live_install_allowed": False,
        "scope": "local bytes only; not ISO source, provisioning or VM boot evidence",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--signature", required=True, type=Path)
    parser.add_argument("--keyring", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_desktop_artifacts(args.iso, args.manifest, args.signature, args.keyring)
    except DesktopIntegrityError as exc:
        parser.exit(1, f"Desktop verification refused: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
