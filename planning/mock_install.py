"""Mock-only Ubuntu Server install orchestration rehearsal.

This module is an in-memory test harness, not an installer. It cannot connect
through midclt, download/convert an image, write host storage, or start a VM.
The distinct MockMiddleware type is required so a production adapter cannot be
passed accidentally. Live profile support remains empty.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if __package__:
    from .installer_core.safe_filesystem import (
        FilesystemSafetyError,
        atomic_replace_private_file,
        write_private_new_file,
    )
    from .installer_core.schema import SchemaError, validate_method_request
    from .ubuntu_server import PROFILE_ID
else:
    from installer_core.safe_filesystem import (
        FilesystemSafetyError,
        atomic_replace_private_file,
        write_private_new_file,
    )
    from installer_core.schema import SchemaError, validate_method_request
    from ubuntu_server import PROFILE_ID

LIVE_PROFILE_ALLOWLIST: frozenset[str] = frozenset()
_MOCK_VM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,62}\Z")
_MOCK_POOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MOCK_SEED_IDENTIFIER = "mock://seed/cidata.iso"


class MockInstallError(RuntimeError):
    """A mock install plan or rehearsal failed closed."""


class MockAmbiguousWrite(MockInstallError):
    """The fake middleware applied a write but simulated a lost response."""


@dataclass(frozen=True)
class MockInstallRequest:
    """Inputs for a local-only rehearsal; paths are opaque mock identifiers."""

    profile: str
    vm_name: str
    pool: str
    bridge: str
    vcpus: int
    memory_mib: int
    disk_gib: int
    seed_identifier: str = _MOCK_SEED_IDENTIFIER


@dataclass
class MockMiddleware:
    """Stateful in-memory TrueNAS substitute; has no transport or filesystem I/O."""

    methods: dict[str, Any]
    pools: dict[str, dict[str, Any]]
    bridges: set[str]
    fail_before_method: str | None = None
    fail_after_apply_method: str | None = None
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    vms: dict[int, dict[str, Any]] = field(default_factory=dict)
    devices: dict[int, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, list[Any]]] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    _next_id: int = 1

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Run a constrained simulated method and mutate only this object's state."""
        params = [] if params is None else params
        if not isinstance(params, list):
            raise MockInstallError("mock middleware parameters must be a list")
        self.calls.append((method, json.loads(json.dumps(params))))
        if method in {"pool.dataset.create", "vm.create", "vm.device.create"}:
            try:
                validate_method_request(self.methods, method, params)
            except SchemaError as exc:
                raise MockInstallError(f"mock request failed captured schema validation: {method}") from exc
            if self.fail_before_method == method:
                self.fail_before_method = None
                raise MockInstallError(f"simulated failure before applying {method}")
            self.writes.append(method)
            result = self._apply_write(method, params)
            if self.fail_after_apply_method == method:
                self.fail_after_apply_method = None
                raise MockAmbiguousWrite(f"simulated lost response after {method}")
            return result
        if method == "pool.query":
            return [{"name": name, **details} for name, details in sorted(self.pools.items())]
        if method == "interface.query":
            return [{"name": name, "type": "BRIDGE"} for name in sorted(self.bridges)]
        if method == "pool.dataset.query":
            return [dict(value) for _, value in sorted(self.datasets.items())]
        if method == "vm.query":
            return [dict(value) for _, value in sorted(self.vms.items())]
        if method == "vm.device.query":
            return [dict(value) for _, value in sorted(self.devices.items())]
        raise MockInstallError(f"method is not implemented by the in-memory mock: {method}")

    def _apply_write(self, method: str, params: list[Any]) -> dict[str, Any]:
        if method == "pool.dataset.create":
            spec = params[0]
            name = spec["name"]
            if name in self.datasets:
                raise MockInstallError("mock dataset already exists")
            pool_name, _, _ = name.partition("/")
            parent_name = name.rpartition("/")[0]
            if spec.get("create_ancestors") is True:
                raise MockInstallError("mock installer must create and manifest ancestors explicitly")
            if parent_name != pool_name and parent_name not in self.datasets:
                raise MockInstallError("mock dataset parent must be created explicitly first")
            dataset = {"id": name, **spec}
            self.datasets[name] = dataset
            return dict(dataset)
        if method == "vm.create":
            spec = params[0]
            if any(vm["name"] == spec["name"] for vm in self.vms.values()):
                raise MockInstallError("mock VM name already exists")
            vm_id = self._next_id
            self._next_id += 1
            vm = {"id": vm_id, "status": {"state": "STOPPED"}, **spec}
            self.vms[vm_id] = vm
            if spec.get("ensure_display_device") is True:
                self._create_device(vm_id, {"dtype": "DISPLAY"})
            return dict(vm)
        if method == "vm.device.create":
            spec = params[0]
            vm_id = spec["vm"]
            if vm_id not in self.vms:
                raise MockInstallError("mock device references an unknown VM")
            attributes = spec["attributes"]
            if attributes.get("dtype") == "NIC" and attributes.get("nic_attach") not in self.bridges:
                raise MockInstallError("mock NIC bridge is not in the supplied inventory")
            return self._create_device(vm_id, attributes)
        raise MockInstallError("mock write method is not supported")

    def _create_device(self, vm_id: int, attributes: dict[str, Any]) -> dict[str, Any]:
        device_id = self._next_id
        self._next_id += 1
        device = {"id": device_id, "vm": vm_id, "attributes": dict(attributes)}
        self.devices[device_id] = device
        return dict(device)


