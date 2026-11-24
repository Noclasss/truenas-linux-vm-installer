"""Read-only inspection helpers for the create-only writer's manifest format."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from .installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
from .installer_core.schema import SchemaCheckedClient
from .installer_core.safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain
from .readonly_preflight import READ_ONLY_METHODS

FORMAT = "truenas-linux-desktop-create-only-v1"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ManifestInspectionError(RuntimeError):
    """The manifest is unsafe, malformed, or incompatible with inspection."""


def make_target_key(hostname: str, build: str, pool_name: str, vm_name: str) -> str:
    """Stable host/resource identity; excludes mutable capacity and file hashes."""
    identity = {
        "hostname": hostname,
        "build": build,
        "pool_name": pool_name,
        "vm_name": vm_name,
        "vm_dataset": f"{pool_name}/linux-vms/{vm_name}",
        "zvol": f"{pool_name}/linux-vms/{vm_name}/disk0",
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_writer_manifest(path: Path) -> dict[str, Any]:
    """Read one private writer-v1 manifest without invoking middleware."""
    path = Path(path)
    try:
        ensure_trusted_directory_chain(path.parent, "Desktop operation manifest")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_mode & 0o077
            or info.st_size > _MAX_MANIFEST_BYTES
        ):
            raise ManifestInspectionError("manifest must be a bounded private regular file")
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except ManifestInspectionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, FilesystemSafetyError) as exc:
        raise ManifestInspectionError("manifest is unreadable or malformed; no recovery action is permitted") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ManifestInspectionError("manifest format is not the supported create-only writer format")
    if (
        not isinstance(data.get("plan"), dict)
        or not isinstance(data.get("resources"), list)
        or not isinstance(data.get("files"), list)
        or data.get("status") not in {"preparing", "reconcile_required", "created_not_started_live_unverified"}
        or (data.get("pending") is not None and not isinstance(data.get("pending"), dict))
    ):
        raise ManifestInspectionError("manifest structure is incomplete; no recovery action is permitted")
    return data


def find_prior_target_manifest(operations_root: Path, target_key: str, pool_name: str, vm_name: str) -> Path | None:
    """Fail closed if this target already has any writer-v1 operation record.

    Pool/name matching also catches manifests written before target_key was
    added. The root is dedicated to operation records; an unreadable or
    unfamiliar JSON record makes absence impossible to establish safely.
    """
    root = Path(operations_root)
    if not root.exists():
        return None
    try:
        ensure_trusted_directory_chain(root, "Desktop operation directory")
        entries = sorted(root.iterdir())
    except (OSError, FilesystemSafetyError) as exc:
        raise ManifestInspectionError("operation directory cannot be inspected safely") from exc
    for entry in entries:
        if entry.suffix != ".json":
            continue
        data = read_writer_manifest(entry)
        plan = data["plan"]
        same_key = data.get("target_key") == target_key
        legacy_same_target = plan.get("pool") == pool_name and plan.get("vm") == vm_name
        if same_key or legacy_same_target:
            return entry
    return None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _safe_resource(row: Any) -> Any:
    if not isinstance(row, dict):
        return None
    result = dict(row)
    attributes = result.get("attributes")
    if isinstance(attributes, dict):
        result["attributes"] = {key: value for key, value in attributes.items() if key != "password"}
    return result


def _inventory_checks(pending: dict[str, Any] | None, plan: dict[str, Any], vm_id: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [
        {"method": "system.version", "purpose": "confirm exact host build before interpreting query fields"},
        {"method": "core.get_methods", "purpose": "confirm read-only query contracts match the reviewed fixture"},
    ]
    method = pending.get("method") if pending else None
    if method in {"vm.create", "vm.device.create"} or type(vm_id) is int:
        checks.extend([
            {"method": "vm.query", "purpose": "match the recorded VM ID/name, requested settings, autostart=false, and stopped state"},
            {"method": "vm.device.query", "purpose": "match returned device ID, exact VM association, dtype, attributes, and unexpected devices"},
        ])
    if method == "filesystem.stage":
        checks.append({
            "method": "local-filesystem.stat-and-hash",
            "path": pending.get("identifier") if pending else None,
            "purpose": "inspect the exact staged file's type, size, digest, owner, mode, and service-account readability; do not write it",
        })
    if method == "pool.dataset.create" or plan.get("vm_dataset") or plan.get("zvol"):
        checks.append({"method": "pool.dataset.query", "purpose": "match exact filesystem/zvol paths, types, mountpoint, and requested volume size"})
    return checks


def inspect_manifest(path: Path) -> dict[str, Any]:
    """Summarize recorded intent and IDs, without claiming fresh inventory."""
    data = read_writer_manifest(path)
    pending = data.get("pending")
    vm_id = pending.get("vm_id") if isinstance(pending, dict) else None
    if type(vm_id) is not int:
        vm_id = data.get("vm_id")
    if type(vm_id) is not int and isinstance(pending, dict) and pending.get("method") == "vm.create":
        candidate = pending.get("api_id")
        vm_id = candidate if type(candidate) is int else None
    last = next((_safe_resource(row) for row in reversed(data["resources"]) if isinstance(row, dict)), None)
    safe_pending = _safe_resource(pending)
    plan = data["plan"]
    blocked = True  # any prior run for this target must be reconciled, never restarted as fresh
    return {
        "manifest_path": os.fspath(Path(path)),
        "manifest_recorded": {
            "format": data["format"],
            "status": data["status"],
            "run_id": data.get("run_id"),
            "target_key": data.get("target_key"),
            "plan": {key: plan.get(key) for key in (
                "build", "host", "vm", "pool", "vm_dataset", "zvol",
                "vcpus", "memory_mib", "disk_gib", "autostart", "source_id",
                "network", "iso_sha256", "seed_sha256",
            ) if key in plan},
            "last_confirmed_resource": last,
            "pending_operation": safe_pending,
            "returned_api_id": pending.get("api_id") if isinstance(pending, dict) else None,
            "associated_vm_id": vm_id,
            "confirmed_resource_count": len(data["resources"]),
            "confirmed_file_count": len(data["files"]),
            "automatic_retry": data.get("policy", {}).get("automatic_retry"),
            "rollback": data.get("policy", {}).get("rollback"),
            "deletion": data.get("policy", {}).get("deletion"),
            "vm_start": data.get("policy", {}).get("vm_start"),
        },
        "fresh_inventory": {"performed": False, "confirms": [], "unknown_until_read": True},
        "inventory_checks_needed": _inventory_checks(pending, plan, vm_id),
        "second_create_blocked_by_manifest_state": blocked,
        "inspection_policy": "read-only only; no retry, resume, rollback, deletion, or start",
    }


class _CaptureClient:
    def __init__(self, client: Any):
        self.client = client
        self.methods: dict[str, Any] | None = None

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        result = self.client.call(method, [] if params is None else params)
        if method == "core.get_methods" and isinstance(result, dict):
            self.methods = result
        return result


def _safe_vm_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row.get(key) for key in ("id", "name", "memory", "cores", "vcpus", "threads", "autostart") if key in row}
    status = row.get("status")
    if isinstance(status, dict) and "domain_state" in status:
        result["status"] = {"domain_state": status.get("domain_state")}
    return result


def _safe_device_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row.get(key) for key in ("id", "vm", "order") if key in row}
    attrs = row.get("attributes")
    if isinstance(attrs, dict):
        allowed = {"dtype", "path", "exists", "boot", "type", "bind", "web", "wait", "resolution", "port", "web_port"}
        result["attributes"] = {key: value for key, value in attrs.items() if key in allowed}
    return result


def _safe_dataset_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row.get(key) for key in ("id", "type", "mountpoint") if key in row}
    size = row.get("volsize")
    if isinstance(size, dict):
        result["volsize"] = {key: size[key] for key in ("parsed", "rawvalue") if key in size}
    return result


def inspect_live_inventory(path: Path, client: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    """Add fresh, bounded, allowlisted inventory reads; never expose raw rows."""
    data = read_writer_manifest(path)
    capture = _CaptureClient(client)
    try:
        compatibility = verify_runtime_compatibility(capture, fixture)
    except CompatibilityError as exc:
        raise ManifestInspectionError("live inspection refused: host build/schema differs from the reviewed fixture") from exc
    if not isinstance(capture.methods, dict):
        raise ManifestInspectionError("live inspection did not capture reviewed methods")
    reader = SchemaCheckedClient(client, methods=capture.methods, allowed_read_methods=set(READ_ONLY_METHODS), allowed_write_methods=set())
    # Import locally to keep only the established query allowlist in this module.
    from .readonly_preflight import _read, _query_rows
    methods = capture.methods
    system_info = _read(reader, methods, "system.info", [])
    datasets = _query_rows(_read(reader, methods, "pool.dataset.query", [[], {"select": ["id", "type", "mountpoint", "volsize"]}]), "pool.dataset.query")
    vm_fields = ["id", "name", "memory", "cores", "vcpus", "threads", "autostart", "devices", "status"]
    vms = _query_rows(_read(reader, methods, "vm.query", [[], {"select": vm_fields}]), "vm.query")
    devices = _query_rows(_read(reader, methods, "vm.device.query", [[], {"select": ["id", "vm", "attributes"]}]), "vm.device.query")
    for row in devices:
        attrs = row.get("attributes")
        if isinstance(attrs, dict):
            row["attributes"] = {key: value for key, value in attrs.items() if key != "password"}
    plan = data["plan"]
    pending = data.get("pending")
    vm_id = pending.get("vm_id") if isinstance(pending, dict) else None
    if type(vm_id) is not int:
        vm_id = data.get("vm_id")
    if type(vm_id) is not int and isinstance(pending, dict) and pending.get("method") == "vm.create":
        vm_id = pending.get("api_id")
    pending_id = pending.get("api_id") if isinstance(pending, dict) else None
    vm_rows = [row for row in vms if (type(vm_id) is int and row.get("id") == vm_id) or row.get("name") == plan.get("vm")]
    device_rows = [row for row in devices if (type(pending_id) is int and row.get("id") == pending_id) or (type(vm_id) is int and row.get("vm") == vm_id)]
    target_ids = {value for value in (plan.get("vm_dataset"), plan.get("zvol")) if isinstance(value, str)}
    dataset_rows = [row for row in datasets if row.get("id") in target_ids or row.get("id") == f"{plan.get('pool')}/linux-vms"]
    return {
        "performed": True,
        "build": compatibility.truenas_version,
        "hostname": system_info.get("hostname") if isinstance(system_info, dict) else None,
        "confirms": {
            "vm_rows_by_recorded_id_or_name": [_safe_vm_row(row) for row in vm_rows],
            "device_rows_by_returned_id_or_vm_id": [_safe_device_row(row) for row in device_rows],
            "dataset_rows_at_exact_target_paths": [_safe_dataset_row(row) for row in dataset_rows],
        },
        "query_methods": ["system.version", "core.get_methods", "system.info", "pool.dataset.query", "vm.query", "vm.device.query"],
        "write_methods_called": [],
        "interpretation": "fresh query results are distinct from manifest-recorded IDs/confirmations",
    }


def inspect(path: Path, *, client: Any | None = None, fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    report = inspect_manifest(path)
    if client is not None:
        if fixture is None:
            raise ManifestInspectionError("reviewed fixture is required for fresh inventory reads")
        report["fresh_inventory"] = inspect_live_inventory(path, client, fixture)
    return report
