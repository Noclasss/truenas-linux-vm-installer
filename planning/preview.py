"""Offline planning only: no network, subprocess, host probes or resource writes."""

import argparse
import json
import re
import sys
from pathlib import Path


PROFILES = ("ubuntu-server", "ubuntu-desktop", "debian-server", "debian-desktop")
CONFIG_FIELDS = {"profile", "name", "pool", "bridge", "vcpus", "memory_mib", "disk_gib"}
INVENTORY_FIELDS = {"vm_names", "datasets", "bridges", "pools"}
COMPONENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")


class ValidationError(ValueError):
    """Invalid input; a plan must not be produced."""


def object_fields(value, required, label):
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a JSON object")
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing or unknown:
        raise ValidationError(
            f"{label}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}"
        )


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{label} must be an integer from {low} to {high}")


def component(value, label):
    if not isinstance(value, str) or not COMPONENT.fullmatch(value):
        raise ValidationError(
            f"{label} must start with a letter and contain 1–63 letters, digits, _ or -"
        )


def string_list(value, label):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ):
        raise ValidationError(f"{label} must be a list of non-empty, unpadded strings")
    if len(value) != len(set(value)):
        raise ValidationError(f"{label} contains duplicate entries")


def validate_inventory(inventory):
    object_fields(inventory, INVENTORY_FIELDS, "inventory")
    for key in ("vm_names", "datasets", "bridges"):
        string_list(inventory[key], f"inventory.{key}")
    if not isinstance(inventory["pools"], dict):
        raise ValidationError("inventory.pools must be an object")
    for name, pool in inventory["pools"].items():
        component(name, "inventory pool name")
        object_fields(pool, {"available_bytes"}, f"pool {name}")
        integer(pool["available_bytes"], 0, 2**63 - 1, f"pool {name}.available_bytes")


def build_plan(config, inventory=None):
    object_fields(config, CONFIG_FIELDS, "config")
    if not isinstance(config["profile"], str) or config["profile"] not in PROFILES:
        raise ValidationError(f"profile must be one of {', '.join(PROFILES)}")
    for key in ("name", "pool", "bridge"):
        component(config[key], key)
    if config["pool"].casefold() in {"boot-pool", "freenas-boot"}:
        raise ValidationError("the boot pool cannot be a VM storage target")
    # Conservative planning bounds, not distribution or TrueNAS support claims.
    integer(config["vcpus"], 1, 128, "vcpus")
    integer(config["memory_mib"], 1024, 1048576, "memory_mib")
    integer(config["disk_gib"], 16, 65536, "disk_gib")
    parent = f"{config['pool']}/linux-vms"
    dataset = f"{parent}/{config['name']}"
    disk_bytes = config["disk_gib"] * 1024**3
    warnings = [
        "Experimental profile: image selection, integrity verification and guest provisioning are not implemented.",
        "No live TrueNAS schema, CPU, RAM, network or storage checks were performed.",
        "This plan cannot be applied. A future installer must recheck live state immediately before creating resources.",
    ]
    if inventory is None:
        warnings.append("No inventory supplied: names, storage capacity and bridge existence are unchecked.")
    else:
        validate_inventory(inventory)
        if config["name"].casefold() in {n.casefold() for n in inventory["vm_names"]}:
            raise ValidationError("a VM with this name already exists in the supplied inventory")
        for existing in inventory["datasets"]:
            if existing == dataset or existing.startswith(dataset + "/"):
                raise ValidationError("the proposed storage path already exists or has children")
        # Parent datasets may exist, but their writability/type is a live-only check.
        if config["pool"] not in inventory["pools"]:
            raise ValidationError("the selected pool is absent from the supplied inventory")
        if config["bridge"] not in inventory["bridges"]:
            raise ValidationError("the selected bridge is absent from the supplied inventory")
        if inventory["pools"][config["pool"]]["available_bytes"] < disk_bytes:
            raise ValidationError("insufficient space for the proposed disk in the supplied inventory")
        warnings.append("Inventory is user-supplied and may be stale or incomplete; free space does not include installation overhead.")
    return {
        "schema_version": 1,
        "mode": "offline-preview",
        "apply_supported": False,
        "profile": {"id": config["profile"], "status": "experimental"},
        "vm": {
            "name": config["name"],
            "cpu": {"total_vcpus": config["vcpus"], "sockets": 1, "cores": config["vcpus"], "threads": 1},
            "memory_mib": config["memory_mib"],
            "start": False,
            "autostart": False,
        },
        "storage": {"dataset": dataset, "disk": dataset + "/disk0", "disk_bytes": disk_bytes},
        "network": {"existing_bridge": config["bridge"], "modify_host_network": False},
        "inventory_checked": inventory is not None,
        "warnings": warnings,
    }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    # Read only explicitly supplied files. Never echo their contents on errors.
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    except (OSError, UnicodeError) as exc:
        raise ValidationError("cannot read the supplied file as UTF-8 JSON") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="local JSON VM settings")
    parser.add_argument("--inventory", help="optional normalized local inventory JSON")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(read_json(args.config), read_json(args.inventory) if args.inventory else None)
    except ValidationError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
