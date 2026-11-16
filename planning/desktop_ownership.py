"""Persistent ownership and read-only recovery for the Desktop offline fake.

No middleware, subprocess, downloads, host storage or live deployment path exists.
"""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__:
    from .desktop_deploy_mock import (
        DeploymentRefused, DesktopRequest, FakeBackend, resource_names,
    )
    from .installer_core.safe_filesystem import (
        FilesystemSafetyError, atomic_replace_private_file, ensure_trusted_directory_chain,
        write_private_new_file,
    )
else:
    from desktop_deploy_mock import (
        DeploymentRefused, DesktopRequest, FakeBackend, resource_names,
    )
    from installer_core.safe_filesystem import (
        FilesystemSafetyError, atomic_replace_private_file, ensure_trusted_directory_chain,
        write_private_new_file,
    )

_FORMAT = "truenas-linux-desktop-ownership-v1"


class OwnershipError(RuntimeError):
    """Invalid, unsafe, or unreconciled mock installation state."""


@dataclass(frozen=True)
class RecoveryReport:
    installation_id: str
    status: str
    resources: tuple[dict[str, Any], ...]
    attention: tuple[str, ...]
    can_continue: bool = False
    can_delete: bool = False


class PersistentFakeBackend(FakeBackend):
    """Stateful in-memory inventory with before/after-apply fault injection."""

    def __init__(self, *, fail_before=None, lose_response_after=None, **kwargs):
        super().__init__(**kwargs)
        self.fail_before = fail_before
        self.lose_response_after = lose_response_after
        self.vms: dict[int, dict[str, Any]] = {}
        self.devices: dict[int, dict[str, Any]] = {}
        self.vm_ids: dict[str, int] = {}
        self._next_id = 1
        self.operations: list[str] = []
        self.resource_owners: dict[str, str] = {}

    def _before(self, operation: str) -> None:
        self.operations.append(operation)
        if self.fail_before == operation:
            self.fail_before = None
            raise RuntimeError("injected failure before resource creation")

    def _after(self, operation: str, result: Any) -> Any:
        if self.lose_response_after == operation:
            self.lose_response_after = None
            raise TimeoutError("injected lost response after resource creation")
        return result

    def create_parent(self, path: str, installation_id: str) -> str:
        self._before("dataset:" + path)
        if path in self.datasets:
            raise OwnershipError("dataset already exists")
        self.datasets[path] = "FILESYSTEM"
        self.owned_datasets.add(path)
        self.resource_owners["dataset:" + path] = installation_id
        return self._after("dataset:" + path, path)

    def create_volume(self, path: str, installation_id: str) -> str:
        self._before("zvol:" + path)
        if path in self.datasets:
            raise OwnershipError("zvol already exists")
        self.datasets[path] = "VOLUME"
        self.owned_datasets.add(path)
        self.resources["disk"].add(path)
        self.resource_owners["zvol:" + path] = installation_id
        return self._after("zvol:" + path, path)

    def create_vm_record(self, name: str, disk: str, installation_id: str) -> int:
        self._before("vm:" + name)
        if any(vm["name"].casefold() == name.casefold() for vm in self.vms.values()):
            raise OwnershipError("duplicate VM name")
        if disk not in self.datasets or self.datasets[disk] != "VOLUME":
            raise OwnershipError("planned zvol is missing or has wrong type")
        identifier = self._next_id
        self._next_id += 1
        self.vms[identifier] = {"id": identifier, "name": name, "disk": disk}
        self.resource_owners["vm:" + name] = installation_id
        self.vm_ids[name] = identifier
        self.resources["vm"].add(name)
        return self._after("vm:" + name, identifier)

    def attach(self, name: str, kind: str, installation_id: str) -> int:
        vm_id = self.vm_ids.get(name)
        if vm_id is None:
            raise OwnershipError("VM is missing")
        key = f"device:{name}:{kind}"
        self._before(key)
        identifier = self._next_id
        self._next_id += 1
        attributes = {"dtype": kind.upper()}
        self.devices[identifier] = {
            "id": identifier, "vm_id": vm_id, "vm_name": name,
            "kind": kind, "attributes": attributes,
        }
        self.resource_owners[key] = installation_id
        return self._after(key, identifier)

    def inventory(self) -> dict[str, Any]:
        return {
            "datasets": dict(self.datasets),
            "vms": {key: dict(value) for key, value in self.vms.items()},
            "devices": {key: dict(value) for key, value in self.devices.items()},
            "owners": dict(self.resource_owners),
        }


