"""Guided, exact-build TrueNAS preflight for Ubuntu Desktop; no writes."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import re
import sys
from typing import Any, Callable

try:
    from .installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from .installer_core.middleware import MiddlewareError, MidcltClient
    from .installer_core.schema import SchemaCheckedClient
    from .readonly_preflight import (
        READ_ONLY_METHODS,
        PreflightError,
        ReadOnlyPreflightRequest,
        _COMPONENT,
        _GIB,
        _MIB,
        _VM_NAME,
        _query_rows,
        _read,
        _validate_desktop_request_shapes,
        _validate_request,
        run_read_only_preflight,
    )
    from .ubuntu_desktop import STAGING_RESERVE_BYTES
    from .verify_compatibility import DEFAULT_FIXTURE, load_fixture
except ImportError:  # direct execution as ``python planning/desktop_readonly_preview.py``
    from installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from installer_core.middleware import MiddlewareError, MidcltClient
    from installer_core.schema import SchemaCheckedClient
    from readonly_preflight import (
        READ_ONLY_METHODS,
        PreflightError,
        ReadOnlyPreflightRequest,
        _COMPONENT,
        _GIB,
        _MIB,
        _VM_NAME,
        _query_rows,
        _read,
        _validate_desktop_request_shapes,
        _validate_request,
        run_read_only_preflight,
    )
    from ubuntu_desktop import STAGING_RESERVE_BYTES
    from verify_compatibility import DEFAULT_FIXTURE, load_fixture

DEFAULT_VCPUS = 2
DEFAULT_MEMORY_MIB = 6144
DEFAULT_DISK_GIB = 32


@dataclass
class ResourceSnapshot:
    truenas_version: str | None = None
    checked_method_contracts: int | None = None
    pool_free_bytes: int | None = None
    pool_healthy: bool | None = None
    physical_memory_bytes: int | None = None
    available_memory_bytes: int | None = None
    gate_error: str | None = None
    errors: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)


def _validate_unit_evidence(fixture: dict[str, Any]) -> None:
    semantics = fixture.get("method_semantics")
    for field_name, unit in (
        ("pool.query.free", "bytes"),
        ("vm.create.memory", "MiB"),
        ("vm.get_available_memory.result", "bytes"),
        ("system.info.physmem", "bytes"),
    ):
        evidence = semantics.get(field_name) if isinstance(semantics, dict) else None
        if (
            not isinstance(evidence, dict)
            or evidence.get("truenas_version") != fixture.get("truenas_version")
            or evidence.get("unit") != unit
        ):
            raise PreflightError(f"{field_name} unit is not verified for this TrueNAS build; refusing to compare")


def _snapshot(client: Any, fixture: dict[str, Any], request: ReadOnlyPreflightRequest) -> ResourceSnapshot:
    result = ResourceSnapshot()
    try:
        _validate_request(request)
        methods = fixture.get("methods")
        if not isinstance(methods, dict):
            raise PreflightError("reviewed method fixture is malformed")
        _validate_desktop_request_shapes(methods, request)
        _validate_unit_evidence(fixture)
        compatibility = verify_runtime_compatibility(client, fixture)
    except (CompatibilityError, PreflightError, MiddlewareError, OSError, ValueError) as exc:
        result.gate_error = str(exc)
        result.unchecked.append("pool and RAM measurements; exact build/schema gate did not pass")
        return result

    result.truenas_version = compatibility.truenas_version
    result.checked_method_contracts = len(compatibility.checked_methods)
    reader = SchemaCheckedClient(client, allowed_read_methods=READ_ONLY_METHODS)
    try:
        pools = _query_rows(_read(reader, methods, "pool.query", [[], {
            "select": ["name", "healthy", "warning", "status", "free"],
        }]), "pool.query")
        selected = [row for row in pools if row.get("name") == request.pool_name]
        if len(selected) != 1:
            raise PreflightError("selected pool was not found exactly once")
        pool = selected[0]
        result.pool_healthy = (
            pool.get("healthy") is True
            and pool.get("warning") is False
            and pool.get("status") == "ONLINE"
        )
        if not result.pool_healthy:
            result.errors.append("selected pool is unhealthy, warning, or not ONLINE")
        free = pool.get("free")
        if type(free) is not int or free < 0:
            result.errors.append("selected pool did not report a valid free-space byte count")
        else:
            result.pool_free_bytes = free
    except (PreflightError, MiddlewareError, OSError, ValueError) as exc:
        result.errors.append(str(exc))

    try:
        system_info = _read(reader, methods, "system.info", [])
    except (PreflightError, MiddlewareError, OSError, ValueError) as exc:
        result.errors.append(str(exc))
        result.unchecked.append("available RAM (total physical RAM could not be verified)")
        return result
    physical = system_info.get("physmem") if isinstance(system_info, dict) else None
    if type(physical) is not int or physical <= 0:
        result.errors.append("host returned an invalid total physical-memory byte count")
        result.unchecked.append("available RAM (query stopped because physical RAM was invalid)")
        return result
    result.physical_memory_bytes = physical

    try:
        available = _read(reader, methods, "vm.get_available_memory", [False])
    except (PreflightError, MiddlewareError, OSError, ValueError) as exc:
        result.errors.append(str(exc))
        return result
    if type(available) is not int or available < 0:
        result.errors.append("host returned an invalid available-memory value")
    else:
        result.available_memory_bytes = available
    return result


def _make_request(
    vm_name: str,
    pool_name: str,
    vcpus: int,
    memory_mib: int,
    disk_gib: int,
    host_reserve_mib: int,
    pool_reserve_mib: int,
) -> ReadOnlyPreflightRequest:
    return ReadOnlyPreflightRequest(
        profile="ubuntu-desktop",
        vm_name=vm_name,
        pool_name=pool_name,
        bridge_name=None,
        vcpus=vcpus,
        memory_mib=memory_mib,
        disk_gib=disk_gib,
        host_reserve_mib=host_reserve_mib,
        pool_reserve_mib=pool_reserve_mib,
        staging_reserve_bytes=STAGING_RESERVE_BYTES,
    )


def _ask_text(label: str, pattern: re.Pattern[str], input_fn: Callable[[str], str]) -> str:
    while True:
        value = input_fn(label).strip()
        if pattern.fullmatch(value):
            return value
        print("Enter a valid single-component name; use ASCII letters/digits (pool names may also use _ or -).")


def _ask_int(
    label: str,
    default: int | None,
    low: int,
    high: int,
    input_fn: Callable[[str], str],
) -> int:
    prompt = f"{label} [{default}]: " if default is not None else f"{label}: "
    while True:
        value = input_fn(prompt).strip()
        if not value and default is not None:
            return default
        if re.fullmatch(r"[0-9]+", value):
            number = int(value)
            if low <= number <= high:
                return number
        print(f"Enter a whole number from {low} to {high}.")


def _collect_settings(input_fn: Callable[[str], str]) -> dict[str, Any]:
    while True:
        vm_name = _ask_text("VM name (required): ", _VM_NAME, input_fn)
        pool_name = _ask_text("Storage pool (required; no automatic selection): ", _COMPONENT, input_fn)
        if pool_name.casefold() not in {"boot-pool", "freenas-boot"}:
            break
        print("Boot pools are not eligible VM storage targets; choose another pool.")
    return {
        "vm_name": vm_name,
        "pool_name": pool_name,
        "vcpus": _ask_int("vCPUs", DEFAULT_VCPUS, 2, 128, input_fn),
        "memory_mib": _ask_int("RAM MiB", DEFAULT_MEMORY_MIB, 6144, 1_048_576, input_fn),
        "disk_gib": _ask_int("VM virtual disk GiB", DEFAULT_DISK_GIB, 25, 65_536, input_fn),
    }


def _bytes_text(value: int | None) -> str:
    if value is None:
        return "unchecked"
    return f"{value:,} bytes ({value / _GIB:.2f} GiB)"


def _headroom(measured: int | None, required: int) -> str:
    return "unchecked" if measured is None else _bytes_text(measured - required)


def _print_proposal(settings: dict[str, Any], output: Callable[[str], None]) -> None:
    output("Proposed Ubuntu Desktop VM (first create; no NIC):")
    output(f"  VM name={settings['vm_name']}; pool={settings['pool_name']}")
    output(
        f"  vCPUs={settings['vcpus']}; RAM={settings['memory_mib']} MiB; "
        f"virtual disk={settings['disk_gib']} GiB; autostart=false; start=false"
    )


def _print_raw_headroom(
    snapshot: ResourceSnapshot,
    settings: dict[str, Any],
    output: Callable[[str], None],
) -> None:
    output(
        f"Initial exact build/schema gate: PASS for {snapshot.truenas_version}; "
        f"matched {snapshot.checked_method_contracts} reviewed method contracts."
    )
    output("Measured headroom before caller-selected reserves (point-in-time, not reserved):")
    ram_bytes = settings["memory_mib"] * _MIB
    pool_bytes = settings["disk_gib"] * _GIB + STAGING_RESERVE_BYTES
    output(
        f"  RAM: physical={_headroom(snapshot.physical_memory_bytes, ram_bytes)}; "
        f"available={_headroom(snapshot.available_memory_bytes, ram_bytes)}"
    )
    output(
        f"  Pool: free={_bytes_text(snapshot.pool_free_bytes)}; "
        f"after virtual disk + pinned ISO/seed budget={_headroom(snapshot.pool_free_bytes, pool_bytes)}; "
        f"pool health={'eligible' if snapshot.pool_healthy else 'not verified'}"
    )


def _print_final_headroom(
    snapshot: ResourceSnapshot,
    settings: dict[str, Any],
    host_reserve_mib: int,
    pool_reserve_mib: int,
    output: Callable[[str], None],
) -> None:
    ram_required = (settings["memory_mib"] + host_reserve_mib) * _MIB
    pool_required = (
        settings["disk_gib"] * _GIB
        + STAGING_RESERVE_BYTES
        + pool_reserve_mib * _MIB
    )
    output(
        f"RAM headroom after VM RAM + {host_reserve_mib} MiB host reserve: "
        f"physical={_headroom(snapshot.physical_memory_bytes, ram_required)}; "
        f"available={_headroom(snapshot.available_memory_bytes, ram_required)}"
    )
    output(
        f"Pool headroom after {settings['disk_gib']} GiB disk + "
        f"{STAGING_RESERVE_BYTES:,} byte ISO/seed budget + {pool_reserve_mib} MiB reserve: "
        f"{_headroom(snapshot.pool_free_bytes, pool_required)}"
    )


def _unchecked_items(snapshot: ResourceSnapshot, failed: bool) -> list[str]:
    items = [
        "bridge eligibility (deferred because no NIC is requested)",
        "VM-process account identity and runuser probe (outside this resource preflight)",
        "actual staging-filesystem quota/access and staged ISO/seed readability",
        "middleware writes, VM creation/start, installation, and guest boot",
    ]
    items.extend(snapshot.unchecked)
    if failed:
        items.append("preflight checks after the first refusal (the existing checker is fail-fast)")
    return list(dict.fromkeys(items))


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] = print,
    client_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Guided Ubuntu Desktop TrueNAS preflight; exact-build and schema-gated, read-only, no create/start."
    )
    parser.add_argument("--preview", action="store_true", required=True)
    parser.parse_args(argv)
    if input_fn is None:
        if not sys.stdin.isatty():
            output_fn("Interactive terminal required; no TrueNAS calls were made.")
            return 2
        input_fn = input
    try:
        fixture = load_fixture(DEFAULT_FIXTURE)
    except (CompatibilityError, OSError, ValueError) as exc:
        output_fn(f"Read-only preflight: FAIL before host queries: {exc}")
        output_fn("RAM headroom: unchecked; pool headroom: unchecked.")
        output_fn("Unchecked: exact-build/schema gate, pool/RAM measurements, and all resource checks.")
        return 1

    while True:
        try:
            settings = _collect_settings(input_fn)
        except EOFError:
            output_fn("Read-only preflight: NOT RUN (interactive input ended).")
            return 2
        _print_proposal(settings, output_fn)
        # Reserve placeholders validate only the settings before measurement; the final request uses chosen reserves.
        measurement_request = _make_request(
            settings["vm_name"], settings["pool_name"], settings["vcpus"],
            settings["memory_mib"], settings["disk_gib"], 1, 1,
        )
        client = None
        try:
            client = (
                client_factory(fixture)
                if client_factory is not None
                else MidcltClient(timeout=120, expected_version=fixture.get("truenas_version"))
            )
            snapshot = _snapshot(client, fixture, measurement_request)
            if snapshot.gate_error:
                output_fn(f"Read-only preflight: FAIL before resource measurements: {snapshot.gate_error}")
                output_fn("RAM headroom: unchecked; pool headroom: unchecked.")
                output_fn("Unchecked: " + "; ".join(_unchecked_items(snapshot, failed=True)))
                output_fn("No write methods called; VM not created or started.")
                return 1
            _print_raw_headroom(snapshot, settings, output_fn)
            host_reserve = _ask_int("Host RAM reserve MiB (required; no default): ", None, 1, 1_048_576, input_fn)
            pool_reserve = _ask_int("Pool free-space reserve MiB (required; no default): ", None, 1, 1_048_576, input_fn)
            request = _make_request(
                settings["vm_name"], settings["pool_name"], settings["vcpus"],
                settings["memory_mib"], settings["disk_gib"], host_reserve, pool_reserve,
            )
            _validate_request(request)
            _print_proposal(settings, output_fn)
            output_fn(f"  host reserve={host_reserve} MiB; pool reserve={pool_reserve} MiB")
            _print_final_headroom(snapshot, settings, host_reserve, pool_reserve, output_fn)
            choice = input_fn("Type RUN for the read-only preflight, CHANGE to edit, or anything else to cancel: ").strip().casefold()
            if choice == "change":
                continue
            if choice != "run":
                output_fn("Read-only preflight: NOT RUN (operator cancelled).")
                return 0

            try:
                report = run_read_only_preflight(client, fixture, request)
            except (CompatibilityError, MiddlewareError, PreflightError, OSError, ValueError) as exc:
                output_fn(f"Read-only preflight: FAIL: {exc}")
                _print_final_headroom(snapshot, settings, host_reserve, pool_reserve, output_fn)
                for error in snapshot.errors:
                    output_fn(f"Measurement note: {error}")
                output_fn("Unchecked: " + "; ".join(_unchecked_items(snapshot, failed=True)))
                output_fn("No write methods called; VM not created or started.")
                return 1

            final_snapshot = ResourceSnapshot(
                truenas_version=report.truenas_version,
                checked_method_contracts=report.checked_method_contracts,
                pool_free_bytes=report.pool_free_bytes,
                pool_healthy=True,
                physical_memory_bytes=report.physical_memory_bytes,
                available_memory_bytes=report.available_memory_bytes,
            )
            output_fn(
                f"Read-only preflight: PASS for {report.truenas_version}; "
                f"matched {report.checked_method_contracts} reviewed method contracts."
            )
            _print_final_headroom(final_snapshot, settings, host_reserve, pool_reserve, output_fn)
            for check in report.checks:
                output_fn(f"PASS: {check}")
            output_fn("Unchecked: " + "; ".join(_unchecked_items(final_snapshot, failed=False)))
            output_fn("No write methods called; VM remains uncreated and unstarted.")
            return 0
        except EOFError:
            output_fn("Read-only preflight: NOT RUN (interactive input ended).")
            return 2
        except (CompatibilityError, MiddlewareError, PreflightError, OSError, ValueError) as exc:
            output_fn(f"Read-only preflight: FAIL: {exc}")
            output_fn("RAM headroom: unchecked; pool headroom: unchecked.")
            output_fn("Unchecked: resource checks not completed; no write methods were called.")
            return 1
        finally:
            if client is not None:
                client.close()


if __name__ == "__main__":
    raise SystemExit(main())
