"""Read-only extraction and checking of the Ubuntu Desktop ISO source catalogue."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Callable

from .desktop_autoinstall import SOURCE_ID_CANDIDATES

_ID_LINE = re.compile(r"^\s*(?:-\s*)?id:\s*['\"]?([a-z0-9][a-z0-9-]{0,63})['\"]?\s*(?:#.*)?$")
_MAX_CATALOGUE_BYTES = 1024 * 1024


class DesktopCatalogueError(RuntimeError):
    """The selected source ID could not be proven from this exact ISO."""


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise DesktopCatalogueError("ISO input must be a regular non-symlink file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def verify_desktop_source_id(
    iso_path: Path,
    source_id: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    xorriso: str = "xorriso",
) -> dict[str, Any]:
    """Extract casper/install-sources.yaml from the supplied ISO and match an ID.

    Extraction is read-only with respect to the ISO and writes only into a
    private temporary directory. No shell is used and xorriso output is not
    surfaced, avoiding accidental disclosure of host-specific paths.
    """
    iso_path = Path(iso_path)
    if source_id not in SOURCE_ID_CANDIDATES:
        raise DesktopCatalogueError("source ID is not an allowed Ubuntu Desktop candidate")
    try:
        before = _identity(iso_path)
    except OSError as exc:
        raise DesktopCatalogueError("could not safely inspect the Desktop ISO") from exc
    with tempfile.TemporaryDirectory(prefix="desktop-catalogue-") as directory:
        root = Path(directory)
        catalogue = root / "install-sources.yaml"
        try:
            result = runner(
                [xorriso, "-osirrox", "on", "-indev", os.fspath(iso_path),
                 "-extract", "/casper/install-sources.yaml", os.fspath(catalogue)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=60, check=False,
                env={"PATH": os.defpath, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DesktopCatalogueError("xorriso is unavailable or could not extract the ISO catalogue") from exc
        if getattr(result, "returncode", 1) != 0:
            raise DesktopCatalogueError("xorriso failed to extract casper/install-sources.yaml")
        try:
            info = catalogue.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > _MAX_CATALOGUE_BYTES:
                raise DesktopCatalogueError("extracted ISO catalogue is not a bounded regular file")
            content = catalogue.read_bytes()
            after = _identity(iso_path)
        except DesktopCatalogueError:
            raise
        except OSError as exc:
            raise DesktopCatalogueError("extracted ISO catalogue could not be read safely") from exc
    if before != after:
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
