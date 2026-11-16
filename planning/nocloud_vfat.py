"""Dependency-free private NoCloud VFAT seed-media builder."""
from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path

try:
    from .installer_core.safe_filesystem import FilesystemSafetyError, write_private_new_file
except ImportError:  # direct execution from the planning directory
    from installer_core.safe_filesystem import FilesystemSafetyError, write_private_new_file

SEED_MEDIA_SIZE_BYTES = 2 * 1024 * 1024
_BYTES_PER_SECTOR = 512
_TOTAL_SECTORS = SEED_MEDIA_SIZE_BYTES // _BYTES_PER_SECTOR
_SECTORS_PER_CLUSTER = 1
_RESERVED_SECTORS = 1
_FAT_COUNT = 2
_ROOT_ENTRIES = 128
_SECTORS_PER_FAT = 12
_ROOT_SECTORS = (_ROOT_ENTRIES * 32) // _BYTES_PER_SECTOR
_ROOT_START_SECTOR = _RESERVED_SECTORS + _FAT_COUNT * _SECTORS_PER_FAT
_DATA_START_SECTOR = _ROOT_START_SECTOR + _ROOT_SECTORS
_DATA_CLUSTER_COUNT = _TOTAL_SECTORS - _DATA_START_SECTOR
_MAX_SEED_FILE_BYTES = 256 * 1024
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.img\Z")


class NoCloudImageError(ValueError):
    """Invalid NoCloud seed contents or unsafe seed-image path."""


def _fat12_set(fat: bytearray, cluster: int, value: int) -> None:
    offset = cluster + cluster // 2
    if offset + 1 >= len(fat):
        raise NoCloudImageError("seed filesystem cluster allocation is invalid")
    if cluster & 1:
        fat[offset] = (fat[offset] & 0x0F) | ((value << 4) & 0xF0)
        fat[offset + 1] = (value >> 4) & 0xFF
    else:
        fat[offset] = value & 0xFF
        fat[offset + 1] = (fat[offset + 1] & 0xF0) | ((value >> 8) & 0x0F)


def _short_checksum(short_name: bytes) -> int:
    checksum = 0
    for value in short_name:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + value) & 0xFF
    return checksum


def _long_name_entry(name: str, short_name: bytes) -> bytes:
    units = list(struct.unpack("<" + "H" * len(name), name.encode("utf-16le")))
    if len(units) > 12:
        raise NoCloudImageError("seed filename is too long for the fixed VFAT root entry")
    units.append(0)
    units.extend([0xFFFF] * (13 - len(units)))
    entry = bytearray(32)
    entry[0] = 0x41  # one long-name entry, marked last
    entry[11] = 0x0F
    entry[12] = 0
    entry[13] = _short_checksum(short_name)
    entry[26:28] = b"\x00\x00"
    name_bytes = struct.pack("<13H", *units)
    entry[1:11] = name_bytes[0:10]
    entry[14:26] = name_bytes[10:22]
    entry[28:32] = name_bytes[22:26]
    return bytes(entry)


def _short_file_entry(short_name: bytes, first_cluster: int, size: int) -> bytes:
    if len(short_name) != 11:
        raise NoCloudImageError("internal VFAT short filename is invalid")
    entry = bytearray(32)
    entry[0:11] = short_name
    entry[11] = 0x20  # archive
    entry[16:18] = (0x21).to_bytes(2, "little")  # 1980-01-01 creation date
    entry[18:20] = (0x21).to_bytes(2, "little")  # 1980-01-01 access date
    entry[24:26] = (0x21).to_bytes(2, "little")  # 1980-01-01 write date
    entry[26:28] = first_cluster.to_bytes(2, "little")
    entry[28:32] = size.to_bytes(4, "little")
    return bytes(entry)


def _validate_seed_text(user_data: str, meta_data: str) -> tuple[bytes, bytes]:
    if not isinstance(user_data, str) or not user_data.startswith("#cloud-config\n"):
        raise NoCloudImageError("user-data must be a cloud-config document")
    if not isinstance(meta_data, str) or not meta_data.strip():
        raise NoCloudImageError("meta-data must be non-empty text")
    try:
        user_json = json.loads(user_data.split("\n", 1)[1])
        metadata = json.loads(meta_data)
        user_bytes = user_data.encode("utf-8")
        meta_bytes = meta_data.encode("utf-8")
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise NoCloudImageError("seed data must be valid UTF-8 JSON cloud-config and JSON metadata") from exc
    if not isinstance(user_json, dict) or not isinstance(user_json.get("autoinstall"), dict):
        raise NoCloudImageError("user-data must contain an autoinstall object")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("instance-id"), str) or not metadata["instance-id"]:
        raise NoCloudImageError("meta-data must contain a non-empty instance-id")
    if (
        not user_bytes
        or not meta_bytes
        or len(user_bytes) > _MAX_SEED_FILE_BYTES
        or len(meta_bytes) > _MAX_SEED_FILE_BYTES
        or b"\x00" in user_bytes
        or b"\x00" in meta_bytes
    ):
        raise NoCloudImageError("seed contents exceed the supported size or contain NUL bytes")
    return user_bytes, meta_bytes