def _manifest(path: Path) -> dict[str, Any]:
    try:
        ensure_trusted_directory_chain(path.parent, "ownership manifest")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o077
        ):
            raise OwnershipError("manifest must be a private, trusted regular file")
        data = json.loads(path.read_text(encoding="utf-8"))
    except OwnershipError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OwnershipError("manifest is unreadable or corrupted; no recovery action is permitted") from exc
    if not isinstance(data, dict) or data.get("format") != _FORMAT:
        raise OwnershipError("manifest format is invalid; no recovery action is permitted")
    if not isinstance(data.get("installation_id"), str) or not re.fullmatch(r"[0-9a-f-]{36}", data["installation_id"]):
        raise OwnershipError("manifest identity is invalid")
    if not isinstance(data.get("resources"), list) or not isinstance(data.get("request"), dict):
        raise OwnershipError("manifest resource plan is invalid")
    return data


def _save(path: Path, data: dict[str, Any], *, create=False) -> None:
    content = json.dumps(data, sort_keys=True, indent=2) + "\n"
    try:
        (write_private_new_file if create else atomic_replace_private_file)(path, content)
    except (OSError, FilesystemSafetyError) as exc:
        raise OwnershipError("private manifest write failed") from exc


def _resource_matches(inventory: dict[str, Any], manifest: dict[str, Any],
                      row: dict[str, Any]) -> bool:
    """Check the exact planned object and its relationships, not just its name."""
    kind, identifier = row.get("kind"), row.get("identifier")
    if not isinstance(identifier, str):
        return False
    if inventory["owners"].get(f"{kind}:{identifier}") != manifest["installation_id"]:
        return False
    datasets = manifest["datasets"]
    if kind == "dataset":
        allowed = {datasets["vm_dataset"]}
        if not manifest["inventory_before"]["shared_parent_exists"]:
            allowed.add(datasets["shared_parent"])
        return identifier in allowed and inventory["datasets"].get(identifier) == "FILESYSTEM"
    if kind == "zvol":
        return identifier == datasets["zvol"] and inventory["datasets"].get(identifier) == "VOLUME"
    api_id = row.get("api_id")
    if type(api_id) is not int or api_id <= 0:
        return False
    if kind == "vm":
        candidates = [vm for vm in inventory["vms"].values()
                      if vm.get("name", "").casefold() == manifest["vm_name"].casefold()]
        return (
            identifier == manifest["vm_name"] and len(candidates) == 1
            and candidates[0].get("id") == api_id
            and candidates[0].get("name") == identifier
            and candidates[0].get("disk") == datasets["zvol"]
        )
    if kind == "device":
        vm_name, separator, device_kind = identifier.rpartition(":")
        vm_id = manifest.get("vm_id")
        expected_attributes = {"dtype": device_kind.upper()}
        if (
            not separator or vm_name != manifest["vm_name"]
            or device_kind not in {"disk", "installer", "seed", "nic", "display"}
            or type(vm_id) is not int or vm_id <= 0
            or row.get("attributes") != expected_attributes
        ):
            return False
        vm_row = {"kind": "vm", "identifier": vm_name, "api_id": vm_id}
        if not _resource_matches(inventory, manifest, vm_row):
            return False
        candidates = [device for device in inventory["devices"].values()
                      if device.get("vm_name") == vm_name and device.get("kind") == device_kind]
        return (
            len(candidates) == 1 and candidates[0].get("id") == api_id
            and candidates[0].get("vm_id") == vm_id
            and candidates[0].get("attributes") == expected_attributes
        )
    return False


