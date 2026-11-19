"""Offline Ubuntu Desktop autoinstall payload and credential helpers.

This module creates no files and performs no TrueNAS operations. The candidate
source IDs were confirmed in the verified 26.04.1 ISO catalogue; this does not
validate autoinstall behavior or enable live installation.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__:
    from .nocloud_vfat import write_nocloud_vfat_image
else:
    from nocloud_vfat import write_nocloud_vfat_image

SOURCE_ID_CANDIDATES = frozenset({"ubuntu-desktop-minimal", "ubuntu-desktop"})
SOURCE_ID_EVIDENCE = (
    "casper/install-sources.yaml from Ubuntu 26.04.1 Desktop ISO "
    "SHA-256 601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f "
    "(verified 2026-11-19): ubuntu-desktop-minimal is default in the ISO catalogue; "
    "ubuntu-desktop is also present"
)

_VM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_LOCALE = re.compile(r"[A-Za-z0-9_.@-]{2,32}\Z")
_KEYBOARD_LAYOUT = re.compile(r"[A-Za-z0-9_-]{1,16}\Z")
_SHA512_CRYPT = re.compile(r"\$6\$(?:rounds=[0-9]+\$)?[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}\Z")


class DesktopAutoinstallError(ValueError):
    """Invalid Ubuntu Desktop autoinstall input or password hash operation."""


def _validate_plain_text(value: object, label: str, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DesktopAutoinstallError(f"{label} must be non-empty printable text under {max_length + 1} characters")
    return value


def validate_sha512_crypt_hash(password_hash: object) -> str:
    """Accept only a SHA-512 crypt string suitable for Subiquity identity.password."""
    if not isinstance(password_hash, str) or not _SHA512_CRYPT.fullmatch(password_hash):
        raise DesktopAutoinstallError("password hash must be a complete SHA-512 crypt value")
    return password_hash


def hash_desktop_password(
    password: str,
    *,
    openssl: str = "openssl",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """Hash a local prompt value with OpenSSL without putting it in argv or logs."""
    if (
        not isinstance(password, str)
        or not 12 <= len(password) <= 128
        or any(ord(character) < 32 or ord(character) == 127 for character in password)
    ):
        raise DesktopAutoinstallError("password must contain 12 to 128 printable characters")
    if not isinstance(openssl, str) or not openssl or any(character in openssl for character in "\x00\n\r"):
        raise DesktopAutoinstallError("OpenSSL command path is invalid")
    try:
        result = runner(
            [openssl, "passwd", "-6", "-stdin"],
            input=password + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopAutoinstallError("could not create a password hash with OpenSSL") from exc
    if getattr(result, "returncode", 0) != 0:
        raise DesktopAutoinstallError("could not create a password hash with OpenSSL")
    output = getattr(result, "stdout", None)
    if not isinstance(output, str):
        raise DesktopAutoinstallError("OpenSSL did not return a valid password hash")
    return validate_sha512_crypt_hash(output.strip())


def _desktop_hostname(vm_name: str) -> str:
    """Derive an RFC 1123 hostname for every VM name accepted by preview.

    Preserve ordinary short names. Add a stable suffix when sanitizing
    underscores/trailing hyphens or truncating, avoiding avoidable collisions.
    """
    normalized = vm_name.lower()
    if re.fullmatch(r"[a-z][a-z0-9-]{0,55}", normalized) and normalized[-1].isalnum():
        return f"ubuntu-{normalized}"
    slug = normalized.replace("_", "-").rstrip("-")
    suffix = hashlib.sha256(normalized.encode("ascii")).hexdigest()[:8]
    return f"ubuntu-{slug[:47].rstrip('-')}-{suffix}"


def build_desktop_autoinstall_user_data(
    *,
    vm_name: str,
    realname: str,
    username: str,
    password_hash: str,
    source_id: str,
    locale: str = "en_US.UTF-8",
    keyboard_layout: str = "us",
    timezone: str = "Etc/UTC",
) -> str:
    """Render private cloud-config user-data for Subiquity Desktop autoinstall."""
    if not isinstance(vm_name, str) or not _VM_NAME.fullmatch(vm_name):
        raise DesktopAutoinstallError("VM name must use 1–63 ASCII letters, digits, hyphens or underscores and start with a letter")
    realname = _validate_plain_text(realname, "display name", 80)
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise DesktopAutoinstallError("Linux username must use lowercase letters, digits, underscores, or hyphens")
    if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id) or source_id not in SOURCE_ID_CANDIDATES:
        raise DesktopAutoinstallError("source ID must match a currently documented Ubuntu Desktop candidate")
    if not isinstance(locale, str) or not _LOCALE.fullmatch(locale):
        raise DesktopAutoinstallError("locale value is invalid")
    if not isinstance(keyboard_layout, str) or not _KEYBOARD_LAYOUT.fullmatch(keyboard_layout):
        raise DesktopAutoinstallError("keyboard layout value is invalid")
    if not isinstance(timezone, str) or not timezone or any(character in timezone for character in "\x00\n\r"):
        raise DesktopAutoinstallError("timezone value is invalid")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DesktopAutoinstallError("timezone must name a known IANA time zone") from exc

    password_hash = validate_sha512_crypt_hash(password_hash)
    hostname = _desktop_hostname(vm_name)
    autoinstall = {
        "version": 1,
        "interactive-sections": [],
        "source": {"id": source_id, "search_drivers": False},
        "locale": locale,
        "keyboard": {"layout": keyboard_layout},
        "timezone": timezone,
        "storage": {"layout": {"name": "direct"}},
        "identity": {
            "realname": realname,
            "username": username,
            "hostname": hostname,
            "password": password_hash,
        },
        "ssh": {"install-server": False, "authorized-keys": [], "allow-pw": False},
        "shutdown": "poweroff",
    }
    return "#cloud-config\n" + json.dumps({"autoinstall": autoinstall}, ensure_ascii=True, indent=2) + "\n"


def build_desktop_meta_data(vm_name: str) -> str:
    """Build deterministic local NoCloud metadata for a validated desktop VM name."""
    if not isinstance(vm_name, str) or not _VM_NAME.fullmatch(vm_name):
        raise DesktopAutoinstallError("VM name must use 1–63 ASCII letters, digits, hyphens or underscores and start with a letter")
    normalized = vm_name.lower()
    instance_id = "truenas-ubuntu-desktop-" + hashlib.sha256(vm_name.casefold().encode("ascii")).hexdigest()[:32]
    return json.dumps(
        {"dsmode": "local", "instance-id": instance_id, "local-hostname": _desktop_hostname(vm_name)},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def build_desktop_seed_image(
    output_path: Path,
    *,
    vm_name: str,
    realname: str,
    username: str,
    password_hash: str,
    source_id: str,
    locale: str = "en_US.UTF-8",
    keyboard_layout: str = "us",
    timezone: str = "Etc/UTC",
) -> Path:
    """Create a private mode-0600 raw VFAT NoCloud disk image for Desktop install."""
    user_data = build_desktop_autoinstall_user_data(
        vm_name=vm_name,
        realname=realname,
        username=username,
        password_hash=password_hash,
        source_id=source_id,
        locale=locale,
        keyboard_layout=keyboard_layout,
        timezone=timezone,
    )
    meta_data = build_desktop_meta_data(vm_name)
    return write_nocloud_vfat_image(output_path, user_data, meta_data)