@dataclass(frozen=True)
class MockRunResult:
    manifest_path: Path
    vm_id: int
    dataset_ids: tuple[str, ...]
    device_ids: tuple[int, ...]
    final_power_state: str
    middleware_calls: tuple[str, ...]


def live_profile_enabled(profile: str) -> bool:
    """Return whether a production install path is enabled (currently never)."""
    return profile in LIVE_PROFILE_ALLOWLIST


def _check_dataset_collisions(
    datasets: list[dict[str, Any]], pool: str, parent: str, zvol: str
) -> None:
    shared_parent = f"{pool}/linux-vms"
    for dataset in datasets:
        name = dataset.get("id")
        if not isinstance(name, str):
            raise MockInstallError("mock dataset inventory contains an invalid identifier")
        if name == pool:
            continue
        if name == shared_parent:
            if dataset.get("type") != "FILESYSTEM":
                raise MockInstallError("mock shared parent is not a filesystem dataset")
            continue
        if (
            name in {parent, zvol}
            or name.startswith(parent + "/")
            or parent.startswith(name + "/")
        ):
            raise MockInstallError("an exact or ancestor/descendant dataset path conflicts with this VM")


def _validate_request(
    request: MockInstallRequest, middleware: MockMiddleware
) -> tuple[str, str, str, str]:
    if request.profile != PROFILE_ID or live_profile_enabled(request.profile):
        raise MockInstallError("only the disabled experimental Ubuntu Server profile is modeled")
    if not isinstance(request.vm_name, str) or not _MOCK_VM_NAME.fullmatch(request.vm_name):
        raise MockInstallError("VM name is invalid")
    if (
        not isinstance(request.pool, str)
        or not _MOCK_POOL_NAME.fullmatch(request.pool)
        or request.pool not in middleware.pools
    ):
        raise MockInstallError("selected pool is not present in the mock inventory")
    pool = middleware.pools[request.pool]
    if pool.get("healthy") is not True or type(pool.get("available_bytes")) is not int:
        raise MockInstallError("mock pool health or capacity is missing/invalid")
    shared_parent = f"{request.pool}/linux-vms"
    parent = f"{shared_parent}/{request.vm_name}"
    zvol = f"{parent}/disk0"
    _check_dataset_collisions(list(middleware.datasets.values()), request.pool, parent, zvol)
    if any(
        isinstance(vm.get("name"), str) and vm["name"].casefold() == request.vm_name.casefold()
        for vm in middleware.vms.values()
    ):
        raise MockInstallError("VM name already exists in the mock inventory")
    if request.bridge not in middleware.bridges:
        raise MockInstallError("selected bridge is not an existing mock bridge")
    if type(request.vcpus) is not int or not 1 <= request.vcpus <= 128:
        raise MockInstallError("vCPU count is outside the planner bounds")
    if type(request.memory_mib) is not int or not 1024 <= request.memory_mib <= 1_048_576:
        raise MockInstallError("RAM is outside the planner bounds")
    if type(request.disk_gib) is not int or not 16 <= request.disk_gib <= 65_536:
        raise MockInstallError("virtual disk size is outside the planner bounds")
    if request.seed_identifier != _MOCK_SEED_IDENTIFIER:
        raise MockInstallError("seed identifier must be the fixed mock-only URI")
    disk_bytes = request.disk_gib * 1024**3
    if pool["available_bytes"] < disk_bytes:
        raise MockInstallError("mock pool capacity is below the requested virtual-disk size")
    return shared_parent, parent, zvol, f"/dev/zvol/{zvol}"


