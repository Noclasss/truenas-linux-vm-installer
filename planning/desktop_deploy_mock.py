"""Offline-only desktop deployment state machine.

This module deliberately has no TrueNAS adapter, subprocesses, or filesystem writes.
It exercises sequencing against an injected fake. It cannot deploy a real VM.
"""
from dataclasses import dataclass
from typing import Protocol

if __package__:
    from .ubuntu_desktop import STAGING_RESERVE_BYTES
else:
    from ubuntu_desktop import STAGING_RESERVE_BYTES

_GIB = 1024**3
_HOST_RESERVE_MIB = 1024
_POOL_RESERVE_BYTES = _GIB

class DeploymentRefused(RuntimeError):
    pass

@dataclass(frozen=True)
class DesktopRequest:
    name: str
    pool: str
    bridge: str
    vcpus: int
    memory_mib: int
    disk_gib: int

class Backend(Protocol):
    def preflight(self, request: DesktopRequest) -> None: ...
    def exists(self, kind: str, name: str) -> bool: ...
    def create_parent_datasets(self, request: DesktopRequest) -> tuple[str, ...]: ...
    def dataset_type(self, name: str) -> str | None: ...
    def create_disk(self, request: DesktopRequest) -> str: ...
    def create_vm(self, request: DesktopRequest, disk: str) -> str: ...
    def attach_installer(self, vm: str) -> None: ...
    def attach_seed(self, vm: str) -> None: ...
    def attach_display(self, vm: str) -> None: ...
    def start(self, vm: str) -> None: ...
    def status(self, vm: str) -> str: ...

def resource_names(request: DesktopRequest) -> tuple[str, str]:
    if __package__:
        from .preview import build_plan
    else:
        from preview import build_plan

    plan = build_plan({
        "profile": "ubuntu-desktop", "name": request.name,
        "pool": request.pool, "bridge": request.bridge,
        "vcpus": request.vcpus, "memory_mib": request.memory_mib,
        "disk_gib": request.disk_gib,
    })
    return plan["vm"]["name"], plan["storage"]["disk"]

def rehearse(request: DesktopRequest, backend: Backend, *, confirmation: str = "") -> tuple[str, ...]:
    """Exercise an ordered creation sequence on an injected fake only.

    No automatic rollback: ambiguous results must be reconciled manually.
    Starting the installer is explicit and the finished desktop is NOT started.
    """
    if type(backend) is not FakeBackend:
        raise DeploymentRefused("only the built-in offline fake is permitted")
    try:
        vm_name, disk_name = resource_names(request)
    except ValueError as exc:
        raise DeploymentRefused(f"offline planner rejected the mock request: {exc}") from exc
    backend.preflight(request)
    if backend.exists("vm", vm_name) or backend.exists("disk", disk_name):
        raise DeploymentRefused("VM or disk already exists")
    if confirmation != "CREATE":
        raise DeploymentRefused("exact CREATE confirmation required")
    events = []
    try:
        vm_dataset = disk_name.rpartition("/")[0]
        shared_parent = vm_dataset.rpartition("/")[0]
        shared_parent_existed = backend.exists("dataset", shared_parent)
        created_parents = backend.create_parent_datasets(request)
        events.append("parent-datasets-created")
        expected_created = (vm_dataset,) if shared_parent_existed else (shared_parent, vm_dataset)
        if (
            backend.dataset_type(shared_parent) != "FILESYSTEM"
            or backend.dataset_type(vm_dataset) != "FILESYSTEM"
            or created_parents != expected_created
        ):
            raise DeploymentRefused("parent dataset read-back mismatch")
        disk = backend.create_disk(request)
        events.append("disk-created")
        if (
            disk != disk_name
            or not backend.exists("disk", disk_name)
            or backend.dataset_type(disk_name) != "VOLUME"
        ):
            raise DeploymentRefused("disk read-back mismatch")
        vm = backend.create_vm(request, disk)
        events.append("vm-created")
        if vm != vm_name or not backend.exists("vm", vm_name):
            raise DeploymentRefused("VM read-back mismatch")
        backend.attach_installer(vm)
        events.append("installer-attached")
        backend.attach_seed(vm)
        events.append("seed-attached")
        backend.attach_display(vm)
        events.append("display-attached")
        backend.start(vm)
        events.append("installer-start-requested")
        if backend.status(vm) != "RUNNING":
            raise DeploymentRefused("installer start not confirmed")
        events.append("installer-running")
    except Exception as exc:
        raise DeploymentRefused(
            "partial or ambiguous mock deployment; reconcile resources manually; "
            f"completed steps: {', '.join(events) or 'none'}"
        ) from exc
    return tuple(events)

