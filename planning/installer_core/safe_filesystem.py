"""Private local paths and manifest-safe file primitives adapted from HAOS core."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Union


class FilesystemSafetyError(RuntimeError):
    """Unsafe path or local file operation."""


TextOrBytes = Union[str, bytes]


def ensure_trusted_directory_chain(
    directory: Path,
    purpose: str = "data",
    create: bool = False,
    allowed_root: Path | None = None,
) -> Path:
    """Require real, trusted directory ancestors; optionally create them privately."""
    directory = Path(os.path.abspath(os.fspath(Path(directory).expanduser())))
    if allowed_root is not None:
        root = Path(os.path.abspath(os.fspath(Path(allowed_root).expanduser())))
        try:
            directory.relative_to(root)
        except ValueError as exc:
            raise FilesystemSafetyError(f"{purpose} path must remain under {root}") from exc
    owners = {0, os.geteuid()}
    chain = [*reversed(directory.parents), directory]
    for current in chain:
        try:
            if create:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            info = current.lstat()
        except OSError as exc:
            raise FilesystemSafetyError(f"cannot inspect {purpose} directory chain") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise FilesystemSafetyError(f"{purpose} path contains a symlink or non-directory")
        if info.st_uid not in owners:
            raise FilesystemSafetyError(f"{purpose} path contains a directory not owned by root or the current user")
        if info.st_mode & 0o022 and not (info.st_mode & stat.S_ISVTX and info.st_uid == 0):
            raise FilesystemSafetyError(f"{purpose} path contains an unsafe group/world-writable directory")
    return directory


def _safe_target_parent(path: Path, allowed_root: Path | None = None) -> Path:
    path = Path(path).expanduser()
    parent = ensure_trusted_directory_chain(path.parent, "private file", allowed_root=allowed_root)
    return parent / path.name


def write_private_new_file(path: Path, content: TextOrBytes, allowed_root: Path | None = None) -> Path:
    """Create a new mode-0600 file exclusively; never overwrite or follow symlinks."""
    path = _safe_target_parent(path, allowed_root)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise FilesystemSafetyError(f"refusing to overwrite existing file: {path.name}") from exc
    except OSError as exc:
        raise FilesystemSafetyError("could not create private file") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            data = content.encode("utf-8") if isinstance(content, str) else content
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise FilesystemSafetyError("could not finish writing private file") from exc
    return path


def atomic_replace_private_file(path: Path, content: TextOrBytes) -> Path:
    """Atomically replace a trusted regular file with a private, synced version."""
    path = _safe_target_parent(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise FilesystemSafetyError("could not inspect existing private file") from exc
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise FilesystemSafetyError("refusing to replace an unsafe or untrusted file")
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        os.fchmod(fd, 0o600)
        data = content.encode("utf-8") if isinstance(content, str) else content
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise FilesystemSafetyError("could not atomically update private file") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return path
