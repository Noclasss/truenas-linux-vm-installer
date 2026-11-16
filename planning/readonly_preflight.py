"""Read-only, exact-build TrueNAS preflight for experimental Ubuntu profiles.

This module never creates a VM, dataset, manifest, or device. Resource choices
are mandatory caller inputs; read-only queries only verify those choices.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

if __package__:
    from .installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from .installer_core.schema import SchemaCheckedClient, SchemaError, validate_method_request
else:  # direct execution as ``python planning/check_readonly_preflight.py``
    from installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from installer_core.schema import SchemaCheckedClient, SchemaError, validate_method_request



READ_ONLY_METHODS = frozenset({
    "pool.query",
    "pool.dataset.query",
    "vm.query",
    "vm.device.query",
    "vm.device.nic_attach_choices",
    "vm.supports_virtualization",
    "vm.maximum_supported_vcpus",
    "vm.get_available_memory",
})
_COMPONENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_VM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,62}\Z")
_BRIDGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_GIB = 1024**3
_MIB = 1024**2


class PreflightError(RuntimeError):
    """A request, runtime result, or prerequisite did not pass safely."""


@dataclass(frozen=True)
class ReadOnlyPreflightRequest:
    profile: str
    vm_name: str
    pool_name: str
    bridge_name: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    host_reserve_mib: int
    pool_reserve_mib: int
    staging_reserve_bytes: int


@dataclass(frozen=True)
class ReadOnlyPreflightReport:
    truenas_version: str
    checked_method_contracts: int
    vm_name: str
    pool_name: str
    bridge_name: str
    checks: tuple[str, ...]
    live_write_authorized: bool = False
    vm_started: bool = False


def _validate_request(request: ReadOnlyPreflightRequest) -> None:
    if not isinstance(request, ReadOnlyPreflightRequest):
        raise PreflightError("preflight request has an unsupported type")
    if request.profile not in {"ubuntu-server", "ubuntu-desktop"}:
        raise PreflightError("unsupported profile for read-only live preflight")
    if not isinstance(request.vm_name, str) or not _VM_NAME.fullmatch(request.vm_name):
        raise PreflightError("VM name must start with an ASCII letter and contain only ASCII letters or digits")
    if not isinstance(request.pool_name, str) or not _COMPONENT.fullmatch(request.pool_name):
        raise PreflightError("pool name must be a safe single path component")
    if request.pool_name.casefold() in {"boot-pool", "freenas-boot"}:
        raise PreflightError("boot pools are not eligible VM storage targets")
    if not isinstance(request.bridge_name, str) or not _BRIDGE.fullmatch(request.bridge_name):
        raise PreflightError("bridge name is invalid")
    minimum_vcpus, minimum_memory_mib, minimum_disk_gib = (
        (2, 6 * 1024, 25) if request.profile == "ubuntu-desktop" else (1, 1024, 16)
    )
    bounds = (
        (request.vcpus, minimum_vcpus, 128, "vCPU count"),
        (request.memory_mib, minimum_memory_mib, 1_048_576, "RAM MiB"),
        (request.disk_gib, minimum_disk_gib, 65_536, "virtual disk GiB"),
        (request.host_reserve_mib, 1, 1_048_576, "host memory reserve MiB"),
        (request.pool_reserve_mib, 1, 1_048_576, "pool free-space reserve MiB"),
        (request.staging_reserve_bytes, 1, 2**63 - 1, "artifact/seed staging reserve bytes"),
    )
    for value, low, high, label in bounds:
        if type(value) is not int or not low <= value <= high:
            raise PreflightError(f"{label} must be an integer from {low} to {high}")


def _query_rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise PreflightError(f"{label} returned an invalid query result")
    return value


def _read(client: SchemaCheckedClient, methods: dict[str, Any], method: str, params: list[Any]) -> Any:
    if method not in READ_ONLY_METHODS:
        raise PreflightError("internal preflight attempted a non-allowlisted read")
    try:
        validate_method_request(methods, method, params)
        return client.call_read(method, params)
    except SchemaError as exc:
        raise PreflightError(f"reviewed request for {method} failed schema validation") from exc


def _validate_desktop_request_shapes(methods: dict[str, Any], request: ReadOnlyPreflightRequest) -> None:
    if request.profile != "ubuntu-desktop":
        return
    samples = (
        ("vm.create", [{
            "name": request.vm_name,
            "vcpus": 1,
            "cores": request.vcpus,
            "threads": 1,
            "memory": request.memory_mib,
            "autostart": False,
            "ensure_display_device": True,
        }]),
        ("vm.device.create", [{
            "vm": 1,
            "attributes": {
                "dtype": "CDROM",
                "path": f"/mnt/{request.pool_name}/linux-vms/{request.vm_name}/ubuntu-installer.iso",
            },
        }]),
        ("vm.device.create", [{
            "vm": 1,
            "attributes": {
                "dtype": "RAW",
                "path": f"/mnt/{request.pool_name}/linux-vms/{request.vm_name}/cidata.img",
                "exists": True,
                "boot": False,
                "type": "AHCI",
            },
        }]),
    )
    try:
        for method, params in samples:
            validate_method_request(methods, method, params)
    except SchemaError as exc:
        raise PreflightError(
            "reviewed Ubuntu Desktop VM/display, installer CD-ROM, or NoCloud RAW-disk request shape is unsupported"
        ) from exc


def run_read_only_preflight(
    client: Any,
    fixture: dict[str, Any],
    request: ReadOnlyPreflightRequest,
) -> ReadOnlyPreflightReport:
    """Verify exact build/schema and caller-selected resources using read-only calls.

    The returned result is a preflight record, not authorization for any write,
    VM creation, VM start, or change to host storage/network configuration.
    """
    _validate_request(request)
    methods = fixture.get("methods") if isinstance(fixture, dict) else None
    if not isinstance(methods, dict):
        raise PreflightError("reviewed method fixture is malformed")
    _validate_desktop_request_shapes(methods, request)
    semantics = fixture.get("method_semantics")
    for field, unit in (
        ("pool.query.free", "bytes"),
        ("vm.create.memory", "MiB"),
        ("vm.get_available_memory.result", "bytes"),
    ):
        evidence = semantics.get(field) if isinstance(semantics, dict) else None
        if (
            not isinstance(evidence, dict)
            or evidence.get("truenas_version") != fixture.get("truenas_version")
            or evidence.get("unit") != unit
        ):
            raise PreflightError(f"{field} unit is not verified for this TrueNAS build; refusing to compare")
    try:
        compatibility = verify_runtime_compatibility(client, fixture)
    except CompatibilityError as exc:
        raise PreflightError(str(exc)) from exc

    reader = SchemaCheckedClient(client, allowed_read_methods=READ_ONLY_METHODS)
    pool_rows = _query_rows(_read(reader, methods, "pool.query", [[], {
        "select": ["name", "healthy", "warning", "status", "free"],
    }]), "pool.query")
    pool_matches = [row for row in pool_rows if row.get("name") == request.pool_name]
    if len(pool_matches) != 1:
        raise PreflightError("selected pool was not found exactly once")
    pool = pool_matches[0]
    if pool.get("healthy") is not True or pool.get("warning") is not False:
        raise PreflightError("selected pool is unhealthy, warning, or missing required health fields")
    if pool.get("status") != "ONLINE":
        raise PreflightError("selected pool is not ONLINE")
    free_bytes = pool.get("free")
    if type(free_bytes) is not int or free_bytes < 0:
        raise PreflightError("selected pool did not report a valid free-space byte count")
    required_free_bytes = (
        request.disk_gib * _GIB
        + request.staging_reserve_bytes
        + request.pool_reserve_mib * _MIB
    )
    if free_bytes < required_free_bytes:
        raise PreflightError(
            "selected pool has less than the full disk, staging space, and requested reserve"
        )

    bridge_result = _read(reader, methods, "vm.device.nic_attach_choices", [])
    if not isinstance(bridge_result, dict) or not isinstance(bridge_result.get("BRIDGE"), list):
        raise PreflightError("VM NIC attach choices returned an invalid result")
    bridge_choices = bridge_result["BRIDGE"]
    if any(not isinstance(item, str) for item in bridge_choices):
        raise PreflightError("VM NIC attach choices contains invalid bridge entries")
    if request.bridge_name not in bridge_choices:
        raise PreflightError("caller-selected bridge is not an eligible VM attachment target")

    vm_rows = _query_rows(_read(reader, methods, "vm.query", [[], {"select": ["name"]}]), "vm.query")
    vm_names: list[str] = []
    for row in vm_rows:
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise PreflightError("VM query returned a row without a valid name")
        vm_names.append(name)
    if len({name.casefold() for name in vm_names}) != len(vm_names):
        raise PreflightError("VM query returned ambiguous case-insensitive names")
    if request.vm_name.casefold() in {name.casefold() for name in vm_names}:
        raise PreflightError("a VM with the requested name already exists")

    device_rows = _query_rows(_read(reader, methods, "vm.device.query", [[], {
        "select": ["id", "vm", "attributes"],
    }]), "vm.device.query")
    intended_device_paths = {
        f"/dev/zvol/{request.pool_name}/linux-vms/{request.vm_name}/disk0": "zvol",
    }
    if request.profile == "ubuntu-desktop":
        media_root = f"/mnt/{request.pool_name}/linux-vms/{request.vm_name}"
        intended_device_paths.update({
            f"{media_root}/ubuntu-installer.iso": "installer ISO",
            f"{media_root}/cidata.img": "NoCloud seed image",
        })
    for row in device_rows:
        attributes = row.get("attributes")
        if not isinstance(attributes, dict):
            raise PreflightError("VM device query returned a row without valid attributes")
        device_path = attributes.get("path")
        if device_path is not None and not isinstance(device_path, str):
            raise PreflightError("VM device query returned an invalid device path")
        if isinstance(device_path, str):
            for intended_path, resource_kind in intended_device_paths.items():
                if device_path.casefold() == intended_path.casefold():
                    raise PreflightError(f"an existing VM device already references the intended {resource_kind} path")

    dataset_rows = _query_rows(_read(reader, methods, "pool.dataset.query", [[], {
        "select": ["id", "type"],
    }]), "pool.dataset.query")
    datasets: dict[str, str] = {}
    folded_ids: set[str] = set()
    for row in dataset_rows:
        dataset_id, dataset_type = row.get("id"), row.get("type")
        if not isinstance(dataset_id, str) or not dataset_id or not isinstance(dataset_type, str):
            raise PreflightError("dataset query returned a row without valid id/type fields")
        folded = dataset_id.casefold()
        if folded in folded_ids:
            raise PreflightError("dataset query returned case-insensitive duplicate identifiers")
        folded_ids.add(folded)
        datasets[dataset_id] = dataset_type

    shared_parent = f"{request.pool_name}/linux-vms"
    vm_parent = f"{shared_parent}/{request.vm_name}"
    disk_zvol = f"{vm_parent}/disk0"
    shared_type = next((kind for name, kind in datasets.items() if name.casefold() == shared_parent.casefold()), None)
    if shared_type is not None and shared_type != "FILESYSTEM":
        raise PreflightError("shared VM parent exists with the wrong dataset type")
    for dataset_id in datasets:
        folded_id = dataset_id.casefold()
        folded_parent = vm_parent.casefold()
        if folded_id == disk_zvol.casefold():
            raise PreflightError("the target disk zvol already exists")
        if folded_id == folded_parent or folded_id.startswith(folded_parent + "/"):
            raise PreflightError("a target or descendant dataset already exists for this VM")

    supports_virtualization = _read(reader, methods, "vm.supports_virtualization", [])
    if supports_virtualization is not True:
        raise PreflightError("host virtualization support is absent or could not be verified")
    maximum_vcpus = _read(reader, methods, "vm.maximum_supported_vcpus", [])
    if type(maximum_vcpus) is not int or maximum_vcpus < 1:
        raise PreflightError("host returned an invalid maximum supported vCPU count")
    if request.vcpus > maximum_vcpus:
        raise PreflightError("requested vCPU count exceeds the host-supported maximum")

    # The exact-build core.get_methods capture describes this return value in bytes.
    available_memory_bytes = _read(reader, methods, "vm.get_available_memory", [False])
    if type(available_memory_bytes) is not int or available_memory_bytes < 0:
        raise PreflightError("host returned an invalid available-memory value")
    required_memory_bytes = (request.memory_mib + request.host_reserve_mib) * _MIB
    if available_memory_bytes < required_memory_bytes:
        raise PreflightError("host available memory is below the VM request plus requested reserve")

    checks = (
        "exact build and reviewed request/return method contracts",
        "caller-selected pool health, ONLINE state, and capacity for the full disk, artifact staging, and requested reserve",
        "caller-selected bridge is an eligible VM NIC attachment target",
        "VM name and intended dataset paths are collision-free",
        "hardware virtualization and requested vCPU limit",
        "available memory meets the request plus caller-selected reserve",
    )
    if request.profile == "ubuntu-desktop":
        checks += (
            "desktop VM display enablement, installer CD-ROM, and NoCloud RAW data-disk requests match reviewed exact-build schemas",
        )
    return ReadOnlyPreflightReport(
        truenas_version=compatibility.truenas_version,
        checked_method_contracts=len(compatibility.checked_methods),
        vm_name=request.vm_name,
        pool_name=request.pool_name,
        bridge_name=request.bridge_name,
        checks=checks,
    )