def build_nocloud_vfat_image(user_data: str, meta_data: str) -> bytes:
    """Build a 2 MiB FAT12 image labelled CIDATA with user-data and meta-data.

    No external filesystem utility is called. The image is intended as a
    non-bootable raw VM data disk, not as CD-ROM/ISO media.
    """
    user_bytes, meta_bytes = _validate_seed_text(user_data, meta_data)
    files = (
        ("user-data", b"USER-D~1   ", user_bytes),
        ("meta-data", b"META-D~1   ", meta_bytes),
    )
    allocated: list[tuple[str, bytes, bytes, list[int]]] = []
    next_cluster = 2
    for long_name, short_name, content in files:
        cluster_count = (len(content) + _BYTES_PER_SECTOR - 1) // _BYTES_PER_SECTOR
        clusters = list(range(next_cluster, next_cluster + cluster_count))
        next_cluster += cluster_count
        allocated.append((long_name, short_name, content, clusters))
    if next_cluster - 2 > _DATA_CLUSTER_COUNT:
        raise NoCloudImageError("seed data does not fit the fixed VFAT filesystem")

    image = bytearray(SEED_MEDIA_SIZE_BYTES)
    boot = memoryview(image)[:_BYTES_PER_SECTOR]
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"MSWIN4.1"
    boot[11:13] = _BYTES_PER_SECTOR.to_bytes(2, "little")
    boot[13] = _SECTORS_PER_CLUSTER
    boot[14:16] = _RESERVED_SECTORS.to_bytes(2, "little")
    boot[16] = _FAT_COUNT
    boot[17:19] = _ROOT_ENTRIES.to_bytes(2, "little")
    boot[19:21] = _TOTAL_SECTORS.to_bytes(2, "little")
    boot[21] = 0xF8
    boot[22:24] = _SECTORS_PER_FAT.to_bytes(2, "little")
    boot[24:26] = (63).to_bytes(2, "little")
    boot[26:28] = (255).to_bytes(2, "little")
    boot[36] = 0x80
    boot[38] = 0x29
    serial = hashlib.sha256(user_bytes + b"\x00" + meta_bytes).digest()[:4]
    boot[39:43] = serial
    boot[43:54] = b"CIDATA     "
    boot[54:62] = b"FAT12   "
    boot[510:512] = b"\x55\xAA"

    fat_size = _SECTORS_PER_FAT * _BYTES_PER_SECTOR
    fat = bytearray(fat_size)
    fat[0:3] = b"\xF8\xFF\xFF"
    root = bytearray(_ROOT_ENTRIES * 32)
    volume = bytearray(32)
    volume[0:11] = b"CIDATA     "
    volume[11] = 0x08
    root[0:32] = volume
    root_offset = 32

    for long_name, short_name, content, clusters in allocated:
        if not clusters:
            raise NoCloudImageError("seed files must not be empty")
        root[root_offset:root_offset + 32] = _long_name_entry(long_name, short_name)
        root_offset += 32
        root[root_offset:root_offset + 32] = _short_file_entry(short_name, clusters[0], len(content))
        root_offset += 32
        for index, cluster in enumerate(clusters):
            _fat12_set(fat, cluster, clusters[index + 1] if index + 1 < len(clusters) else 0xFFF)
            sector = _DATA_START_SECTOR + cluster - 2
            start = sector * _BYTES_PER_SECTOR
            chunk = content[index * _BYTES_PER_SECTOR:(index + 1) * _BYTES_PER_SECTOR]
            image[start:start + len(chunk)] = chunk

    fat_start = _RESERVED_SECTORS * _BYTES_PER_SECTOR
    image[fat_start:fat_start + fat_size] = fat
    second_fat_start = fat_start + fat_size
    image[second_fat_start:second_fat_start + fat_size] = fat
    root_start = _ROOT_START_SECTOR * _BYTES_PER_SECTOR
    image[root_start:root_start + len(root)] = root
    return bytes(image)


def write_nocloud_vfat_image(output_path: Path, user_data: str, meta_data: str) -> Path:
    """Create a new private mode-0600 seed image without overwriting existing files."""
    raw_path = Path(output_path).expanduser()
    if ".." in raw_path.parts or not _SAFE_FILENAME.fullmatch(raw_path.name):
        raise NoCloudImageError("seed output path must end in a safe .img filename")
    image = build_nocloud_vfat_image(user_data, meta_data)
    try:
        return write_private_new_file(raw_path, image)
    except FilesystemSafetyError as exc:
        raise NoCloudImageError("could not create the private seed image safely") from exc