def rehearse_persistent(
    request: DesktopRequest,
    backend: PersistentFakeBackend,
    *, manifest_path: Path,
    confirmation: str,
) -> str:
    """Run the fake sequence, journaling intent before each resource creation."""
    if type(backend) is not PersistentFakeBackend:
        raise DeploymentRefused("persistent rehearsal accepts only its offline fake")
    if not isinstance(request.name, str) or not request.name:
        raise DeploymentRefused("invalid VM name")
    try:
        vm_name, zvol = resource_names(request)
    except ValueError as exc:
        raise DeploymentRefused("offline planner rejected the VM name") from exc
    backend.preflight(request)
    if any(vm["name"].casefold() == vm_name.casefold() for vm in backend.vms.values()):
        raise DeploymentRefused("duplicate VM name")
    if backend.exists("disk", zvol):
        raise DeploymentRefused("target zvol already exists")
    if confirmation != "CREATE":
        raise DeploymentRefused("exact CREATE confirmation required")

    vm_dataset = zvol.rpartition("/")[0]
    shared = vm_dataset.rpartition("/")[0]
    manifest = {
        "format": _FORMAT,
        "installation_id": str(uuid.uuid4()),
        "status": "preparing",
        "request": asdict(request),
        "vm_name": vm_name,
        "pool": request.pool,
        "datasets": {"shared_parent": shared, "vm_dataset": vm_dataset, "zvol": zvol},
        "vm_id": None,
        "devices": [],
        "resources": [],
        "pending": None,
        "inventory_before": {"shared_parent_exists": backend.exists("dataset", shared)},
    }
    path = Path(manifest_path)
    _save(path, manifest, create=True)

    def create(kind: str, identifier: str, call):
        manifest["pending"] = {"kind": kind, "identifier": identifier, "state": "intent_recorded"}
        _save(path, manifest)
        result = call()
        resource = {
            "kind": kind, "identifier": identifier,
            "created_by_installation": True, "verified": True,
            "api_id": result if type(result) is int else None,
        }
        if kind == "device":
            resource["attributes"] = {"dtype": identifier.rsplit(":", 1)[-1].upper()}
        if kind in {"dataset", "zvol"} and result != identifier:
            raise OwnershipError("created dataset identifier does not match the plan")
        if not _resource_matches(backend.inventory(), manifest, resource):
            raise OwnershipError("created resource failed exact inventory read-back")
        manifest["resources"].append(resource)
        manifest["pending"] = None
        if kind == "vm":
            manifest["vm_id"] = result
        if kind == "device":
            manifest["devices"].append({
                "kind": identifier.rsplit(":", 1)[-1], "id": result,
                "vm_id": manifest["vm_id"], "attributes": resource["attributes"],
            })
        _save(path, manifest)
        return result

    try:
        if not manifest["inventory_before"]["shared_parent_exists"]:
            create("dataset", shared, lambda: backend.create_parent(shared, manifest["installation_id"]))
        create("dataset", vm_dataset, lambda: backend.create_parent(vm_dataset, manifest["installation_id"]))
        create("zvol", zvol, lambda: backend.create_volume(zvol, manifest["installation_id"]))
        create("vm", vm_name, lambda: backend.create_vm_record(vm_name, zvol, manifest["installation_id"]))
        for kind in ("disk", "installer", "seed", "nic", "display"):
            create("device", f"{vm_name}:{kind}", lambda kind=kind: backend.attach(vm_name, kind, manifest["installation_id"]))
        manifest["status"] = "mock_complete_not_installed"
        _save(path, manifest)
    except Exception as exc:
        manifest["status"] = "reconcile_required"
        manifest["failure"] = type(exc).__name__
        try:
            _save(path, manifest)
        except OwnershipError:
            pass
        raise OwnershipError("partial/ambiguous creation retained; inspect recovery report; no retry or deletion") from exc
    return manifest["installation_id"]


