"""Review Ubuntu Desktop middleware write payloads against the pinned offline fixture.

This is a *proposal* generator, not a writer: it does not contact TrueNAS,
stage images, create files, or enable a live deployment profile. A schema
match proves payload shape only, not authorization or runtime acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

if __package__:
    from .installer_core.schema import SchemaError, validate_method_request
    from .preview import build_plan
else:
    from installer_core.schema import SchemaError, validate_method_request
    from preview import build_plan

REVIEWED_BUILD = "TrueNAS-26.0.0-BETA.2"
FIXTURE_NAME = "truenas-26.0.0-beta.2-method-contracts.json"
_GIB = 1024**3

# Installer media names inside the installer-owned VM dataset (see docs/desktop-staged-media-design.md).
INSTALLER_ISO_NAME = "ubuntu-installer.iso"
SEED_IMAGE_NAME = "cidata.img"


class DesktopWriteContractError(ValueError):
    """An offline proposed write does not match the reviewed method contract."""


def media_path(disk: str, filename: str) -> str:
    """Host path of installer media in the VM dataset that holds the given zvol."""
    vm_dataset = disk.rpartition("/")[0]
    return f"/mnt/{vm_dataset}/{filename}"


def load_reviewed_desktop_fixture() -> dict[str, Any]:
    """Read only the checked-in, sanitized method fixture; never query a host."""
    path = Path(__file__).resolve().parent / "fixtures" / FIXTURE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DesktopWriteContractError("reviewed Desktop method fixture is unavailable") from exc
    if not isinstance(data, dict):
        raise DesktopWriteContractError("reviewed Desktop method fixture is malformed")
    return data


def build_desktop_write_intents(
    request: Any,
    fixture: dict[str, Any],
    *,
    vm_id: int,
    shared_parent_exists: bool = False,
) -> tuple[tuple[str, list[Any]], ...]:
    """Validate an offline sequence of *proposed* writes for the reviewed build.

    The returned vm_id is a caller-supplied fake placeholder. A live workflow
    would have to revalidate the actual returned ID and exact runtime methods
    after fresh read-only preflight; this function cannot perform those steps.
    """
    if type(vm_id) is not int or vm_id <= 0:
        raise DesktopWriteContractError("mock VM ID must be a positive integer")
    if type(shared_parent_exists) is not bool:
        raise DesktopWriteContractError("shared parent state must be a boolean")
    if not isinstance(fixture, dict) or fixture.get("truenas_version") != REVIEWED_BUILD:
        raise DesktopWriteContractError("fixture is not the exact reviewed TrueNAS build")
    methods = fixture.get("methods")
    semantics = fixture.get("method_semantics")
    if not isinstance(methods, dict) or not isinstance(semantics, dict):
        raise DesktopWriteContractError("reviewed fixture lacks method contracts or units")
    for key, expected in (
        ("pool.query.free", "bytes"),
        ("vm.create.memory", "MiB"),
        ("vm.get_available_memory.result", "bytes"),
    ):
        unit = semantics.get(key)
        if not isinstance(unit, dict) or unit.get("unit") != expected:
            raise DesktopWriteContractError("reviewed fixture unit semantics are missing or changed")

    try:
        plan = build_plan({
            "profile": "ubuntu-desktop",
            "name": request.name,
            "pool": request.pool,
            "bridge": request.bridge,
            "vcpus": request.vcpus,
            "memory_mib": request.memory_mib,
            "disk_gib": request.disk_gib,
        })
    except (AttributeError, TypeError, ValueError) as exc:
        raise DesktopWriteContractError("offline planner rejected Desktop write intent") from exc

    name = plan["vm"]["name"]
    disk = plan["storage"]["disk"]
    vm_dataset = disk.rpartition("/")[0]
    shared_parent = vm_dataset.rpartition("/")[0]
    media_root = f"/mnt/{vm_dataset}"

    intents: list[tuple[str, list[Any]]] = []
    if not shared_parent_exists:
        intents.append(("pool.dataset.create", [{
            "name": shared_parent, "type": "FILESYSTEM", "create_ancestors": False,
        }]))
    intents.extend([
        ("pool.dataset.create", [{
            "name": vm_dataset, "type": "FILESYSTEM", "create_ancestors": False,
        }]),
        ("pool.dataset.create", [{
            "name": disk, "type": "VOLUME", "volsize": request.disk_gib * _GIB,
            "create_ancestors": False,
        }]),
        ("vm.create", [{
            "name": name, "vcpus": 1, "cores": request.vcpus,
            "threads": 1, "memory": request.memory_mib, "bootloader": "UEFI",
            "enable_secure_boot": False, "autostart": False,
            "ensure_display_device": True,
        }]),
        ("vm.device.create", [{
            "vm": vm_id, "attributes": {
                "dtype": "DISK", "path": f"/dev/zvol/{disk}",
                "type": "VIRTIO", "create_zvol": False,
            },
        }]),
        ("vm.device.create", [{
            "vm": vm_id, "attributes": {
                "dtype": "CDROM", "path": f"{media_root}/{INSTALLER_ISO_NAME}",
            },
        }]),
        ("vm.device.create", [{
            "vm": vm_id, "attributes": {
                "dtype": "RAW", "path": f"{media_root}/{SEED_IMAGE_NAME}",
                "exists": True, "boot": False, "type": "AHCI",
            },
        }]),
        ("vm.device.create", [{
            "vm": vm_id, "attributes": {
                "dtype": "NIC", "nic_attach": request.bridge, "type": "VIRTIO",
            },
        }]),
        ("vm.start", [vm_id, {"overcommit": False}]),
    ])

    for method, params in intents:
        try:
            validate_method_request(methods, method, params)
        except SchemaError as exc:
            raise DesktopWriteContractError(
                f"proposed Desktop write does not match reviewed schema: {method}"
            ) from exc
    return tuple(intents)
