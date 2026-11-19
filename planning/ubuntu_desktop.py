"""Static experimental Ubuntu Desktop artifact metadata; no network or host I/O."""

PROFILE_ID = "ubuntu-desktop"
PROFILE_STATUS = "experimental"
UBUNTU_RELEASE = "26.04.1 LTS"
IMAGE_FILENAME = "ubuntu-26.04.1-desktop-amd64.iso"
ARTIFACT_URL = f"https://releases.ubuntu.com/26.04.1/{IMAGE_FILENAME}"
ARTIFACT_SHA256 = "601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f"
ARTIFACT_SIZE_BYTES = 6_482_409_472
ARTIFACT_MAX_BYTES = ARTIFACT_SIZE_BYTES
SEED_MEDIA_SIZE_BYTES = 2 * 1024**2
STAGING_RESERVE_BYTES = ARTIFACT_SIZE_BYTES + SEED_MEDIA_SIZE_BYTES
MIN_VCPUS = 2
MIN_MEMORY_MIB = 6 * 1024
MIN_DISK_GIB = 25
DEFAULT_VCPUS = 2
DEFAULT_MEMORY_MIB = 6 * 1024
DEFAULT_DISK_GIB = 32
CHECKSUM_MANIFEST_URL = "https://releases.ubuntu.com/26.04.1/SHA256SUMS"
CHECKSUM_SIGNATURE_URL = CHECKSUM_MANIFEST_URL + ".gpg"
SIGNING_KEY_FINGERPRINT = "843938DF228D22F7B3742BC0D94AA3F0EFE21092"


def artifact_metadata() -> dict[str, object]:
    """Return pinned candidate provenance; this function performs no I/O."""
    return {
        "profile_id": PROFILE_ID,
        "status": PROFILE_STATUS,
        "release": UBUNTU_RELEASE,
        "architecture": "amd64",
        "filename": IMAGE_FILENAME,
        "url": ARTIFACT_URL,
        "sha256": ARTIFACT_SHA256,
        "size_bytes": ARTIFACT_SIZE_BYTES,
        "max_bytes": ARTIFACT_MAX_BYTES,
        "seed_media_size_bytes": SEED_MEDIA_SIZE_BYTES,
        "seed_media_format": "FAT12/VFAT raw disk image, volume label CIDATA",
        "staging_reserve_bytes": STAGING_RESERVE_BYTES,
        "guest_minimums": {
            "vcpus": MIN_VCPUS,
            "memory_mib": MIN_MEMORY_MIB,
            "disk_gib": MIN_DISK_GIB,
            "evidence": "Ubuntu 26.04 release notes; CPU frequency and graphical performance remain host-dependent",
        },
        "planning_defaults": {
            "vcpus": DEFAULT_VCPUS,
            "memory_mib": DEFAULT_MEMORY_MIB,
            "disk_gib": DEFAULT_DISK_GIB,
        },
        "checksum_manifest_url": CHECKSUM_MANIFEST_URL,
        "checksum_signature_url": CHECKSUM_SIGNATURE_URL,
        "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
        "integrity_status": (
            "Pinned SHA256SUMS signature and exact-size ISO bytes were verified "
            "locally on 2026-11-19; any future staged bytes must be reverified."
        ),
        "image_format": "iso",
        "image_format_status": (
            "Pinned Desktop ISO verified; its casper/install-sources.yaml was "
            "inspected separately; installation and guest boot remain untested."
        ),
    }
