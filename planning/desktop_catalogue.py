"""Read-only extraction and checking of the Ubuntu Desktop ISO source catalogue."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Callable

from .desktop_autoinstall import SOURCE_ID_CANDIDATES

_ID_LINE = re.compile(r"^\s*(?:-\s*)?id:\s*['\"]?([a-z0-9][a-z0-9-]{0,63})['\"]?\s*(?:#.*)?$")
_MAX_CATALOGUE_BYTES = 1024 * 1024


class DesktopCatalogueError(RuntimeError):
    """The selected source ID could not be proven from this exact ISO."""


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    if not stat.S_ISREG(info.st_mode):
        raise DesktopCatalogueError("ISO input must be a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_iso(path: Path) -> tuple[int, tuple[int, int, int, int, int]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise DesktopCatalogueError("could not safely open the Desktop ISO") from exc
    try:
        identity = _identity(os.fstat(fd))
        if identity != _identity(path.lstat()):
            raise DesktopCatalogueError("ISO path changed while it was opened")
        return fd, identity
    except BaseException:
        os.close(fd)
        raise


def _stop_process(process: Any) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.SubprocessError:
            pass


def _extract_7z_catalogue(extractor: str, iso_fd_path: str, iso_fd: int) -> bytes:
    argv = [extractor, "e", "-so", "-spd", "-bd", iso_fd_path,
            "casper/install-sources.yaml"]
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True, pass_fds=(iso_fd,), bufsize=0,
            env={"PATH": os.defpath, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopCatalogueError("7z could not start for ISO catalogue extraction") from exc
    if process.stdout is None:
        _stop_process(process)
        raise DesktopCatalogueError("7z produced no catalogue stream")

    deadline = time.monotonic() + 60
    content = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError
            chunk = os.read(process.stdout.fileno(), min(64 * 1024, _MAX_CATALOGUE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > _MAX_CATALOGUE_BYTES:
                raise DesktopCatalogueError("extracted ISO catalogue exceeds the byte limit")
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except DesktopCatalogueError:
        _stop_process(process)
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        _stop_process(process)
        raise DesktopCatalogueError("7z failed, timed out, or could not read the ISO catalogue") from exc
    finally:
        try:
            process.stdout.close()
        except OSError:
            pass
    if returncode != 0:
        raise DesktopCatalogueError("7z failed to extract casper/install-sources.yaml")
    if not content:
        raise DesktopCatalogueError("7z returned an empty ISO catalogue")
    return bytes(content)


def verify_desktop_source_id(
    iso_path: Path,
    source_id: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    xorriso: str | None = None,
) -> dict[str, Any]:
    """Extract casper/install-sources.yaml from the supplied ISO and match an ID.

    Prefer xorriso; use 7z's stdout mode when xorriso is not installed. Neither
    extractor writes to the ISO, and no shell output or host paths are surfaced.
    """
    iso_path = Path(iso_path)
    if source_id not in SOURCE_ID_CANDIDATES:
        raise DesktopCatalogueError("source ID is not an allowed Ubuntu Desktop candidate")
    try:
        iso_fd, before = _open_iso(iso_path)
    except DesktopCatalogueError:
        raise
    except OSError as exc:
        raise DesktopCatalogueError("could not safely inspect the Desktop ISO") from exc

    try:
        if not Path("/proc/self/fd").is_dir():
            raise DesktopCatalogueError("descriptor-safe catalogue extraction requires /proc/self/fd")
        iso_fd_path = f"/proc/self/fd/{iso_fd}"
        extractor = xorriso if xorriso is not None else shutil.which("xorriso", path=os.defpath)
        if extractor is not None and (not isinstance(extractor, str) or not extractor):
            raise DesktopCatalogueError("ISO catalogue extractor path is invalid")
        if extractor is not None:
            with tempfile.TemporaryDirectory(prefix="desktop-catalogue-") as directory:
                catalogue = Path(directory) / "install-sources.yaml"
                try:
                    result = runner(
                        [extractor, "-osirrox", "on", "-indev", iso_fd_path,
                         "-extract", "/casper/install-sources.yaml", os.fspath(catalogue)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=60, check=False,
                        env={"PATH": os.defpath, "LC_ALL": "C"}, pass_fds=(iso_fd,),
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise DesktopCatalogueError("xorriso could not extract the ISO catalogue") from exc
                if getattr(result, "returncode", 1) != 0:
                    raise DesktopCatalogueError("xorriso failed to extract casper/install-sources.yaml")
                try:
                    info = catalogue.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > _MAX_CATALOGUE_BYTES:
                        raise DesktopCatalogueError("extracted ISO catalogue is not a bounded regular file")
                    content = catalogue.read_bytes()
                except DesktopCatalogueError:
                    raise
                except OSError as exc:
                    raise DesktopCatalogueError("extracted ISO catalogue could not be read safely") from exc
        else:
            extractor = shutil.which("7z", path=os.defpath)
            if extractor is None:
                raise DesktopCatalogueError("no supported ISO catalogue extractor is available")
            content = _extract_7z_catalogue(extractor, iso_fd_path, iso_fd)

        try:
            after_fd = _identity(os.fstat(iso_fd))
            after_path = _identity(iso_path.lstat())
        except OSError as exc:
            raise DesktopCatalogueError("could not recheck the Desktop ISO after catalogue inspection") from exc
        if before != after_fd or before != after_path:
            raise DesktopCatalogueError("ISO changed during source catalogue inspection")
        try:
            text = content.decode("utf-8")
        except UnicodeError as exc:
            raise DesktopCatalogueError("ISO source catalogue is not UTF-8") from exc
        ids = []
        for line in text.splitlines():
            match = _ID_LINE.fullmatch(line)
            if match and match.group(1) in SOURCE_ID_CANDIDATES:
                ids.append(match.group(1))
        if not ids or len(ids) != len(set(ids)):
            raise DesktopCatalogueError("ISO catalogue has missing or duplicate supported source IDs")
        if source_id not in ids:
            raise DesktopCatalogueError("selected source ID is absent from the exact ISO catalogue")
        return {
            "source_id": source_id,
            "source_ids": sorted(ids),
            "catalogue_sha256": hashlib.sha256(content).hexdigest(),
            "verified_from_iso": True,
            "iso_unchanged_during_extraction": True,
        }
    finally:
        os.close(iso_fd)