def _manifest_bytes(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, sort_keys=True, indent=2) + "\n"


def _persist(manifest_path: Path, manifest: dict[str, Any], *, create: bool = False) -> None:
    content = _manifest_bytes(manifest)
    if create:
        write_private_new_file(manifest_path, content)
    else:
        atomic_replace_private_file(manifest_path, content)


def run_mock_install(
    request: MockInstallRequest,
    middleware: MockMiddleware,
    *,
    manifest_path: Path,
    confirmation: str,
) -> MockRunResult:
    """Rehearse orchestration against the in-memory fake only.

    No download, conversion, TrueNAS transport, host path, or VM start exists in
    this function. Writes affect only ``MockMiddleware`` and a caller-selected
    local test manifest. A failed or ambiguous run is recorded and never retried
    or automatically cleaned up.
    """
    if type(middleware) is not MockMiddleware:
        raise MockInstallError("mock-only backend required; no live middleware adapter is accepted")
    shared_parent, parent, zvol, disk_path = _validate_request(request, middleware)
    if confirmation != f"CREATE MOCK {request.vm_name}":
        raise MockInstallError("mock rehearsal requires exact per-VM confirmation")
    path = Path(manifest_path)
    manifest = {
        "format": "truenas-linux-mock-manifest-v1",
        "mode": "offline-mock-only",
        "profile": request.profile,
        "status": "preparing",
        "request": asdict(request),
        "planned": {
            "shared_parent_dataset": shared_parent,
            "parent_dataset": parent,
            "zvol": zvol,
            "vm_name": request.vm_name,
        },
        "created": {"datasets": [], "vm": None, "devices": []},
        "notes": [
            "No TrueNAS API, download, image conversion, host storage, or VM start occurred.",
            "Mock identifier paths are synthetic and must never be used as live paths.",
            "Dataset ancestors are created explicitly and recorded; create_ancestors is never used.",
        ],
    }
    try:
        _persist(path, manifest, create=True)
    except (OSError, FilesystemSafetyError) as exc:
        raise MockInstallError("could not exclusively create a private mock manifest") from exc

    try:
        datasets_before = middleware.call("pool.dataset.query", [])
        _check_dataset_collisions(datasets_before, request.pool, parent, zvol)
        if any(
            isinstance(vm.get("name"), str) and vm["name"].casefold() == request.vm_name.casefold()
            for vm in middleware.call("vm.query", [])
        ):
            raise MockInstallError("VM name appeared after mock preflight")

        if not any(item.get("id") == shared_parent for item in datasets_before):
            shared_parent_result = middleware.call(
                "pool.dataset.create", [{"name": shared_parent, "type": "FILESYSTEM"}]
            )
            manifest["created"]["datasets"].append(
                {"id": shared_parent_result["id"], "type": "FILESYSTEM"}
            )
            _persist(path, manifest)

        parent_result = middleware.call(
            "pool.dataset.create", [{"name": parent, "type": "FILESYSTEM"}]
        )
        manifest["created"]["datasets"].append({"id": parent_result["id"], "type": "FILESYSTEM"})
        _persist(path, manifest)

        zvol_result = middleware.call(
            "pool.dataset.create",
            [{"name": zvol, "type": "VOLUME", "volsize": request.disk_gib * 1024**3, "sparse": True}],
        )
        manifest["created"]["datasets"].append({"id": zvol_result["id"], "type": "VOLUME"})
        _persist(path, manifest)

        # A mock URI makes it impossible to accidentally pass a real local image path.
        vm_result = middleware.call(
            "vm.create",
            [{
                "name": request.vm_name,
                "memory": request.memory_mib,
                "vcpus": request.vcpus,
                "bootloader": "UEFI",
                "enable_secure_boot": False,
                "autostart": False,
                "ensure_display_device": True,
                "description": "EXPERIMENTAL MOCK ONLY - not a live installer VM",
            }],
        )
        vm_id = vm_result["id"]
        manifest["created"]["vm"] = {"id": vm_id, "name": request.vm_name}
        auto_devices = [
            device for device in middleware.call("vm.device.query", [])
            if device.get("vm") == vm_id
        ]
        if not any(device.get("attributes", {}).get("dtype") == "DISPLAY" for device in auto_devices):
            raise MockInstallError("mock VM creation did not produce the expected DISPLAY device")
        manifest["created"]["devices"].extend({
            "id": device["id"],
            "vm": vm_id,
            "dtype": device.get("attributes", {}).get("dtype"),
            "attributes": device.get("attributes", {}),
            "source": "middleware-created-mock-device",
        } for device in auto_devices)
        _persist(path, manifest)

        device_specs = [
            {"dtype": "DISK", "path": disk_path, "type": "VIRTIO"},
            {"dtype": "CDROM", "path": request.seed_identifier},
            {"dtype": "NIC", "nic_attach": request.bridge, "type": "VIRTIO"},
        ]
        for attributes in device_specs:
            device_result = middleware.call(
                "vm.device.create", [{"vm": vm_id, "attributes": attributes}]
            )
            manifest["created"]["devices"].append({
                "id": device_result["id"],
                "vm": vm_id,
                "dtype": attributes["dtype"],
                "attributes": attributes,
            })
            _persist(path, manifest)

        # Read back all exact fake targets, including the middleware-created DISPLAY.
        datasets = middleware.call("pool.dataset.query", [])
        vm_rows = middleware.call("vm.query", [])
        device_rows = middleware.call("vm.device.query", [])
        live_vm = [vm for vm in vm_rows if vm.get("id") == vm_id]
        live_devices = [device for device in device_rows if device.get("vm") == vm_id]
        recorded_ids = {row["id"] for row in manifest["created"]["devices"]}
        actual_ids = {row["id"] for row in live_devices}
        if len(live_vm) != 1:
            raise MockInstallError("mock VM read-back did not return exactly one VM")
        expected_vm = {
            "name": request.vm_name,
            "memory": request.memory_mib,
            "vcpus": request.vcpus,
            "bootloader": "UEFI",
            "enable_secure_boot": False,
            "autostart": False,
            "ensure_display_device": True,
        }
        if any(live_vm[0].get(key) != value for key, value in expected_vm.items()):
            raise MockInstallError("mock VM read-back did not match the stopped/autostart-disabled plan")
        if live_vm[0].get("status", {}).get("state") != "STOPPED":
            raise MockInstallError("mock VM did not remain stopped")
        if recorded_ids != actual_ids:
            raise MockInstallError("mock device read-back differs from the exact manifest device inventory")
        for recorded in manifest["created"]["devices"]:
            actual = next((device for device in live_devices if device.get("id") == recorded["id"]), None)
            if actual is None or actual.get("attributes") != recorded.get("attributes"):
                raise MockInstallError("mock device read-back did not match the exact recorded attributes")
        dataset_by_id = {item.get("id"): item for item in datasets}
        if (
            dataset_by_id.get(shared_parent, {}).get("type") != "FILESYSTEM"
            or dataset_by_id.get(parent, {}).get("type") != "FILESYSTEM"
            or dataset_by_id.get(zvol, {}).get("type") != "VOLUME"
            or dataset_by_id.get(zvol, {}).get("volsize") != request.disk_gib * 1024**3
        ):
            raise MockInstallError("mock dataset/zvol read-back is incomplete")
        manifest["status"] = "mock_verified_not_installed"
        manifest["created"]["devices"] = [
            {
                "id": device["id"],
                "vm": vm_id,
                "dtype": device.get("attributes", {}).get("dtype"),
                "attributes": device.get("attributes", {}),
                "source": "mock-middleware-read-back",
            }
            for device in sorted(live_devices, key=lambda item: item["id"])
        ]
        _persist(path, manifest)
        return MockRunResult(
            manifest_path=path,
            vm_id=vm_id,
            dataset_ids=tuple(item["id"] for item in manifest["created"]["datasets"]),
            device_ids=tuple(sorted(actual_ids)),
            final_power_state="STOPPED",
            middleware_calls=tuple(method for method, _ in middleware.calls),
        )
    except Exception as exc:
        manifest["status"] = (
            "reconcile_required"
            if isinstance(exc, MockAmbiguousWrite) or middleware.writes
            else "failed_no_automatic_cleanup"
        )
        manifest["failure_type"] = type(exc).__name__
        try:
            _persist(path, manifest)
        except (OSError, FilesystemSafetyError):
            pass
        if isinstance(exc, MockInstallError):
            raise
        raise MockInstallError("mock orchestration failed; manifest retained; no retry or cleanup performed") from exc
