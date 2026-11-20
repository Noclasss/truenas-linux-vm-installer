"""Build private Ubuntu Desktop NoCloud media from hidden local prompts.

Passwords are never accepted as arguments and never printed. The SHA-512 crypt
hash is embedded in the seed image, which must be handled as credential-bearing.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from .desktop_autoinstall import (
    DesktopAutoinstallError,
    build_desktop_autoinstall_user_data,
    build_desktop_meta_data,
    build_desktop_seed_image,
    hash_desktop_password,
)
from .nocloud_vfat import build_nocloud_vfat_image


def _digest_regular_file(path: Path) -> tuple[int, str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("seed output is not a regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        if identity(before) != identity(after) or total != before.st_size:
            raise ValueError("seed output changed while being verified")
        return total, digest.hexdigest(), stat.S_IMODE(after.st_mode)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--realname", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--source-id", required=True, choices=("ubuntu-desktop-minimal", "ubuntu-desktop"))
    parser.add_argument("--locale", default="en_US.UTF-8")
    parser.add_argument("--keyboard-layout", default="us")
    parser.add_argument("--timezone", default="Etc/UTC")
    args = parser.parse_args(argv)
    try:
        password = getpass.getpass("Ubuntu guest account password (hidden): ")
        confirmation = getpass.getpass("Repeat Ubuntu guest account password (hidden): ")
        if password != confirmation:
            raise DesktopAutoinstallError("password entries do not match")
        password_hash = hash_desktop_password(password)
        user_data = build_desktop_autoinstall_user_data(
            vm_name=args.vm_name, realname=args.realname, username=args.username,
            password_hash=password_hash, source_id=args.source_id,
            locale=args.locale, keyboard_layout=args.keyboard_layout, timezone=args.timezone,
        )
        meta_data = build_desktop_meta_data(args.vm_name)
        expected_image = build_nocloud_vfat_image(user_data, meta_data)
        output = build_desktop_seed_image(
            args.output, vm_name=args.vm_name, realname=args.realname, username=args.username,
            password_hash=password_hash, source_id=args.source_id,
            locale=args.locale, keyboard_layout=args.keyboard_layout, timezone=args.timezone,
        )
        size, digest, mode = _digest_regular_file(output)
        if size != len(expected_image) or mode != 0o600 or digest != hashlib.sha256(expected_image).hexdigest():
            raise DesktopAutoinstallError("written seed differs from the verified local builder output")
        print(json.dumps({
            "status": "built_and_verified", "path": os.fspath(output),
            "size_bytes": size, "sha256": digest, "mode": "0600",
            "seed_media_contains_credential_hash": True,
            "plaintext_or_hash_printed": False,
        }, indent=2, sort_keys=True))
        return 0
    except (DesktopAutoinstallError, OSError, ValueError) as exc:
        print(f"desktop seed build refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
