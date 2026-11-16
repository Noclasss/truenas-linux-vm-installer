"""Offline-only desktop deployment state machine.

This module deliberately has no TrueNAS adapter, subprocesses, or filesystem writes.
It exercises sequencing against an injected fake. It cannot deploy a real VM.
"""
from dataclasses import dataclass
from typing import Protocol

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
    def create_disk(self, request: DesktopRequest) -> str: ...
    def create_vm(self, request: DesktopRequest, disk: str) -> str: ...
    def attach_installer(self, vm: str) -> None: ...
    def attach_seed(self, vm: str) -> None: ...
    def attach_display(self, vm: str) -> None: ...
    def start(self, vm: str) -> None: ...
    def status(self, vm: str) -> str: ...

def resource_names(request: DesktopRequest) -> tuple[str, str]:
    from .preview import build_plan
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
    vm_name, disk_name = resource_names(request)
    backend.preflight(request)
    if backend.exists("vm", vm_name) or backend.exists("disk", disk_name):
        raise DeploymentRefused("VM or disk already exists")
    if confirmation != "CREATE":
        raise DeploymentRefused("exact CREATE confirmation required")
    events = []
    try:
        disk = backend.create_disk(request)
        events.append("disk-created")
        if disk != disk_name or not backend.exists("disk", disk_name):
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
    def __init__(self, *, fail_at=None):
        self.resources = {"vm": set(), "disk": set()}
        self.events = []
        self.fail_at = fail_at
        self.states = {}

    def _record(self, action):
        if action == self.fail_at:
            raise RuntimeError("injected failure")
        self.events.append(action)

    def preflight(self, request):
        self._record("preflight")

    def exists(self, kind, name):
        return name in self.resources[kind]

    def create_disk(self, request):
        self._record("create_disk")
        name = resource_names(request)[1]
        self.resources["disk"].add(name)
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