def inspect_recovery(manifest_path: Path, backend: PersistentFakeBackend) -> RecoveryReport:
    """Read and reconcile one manifest against fake inventory without mutations."""
    if type(backend) is not PersistentFakeBackend:
        raise OwnershipError("recovery inspection accepts only the offline fake inventory")
    data = _manifest(Path(manifest_path))
    req = data["request"]
    try:
        planned_request = DesktopRequest(**req)
        vm_name, zvol = resource_names(planned_request)
    except (TypeError, ValueError) as exc:
        raise OwnershipError("manifest request identity is invalid") from exc
    vm_dataset = zvol.rpartition("/")[0]
    shared_parent = vm_dataset.rpartition("/")[0]
    if (
        req.get("name") != data.get("vm_name")
        or req.get("pool") != data.get("pool")
        or data.get("datasets") != {
            "shared_parent": shared_parent, "vm_dataset": vm_dataset, "zvol": zvol,
        }
        or data.get("status") not in {"preparing", "reconcile_required", "mock_complete_not_installed"}
        or not isinstance(data.get("inventory_before"), dict)
        or type(data["inventory_before"].get("shared_parent_exists")) is not bool
    ):
        raise OwnershipError("manifest identity or resource plan does not match the original request")
    inventory = backend.inventory()
    observed: dict[str, bool] = {}
    attention: list[str] = []
    resources = []
    for row in data["resources"]:
        if not isinstance(row, dict) or not isinstance(row.get("identifier"), str):
            raise OwnershipError("manifest contains malformed ownership data")
        kind, identifier = row.get("kind"), row["identifier"]
        if kind in {"dataset", "zvol"}:
            exists = identifier in inventory["datasets"]
        elif kind == "vm":
            exists = any(v["name"].casefold() == identifier.casefold()
                         for v in inventory["vms"].values())
        elif kind == "device":
            exists = any(f"{d['vm_name']}:{d['kind']}" == identifier
                         for d in inventory["devices"].values())
        else:
            raise OwnershipError("manifest contains an unsupported resource type")
        matches = _resource_matches(inventory, data, row)
        observed[identifier] = exists
        resources.append({"kind": kind, "identifier": identifier, "exists": exists, "owned": bool(row.get("created_by_installation") is True and matches), "verified": matches})
        if not matches:
            attention.append(f"owned resource missing, changed, or identity mismatch: {kind}:{identifier}")

    pending = data.get("pending")
    if pending is not None:
        identifier = pending.get("identifier") if isinstance(pending, dict) else None
        exists = isinstance(identifier, str) and (
            identifier in inventory["datasets"]
            or any(v["name"].casefold() == identifier.casefold() for v in inventory["vms"].values())
            or any(f"{d['vm_name']}:{d['kind']}" == identifier for d in inventory["devices"].values())
        )
        resources.append({"kind": pending.get("kind", "unknown") if isinstance(pending, dict) else "unknown", "identifier": identifier, "exists": exists, "owned": False, "verified": False})
        attention.append("operation outcome is ambiguous; pending resource requires human review")

    tracked_device_ids = {
        row.get("api_id") for row in data["resources"]
        if isinstance(row, dict) and row.get("kind") == "device" and type(row.get("api_id")) is int
    }
    vm_id = data.get("vm_id")
    if type(vm_id) is int:
        for device in inventory["devices"].values():
            if device.get("vm_id") == vm_id and device.get("id") not in tracked_device_ids:
                attention.append("untracked attached device requires human review")
                resources.append({
                    "kind": "device", "identifier": f"{device.get('vm_name')}:{device.get('kind')}",
                    "exists": True, "owned": False, "verified": False,
                })

    shared = data["datasets"].get("shared_parent")
    if data["inventory_before"].get("shared_parent_exists"):
        resources.append({"kind": "dataset", "identifier": shared, "exists": shared in inventory["datasets"], "owned": False, "verified": inventory["datasets"].get(shared) == "FILESYSTEM"})
    for identifier in (data["datasets"].get("vm_dataset"), data["datasets"].get("zvol")):
        if identifier in inventory["datasets"] and identifier not in observed:
            attention.append("untracked resource at planned path requires human review: " + identifier)
            resources.append({"kind": "dataset", "identifier": identifier, "exists": True, "owned": False, "verified": False})
    for vm in inventory["vms"].values():
        if vm["name"].casefold() == data["vm_name"].casefold() and not any(r["kind"] == "vm" and r["identifier"] == vm["name"] and r["owned"] for r in resources):
            attention.append("unexplained VM name collision requires human review")
    if data["status"] != "mock_complete_not_installed" and not attention:
        attention.append("incomplete installation is never auto-resumed; human review required")
    return RecoveryReport(
        installation_id=data["installation_id"], status=data["status"],
        resources=tuple(resources), attention=tuple(sorted(set(attention))),
    )
