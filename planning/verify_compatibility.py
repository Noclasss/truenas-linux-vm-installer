#!/usr/bin/env python3
"""Read-only TrueNAS build and method-request-schema compatibility check."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from .installer_core.middleware import MiddlewareError, MidcltClient
except ImportError:  # direct execution as ``python planning/verify_compatibility.py``
    from installer_core.compatibility import CompatibilityError, verify_runtime_compatibility
    from installer_core.middleware import MiddlewareError, MidcltClient

DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "truenas-26.0.0-beta.2-method-contracts.json"
MAX_FIXTURE_BYTES = 1_000_000


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def load_fixture(path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_FIXTURE_BYTES:
            raise ValueError("fixture exceeds the 1 MB limit")
        content = path.read_text(encoding="utf-8")
        fixture = json.loads(content, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CompatibilityError("could not load the reviewed schema fixture") from exc
    if not isinstance(fixture, dict):
        raise CompatibilityError("schema fixture must be a JSON object")
    return fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a TrueNAS build and selected request/return schema contracts using read-only calls only."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="reviewed schema fixture (default: captured TrueNAS 26.0.0-BETA.2)")
    args = parser.parse_args(argv)
    try:
        fixture = load_fixture(args.fixture)
        client = MidcltClient(timeout=120)
        try:
            report = verify_runtime_compatibility(client, fixture)
        finally:
            client.close()
    except (CompatibilityError, MiddlewareError) as exc:
        print(f"Compatibility check failed: {exc}", file=sys.stderr)
        return 1
    print(f"Read-only compatibility check passed for {report.truenas_version}.")
    print(f"Matched request/return schema contracts for {len(report.checked_methods)} selected methods.")
    print("Middleware calls made: system.version and core.get_methods only; no write methods called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
