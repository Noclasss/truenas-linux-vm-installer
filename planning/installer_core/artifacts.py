"""Generic HTTPS artifact download, size and SHA-256 verification."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain


class ArtifactError(RuntimeError):
    """Artifact URL, cache, size, or digest validation failed."""


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme.lower() != "https":
            raise ArtifactError("refusing an artifact redirect that leaves HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https_url(request: urllib.request.Request | str, timeout: int = 120):
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(char.isspace() for char in url)
    ):
        raise ArtifactError("artifact URL must be valid HTTPS without embedded credentials or fragments")
    try:
        return urllib.request.build_opener(HTTPSOnlyRedirectHandler()).open(request, timeout=timeout)
    except ArtifactError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise ArtifactError("could not retrieve artifact over HTTPS") from exc


def ensure_private_cache_dir(cache_dir: Path) -> Path:
    cache_dir = Path(os.path.abspath(os.fspath(Path(cache_dir).expanduser())))
    try:
        ensure_trusted_directory_chain(cache_dir, "artifact cache", create=True)
        info = cache_dir.lstat()
    except FilesystemSafetyError as exc:
        raise ArtifactError(str(exc)) from exc
    except OSError as exc:
        raise ArtifactError("could not create a private artifact cache") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ArtifactError("artifact cache must be a directory owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ArtifactError(
            "artifact cache must already have mode 0700; refusing to change existing directory permissions"
        )
    return cache_dir


def download_and_verify(
    url: str,
    sha256: str,
    cache_dir: Path,
    max_bytes: int,
    expected_size: int | None = None,
    timeout: int = 120,
) -> Path:
    """Download an artifact to a private digest-keyed cache and verify it.

    Release-specific metadata discovery and artifact-format decisions belong to
    distro profiles; this function accepts an already-selected HTTPS URL and
    trusted expected digest from that profile's verified metadata source.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ArtifactError("artifact URL must be valid HTTPS without embedded credentials or fragments")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ArtifactError("a valid 64-character SHA-256 digest is required")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ArtifactError("maximum download size must be a positive integer")
    if expected_size is not None and (type(expected_size) is not int or not 0 < expected_size <= max_bytes):
        raise ArtifactError("expected artifact size must be positive and within the configured limit")
    cache_dir = ensure_private_cache_dir(cache_dir)
    digest = sha256.lower()
    destination = cache_dir / f"sha256-{digest}.artifact"
    try:
        info = destination.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise ArtifactError("could not inspect cached artifact") from exc
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ArtifactError("refusing an unsafe or unowned cache entry")
        if info.st_size <= max_bytes and (expected_size is None or info.st_size == expected_size):
            cached_hash = hashlib.sha256()
            with destination.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    cached_hash.update(chunk)
            if cached_hash.hexdigest() == digest:
                return destination
        destination.unlink()

    # shortcut: unknown sizes use the full limit; add an exact pin to avoid over-reserving.
    required_bytes = expected_size if expected_size is not None else max_bytes
    try:
        space = os.statvfs(cache_dir)
        free_bytes = space.f_bavail * space.f_frsize
    except OSError as exc:
        raise ArtifactError("could not check free space on the artifact cache filesystem") from exc
    if free_bytes < required_bytes:
        raise ArtifactError("artifact cache filesystem has insufficient free space for the download")

    request = urllib.request.Request(url, headers={"User-Agent": "TrueNAS-Linux-VM-Installer"})
    partial: Path | None = None
    digest_state = hashlib.sha256()
    total = 0
    try:
        with open_https_url(request, timeout=timeout) as response:
            if urllib.parse.urlparse(response.geturl()).scheme.lower() != "https":
                raise ArtifactError("artifact download did not remain on HTTPS")
            header_size = response.headers.get("Content-Length")
            if header_size:
                try:
                    declared = int(header_size)
                except (TypeError, ValueError) as exc:
                    raise ArtifactError("artifact server returned an invalid Content-Length") from exc
                if declared < 0 or declared > max_bytes:
                    raise ArtifactError("artifact exceeds the configured download limit")
                if expected_size is not None and declared != expected_size:
                    raise ArtifactError("artifact Content-Length does not match expected size")
            fd, temp_name = tempfile.mkstemp(prefix="download-", suffix=".part", dir=cache_dir)
            partial = Path(temp_name)
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ArtifactError("artifact exceeded the configured download limit")
                    digest_state.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if expected_size is not None and total != expected_size:
            raise ArtifactError("downloaded artifact size does not match expected size")
        if digest_state.hexdigest() != digest:
            raise ArtifactError("downloaded artifact SHA-256 does not match expected digest")
        os.replace(partial, destination)
        partial = None
        return destination
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError("artifact download or cache-file operation failed") from exc
    finally:
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
