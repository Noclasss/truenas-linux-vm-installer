"""Offline planning only: no network, subprocess, host probes or resource writes."""

import argparse
import json
import re
import sys
from pathlib import Path

if __package__:
    from .ubuntu_server import artifact_metadata
else:
    from ubuntu_server import artifact_metadata


PROFILES = ("ubuntu-server", "ubuntu-desktop", "debian-server", "debian-desktop")
PROFILE_LABELS = {
    "ubuntu-server": "Ubuntu Server",
    "ubuntu-desktop": "Ubuntu Desktop",
    "debian-server": "Debian Server",
    "debian-desktop": "Debian Desktop",
}
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


def _terminal_input(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    answer = sys.stdin.readline()
    if answer == "":
        raise EOFError
    return answer.rstrip("\r\n")


def _prompt_choice(options, label, input_fn, output, formatter=lambda value: str(value)):
    options = tuple(options)
    if not options:
        raise ValidationError(f"no options available for {label}")
    output.write(f"{label}:\n")
    for index, value in enumerate(options, start=1):
        output.write(f"  {index}) {formatter(value)}\n")
    while True:
        answer = input_fn(f"Choose {label} [1-{len(options)}]: ").strip()
        if answer.isascii() and answer.isdecimal():
            selected = int(answer)
            if 1 <= selected <= len(options):
                return options[selected - 1]
        output.write("Enter one of the listed numbers.\n")


def _prompt_integer(label, default, low, high, input_fn, output):
    while True:
        answer = input_fn(f"{label} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isascii() and answer.isdecimal():
            value = int(answer)
            if low <= value <= high:
                return value
        output.write(f"Enter a whole number from {low} to {high}.\n")


def collect_interactive_config(
    inventory=None,
    *,
    profile_override=None,
    input_fn=None,
    output=None,
):
    """Collect a preview configuration; this function performs no host I/O."""
    if input_fn is None:
        input_fn = _terminal_input
    if output is None:
        output = sys.stderr
    if profile_override is None:
        profile = _prompt_choice(
            PROFILES,
            "guest profile (all profiles are experimental)",
            input_fn,
            output,
            lambda value: PROFILE_LABELS[value],
        )
    elif profile_override in PROFILES:
        profile = profile_override
    else:
        raise ValidationError("profile override is not a known experimental profile")

    name = input_fn("VM name [linux-demo]: ").strip() or "linux-demo"
    if inventory is None:
        output.write("No inventory supplied; pool and bridge selections will remain unchecked.\n")
        pool = input_fn("Existing non-boot storage pool (not a physical drive): ").strip()
        bridge = input_fn("Existing network bridge (host networking will not be changed): ").strip()
    else:
        validate_inventory(inventory)
        pools = tuple(sorted(
            name for name in inventory["pools"]
            if name.casefold() not in {"boot-pool", "freenas-boot"}
        ))
        if not pools:
            raise ValidationError("inventory has no eligible non-boot storage pool")
        pool = _prompt_choice(
            pools,
            "storage pool for this VM (a pool, not an individual physical drive)",
            input_fn,
            output,
            lambda value: f"{value} — available_bytes={inventory['pools'][value]['available_bytes']}",
        )
        bridges = tuple(sorted(inventory["bridges"]))
        bridge = _prompt_choice(
            bridges,
            "existing network bridge (no host-network changes)",
            input_fn,
            output,
        )

    return {
        "profile": profile,
        "name": name,
        "pool": pool,
        "bridge": bridge,
        "vcpus": _prompt_integer("Total vCPUs", 2, 1, 128, input_fn, output),
        "memory_mib": _prompt_integer("RAM MiB", 4096, 1024, 1048576, input_fn, output),
        "disk_gib": _prompt_integer("VM virtual-disk size GiB", 32, 16, 65536, input_fn, output),
    }


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
    if config["profile"] == "ubuntu-server":
        warnings = [
            "Ubuntu Server has a pinned artifact and offline NoCloud seed helper; preview does not download the image or generate seed media.",
            "Image format is verified as qcow2 with qemu-img; import/conversion and live installation are not implemented.",
            "No live TrueNAS schema, CPU, RAM, network or storage checks were performed.",
            "This plan cannot be applied. A future installer must recheck live state immediately before creating resources.",
        ]
    else:
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
    plan = {
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
        "network": {
            "existing_bridge": config["bridge"],
            "modify_host_network": False,
            "guest_ip_configuration": "not configured or validated by the offline planner",
        },
        "inventory_checked": inventory is not None,
        "warnings": warnings,
    }
    if config["profile"] == "ubuntu-server":
        plan["guest_bootstrap"] = {
            "artifact": artifact_metadata(),
            "provisioning": {
                "method": "cloud-init NoCloud",
                "seed_builder": "cloud-localds",
                "seed_files": ["user-data", "meta-data"],
                "datasource_mode": "local",
                "media_format": "raw image with ISO9660 filesystem",
                "media_label": "CIDATA",
                "device_type": "CDROM",
                "device_path_attribute": "path",
                "device_evidence": (
                    "TrueNAS-26.0.0-BETA.2 accepted-request schema-only evidence; "
                    "write acceptance has not been tested"
                ),
                "ssh_public_keys_required": True,
                "ssh_password_auth": False,
                "root_login": False,
            },
        }
    return plan


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


def main(argv=None, *, input_fn=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", help="local JSON VM settings")
    mode.add_argument("--interactive", action="store_true", help="collect choices and print an offline plan")
    parser.add_argument("--inventory", help="optional normalized local inventory JSON")
    parser.add_argument("--profile", choices=PROFILES, help="explicit profile override")
    args = parser.parse_args(argv)
    try:
        inventory = read_json(args.inventory) if args.inventory else None
        if args.interactive:
            config = collect_interactive_config(
                inventory,
                profile_override=args.profile,
                input_fn=input_fn,
                output=sys.stderr,
            )
        else:
            config = read_json(args.config)
            if args.profile is not None:
                if not isinstance(config, dict):
                    raise ValidationError("config must be a JSON object")
                config = dict(config)
                config["profile"] = args.profile
        plan = build_plan(config, inventory)
    except EOFError:
        print("Input ended; no plan produced.", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
