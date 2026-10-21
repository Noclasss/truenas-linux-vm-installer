#!/usr/bin/env python3
"""Run the experimental Ubuntu profile's read-only TrueNAS preflight."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .installer_core.compatibility import CompatibilityError
    from .installer_core.middleware import MiddlewareError, MidcltClient
    from .readonly_preflight import PreflightError, ReadOnlyPreflightRequest, run_read_only_preflight
    from .verify_compatibility import DEFAULT_FIXTURE, load_fixture
except ImportError:  # direct execution as ``python planning/check_readonly_preflight.py``
    from installer_core.compatibility import CompatibilityError
    from installer_core.middleware import MiddlewareError, MidcltClient
    from readonly_preflight import PreflightError, ReadOnlyPreflightRequest, run_read_only_preflight
    from verify_compatibility import DEFAULT_FIXTURE, load_fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify caller-selected Ubuntu VM resources with read-only middleware calls only. "
            "This command cannot create, start, stop, or delete a VM or dataset."
        )
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--profile", required=True, choices=("ubuntu-server",))
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--pool", required=True, help="caller-selected, non-boot storage pool")
    parser.add_argument("--bridge", required=True, help="caller-selected existing VM bridge")
    parser.add_argument("--vcpus", required=True, type=int)
    parser.add_argument("--memory-mib", required=True, type=int)
    parser.add_argument("--disk-gib", required=True, type=int)
    parser.add_argument("--host-reserve-mib", required=True, type=int)
    parser.add_argument("--pool-reserve-mib", required=True, type=int)
    args = parser.parse_args(argv)

    request = ReadOnlyPreflightRequest(
        profile=args.profile,
        vm_name=args.vm_name,
        pool_name=args.pool,
        bridge_name=args.bridge,
        vcpus=args.vcpus,
        memory_mib=args.memory_mib,
        disk_gib=args.disk_gib,
        host_reserve_mib=args.host_reserve_mib,
        pool_reserve_mib=args.pool_reserve_mib,
    )
    client = None
    try:
        fixture = load_fixture(args.fixture)
        client = MidcltClient(timeout=120)
        report = run_read_only_preflight(client, fixture, request)
    except (CompatibilityError, MiddlewareError, PreflightError, OSError, ValueError) as exc:
        print(f"Read-only preflight failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()

    print(f"Read-only preflight checks passed for {report.truenas_version}.")
    print(f"Matched {report.checked_method_contracts} reviewed request/return method contracts.")
    print(f"Caller selections: VM={report.vm_name}, pool={report.pool_name}, bridge={report.bridge_name}.")
    for check in report.checks:
        print(f"- {check}")
    print("No write methods called. This is not authorization to create or start a VM.")
    print("All distro profiles remain experimental; the VM remains uncreated and unstarted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
