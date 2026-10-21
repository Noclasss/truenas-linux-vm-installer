#!/usr/bin/env python3
"""Capture a small, read-only TrueNAS middleware schema subset to a private file.

Intended to run from an authenticated TrueNAS shell. This helper makes exactly
these middleware calls: ``system.version`` and ``core.get_methods``. It does
not call any method that creates, deletes, starts, or changes resources.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:
    from .installer_core.safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain, write_private_new_file as write_private_file
except ImportError:  # direct execution as ``python planning/capture_schema.py``
    from installer_core.safe_filesystem import FilesystemSafetyError, ensure_trusted_directory_chain, write_private_new_file as write_private_file

DEFAULT_OUTPUT_ROOT = Path("/tmp")
MIDCLT_TIMEOUT_SECONDS = 120
MAX_METHODS_RESPONSE_BYTES = 64 * 1024 * 1024
METHODS_TO_CAPTURE = (
    "core.get_methods",
    "system.version",
    "system.info",
    "interface.query",
    "pool.query",
    "pool.dataset.query",
    "pool.dataset.create",
    "vm.query",
    "vm.create",
    "vm.start",
    "vm.stop",
    "vm.status",
    "vm.device.query",
    "vm.device.create",
    "vm.device.convert",
    "vm.device.delete",
    "vm.device.nic_attach_choices",
    "vm.supports_virtualization",
    "vm.maximum_supported_vcpus",
    "vm.get_available_memory",
)


class CaptureError(RuntimeError):
    """Expected capture failure safe to display to the operator."""


def run_midclt(method: str) -> str:
    """Run one allowlisted read method and avoid echoing middleware output."""
    if method not in {"system.version", "core.get_methods"}:
        raise CaptureError("Only system.version and core.get_methods are permitted")
    executable = shutil.which("midclt")
    if executable is None:
        raise CaptureError("midclt is unavailable; run this tool from a TrueNAS host shell")
    try:
        result = subprocess.run(
            [executable, "call", method],
            capture_output=True,
            text=True,
            timeout=MIDCLT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"Read-only middleware call {method} timed out") from exc
    except OSError as exc:
        raise CaptureError(f"Could not execute midclt for {method}") from exc
    if result.returncode != 0:
        raise CaptureError(f"Read-only middleware call {method} failed (exit {result.returncode})")
    return result.stdout.strip()


def decode_version(output: str) -> str:
    """Accept midclt's JSON string or its plain-text scalar form."""
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        value = output
    if not isinstance(value, str) or not re.fullmatch(r"TrueNAS-[A-Za-z0-9._+-]+", value):
        raise CaptureError("system.version returned an unexpected value")
    return value


def decode_methods(output: str) -> dict[str, Any]:
    if len(output.encode("utf-8")) > MAX_METHODS_RESPONSE_BYTES:
        raise CaptureError("core.get_methods response exceeds the 64 MiB parsing limit")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CaptureError("core.get_methods did not return valid JSON") from exc
    if not isinstance(value, dict) or not all(isinstance(name, str) for name in value):
        raise CaptureError("core.get_methods returned an unexpected structure")
    return value


def validate_output_parent(path: Path, root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    """Require a new output file beneath a safe temporary root, rejecting symlinks."""
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    absolute_root = Path(os.path.abspath(os.fspath(root.expanduser())))
    try:
        ensure_trusted_directory_chain(
            absolute.parent,
            "capture output",
            allowed_root=absolute_root,
        )
    except FilesystemSafetyError as exc:
        raise CaptureError(str(exc)) from exc
    return absolute


def default_output_path(version: str, root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", version)
    return root / f"truenas-linux-vm-installer-methods-{safe_version}.json"


def write_private_new_file(path: Path, text: str, root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    path = validate_output_parent(path, root)
    try:
        return write_private_file(path, text, allowed_root=root)
    except FilesystemSafetyError as exc:
        if "overwrite" in str(exc):
            raise CaptureError(f"Refusing to overwrite existing capture file: {path}") from exc
        raise CaptureError(f"Could not safely write capture file at {path}") from exc


def capture_schema(
    expected_version: str,
    output_path: Path | None = None,
    caller: Callable[[str], str] = run_midclt,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[Path, dict[str, Any]]:
    expected_version = decode_version(expected_version)
    version = decode_version(caller("system.version"))
    if version != expected_version:
        raise CaptureError(
            f"Connected to {version}, expected {expected_version}; no schema capture was written"
        )

    methods = decode_methods(caller("core.get_methods"))
    selected: dict[str, Any] = {}
    missing: list[str] = []
    for name in METHODS_TO_CAPTURE:
        if name not in methods:
            missing.append(name)
            continue
        if not isinstance(methods[name], dict):
            raise CaptureError(f"core.get_methods entry for {name} is not an object")
        selected[name] = methods[name]

    capture: dict[str, Any] = {
        "format": "truenas-core-get-methods-subset-v1",
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "truenas_version": version,
        "source": "midclt call core.get_methods",
        "requested_methods": list(METHODS_TO_CAPTURE),
        "missing_methods": missing,
        "available_method_count": len(methods),
        "methods": selected,
        "warning": "Local schema metadata only; do not commit this capture or treat it as successful VM creation evidence.",
    }
    destination = output_path or default_output_path(version, output_root)
    written = write_private_new_file(
        destination,
        json.dumps(capture, indent=2, sort_keys=True) + "\n",
        output_root,
    )
    return written, capture


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture selected TrueNAS middleware method metadata locally; no resources are changed."
    )
    parser.add_argument(
        "--expected-version",
        required=True,
        help="exact TrueNAS build string printed by system.version; the capture aborts if the build differs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new output file under /tmp (default: private versioned JSON); existing files are never overwritten",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        path, capture = capture_schema(args.expected_version, args.output)
    except CaptureError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"Captured {len(capture['methods'])}/{len(capture['requested_methods'])} "
        f"method definitions for {capture['truenas_version']}."
    )
    if capture["missing_methods"]:
        print("Missing methods: " + ", ".join(capture["missing_methods"]))
    print(f"Private local capture: {path} (mode 0600)")
    print("Only system.version and core.get_methods were queried; no middleware write methods were called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