class FakeBackend:
    """In-memory fixture only; never delegates to a real system."""
    def __init__(
        self,
        *,
        fail_at=None,
        pools=None,
        bridges=None,
        datasets=None,
        supports_virtualization=True,
        maximum_vcpus=8,
        available_memory_mib=32768,
        pool_reserve_bytes=_POOL_RESERVE_BYTES,
    ):
        self.resources = {"vm": set(), "disk": set()}
        self.datasets = dict(datasets or {})
        self.owned_datasets = set()
        self.events = []
        self.fail_at = fail_at
        self.states = {}
        self.pools = pools if pools is not None else {
            "testpool": {"healthy": True, "available_bytes": 100 * _GIB}
        }
        self.bridges = set(bridges) if bridges is not None else {"br0"}
        self.supports_virtualization = supports_virtualization
        self.maximum_vcpus = maximum_vcpus
        self.available_memory_mib = available_memory_mib
        self.pool_reserve_bytes = pool_reserve_bytes

    def _record(self, action):
        if action == self.fail_at:
            raise RuntimeError("injected failure")
        self.events.append(action)

    def preflight(self, request):
        if __package__:
            from .preview import build_plan
        else:
            from preview import build_plan

        self._record("preflight")
        if self.supports_virtualization is not True:
            raise DeploymentRefused("mock host does not report virtualization support")
        if type(self.maximum_vcpus) is not int or self.maximum_vcpus < 1:
            raise DeploymentRefused("mock host returned an invalid maximum vCPU count")
        if request.vcpus > self.maximum_vcpus:
            raise DeploymentRefused("requested vCPU count exceeds mock host support")
        if type(self.available_memory_mib) is not int or self.available_memory_mib < 0:
            raise DeploymentRefused("mock host returned invalid available memory")
        if self.available_memory_mib < request.memory_mib + _HOST_RESERVE_MIB:
            raise DeploymentRefused("mock host memory is below the request plus reserve")
        pool = self.pools.get(request.pool)
        if not isinstance(pool, dict) or pool.get("healthy") is not True:
            raise DeploymentRefused("selected mock pool is missing or unhealthy")
        available = pool.get("available_bytes")
        if type(available) is not int or available < 0:
            raise DeploymentRefused("selected mock pool has invalid available capacity")
        if type(self.pool_reserve_bytes) is not int or self.pool_reserve_bytes < 0:
            raise DeploymentRefused("mock pool reserve must be a non-negative byte count")
        required = request.disk_gib * _GIB + STAGING_RESERVE_BYTES + self.pool_reserve_bytes
        if available < required:
            raise DeploymentRefused("mock pool lacks disk, artifact staging, and reserve capacity")
        shared_parent = f"{request.pool}/linux-vms"
        if shared_parent in self.datasets and self.datasets[shared_parent] != "FILESYSTEM":
            raise DeploymentRefused("existing shared parent is not a filesystem dataset")
        inventory = {
            "vm_names": sorted(self.resources["vm"]),
            "datasets": sorted(self.datasets),
            "bridges": sorted(self.bridges),
            "pools": {
                name: {"available_bytes": details["available_bytes"]}
                for name, details in self.pools.items()
                if isinstance(details, dict) and type(details.get("available_bytes")) is int
            },
        }
        try:
            build_plan({
                "profile": "ubuntu-desktop", "name": request.name,
                "pool": request.pool, "bridge": request.bridge,
                "vcpus": request.vcpus, "memory_mib": request.memory_mib,
                "disk_gib": request.disk_gib,
            }, inventory)
        except ValueError as exc:
            raise DeploymentRefused(f"offline planner rejected the mock request: {exc}") from exc

    def exists(self, kind, name):
        if kind == "dataset":
            return name in self.datasets
        if kind not in self.resources:
            raise DeploymentRefused("unknown fake resource kind")
        return name in self.resources[kind]

    def dataset_type(self, name):
        return self.datasets.get(name)

    def create_parent_datasets(self, request):
        self._record("create_parent_datasets")
        _, disk_name = resource_names(request)
        vm_parent = disk_name.rpartition("/")[0]
        shared_parent = vm_parent.rpartition("/")[0]
        if shared_parent in self.datasets and self.datasets[shared_parent] != "FILESYSTEM":
            raise DeploymentRefused("existing shared parent is not a filesystem dataset")
        if vm_parent in self.datasets or any(
            name.startswith(vm_parent + "/") for name in self.datasets
        ):
            raise DeploymentRefused("target VM dataset path already exists")
        created = []
        if shared_parent not in self.datasets:
            self.datasets[shared_parent] = "FILESYSTEM"
            self.owned_datasets.add(shared_parent)
            created.append(shared_parent)
        self.datasets[vm_parent] = "FILESYSTEM"
        self.owned_datasets.add(vm_parent)
        created.append(vm_parent)
        return tuple(created)

    def create_disk(self, request):
        self._record("create_disk")
        name = resource_names(request)[1]
        parent = name.rpartition("/")[0]
        if self.datasets.get(parent) != "FILESYSTEM":
            raise RuntimeError("disk parent filesystem is missing")
        if name in self.datasets:
            raise DeploymentRefused("target disk dataset already exists")
        self.resources["disk"].add(name)
        self.datasets[name] = "VOLUME"
        self.owned_datasets.add(name)
        return name

    def create_vm(self, request, disk):
        self._record("create_vm")
        if disk not in self.resources["disk"]:
            raise RuntimeError("missing disk")
        self.resources["vm"].add(request.name)
        self.states[request.name] = "STOPPED"
        return request.name

    def attach_installer(self, vm):
        self._record("attach_installer")

    def attach_seed(self, vm):
        self._record("attach_seed")

    def attach_display(self, vm):
        self._record("attach_display")

    def start(self, vm):
        self._record("start")
        self.states[vm] = "RUNNING"

    def status(self, vm):
        self._record("status")
        return self.states[vm]
