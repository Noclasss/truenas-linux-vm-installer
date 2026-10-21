"""Read-only comparison of live TrueNAS request and return schemas to a pinned fixture."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schema import SchemaError, normalize_method_contract


_FIXTURE_FORMAT = "truenas-method-contract-fixture-v1"
_BUILD = re.compile(r"TrueNAS-[A-Za-z0-9._+-]+\Z")
_METHOD = re.compile(r"[A-Za-z][A-Za-z0-9_.]*\Z")


class CompatibilityError(RuntimeError):
    """The live host does not match the reviewed read-only schema fixture."""


@dataclass(frozen=True)
class CompatibilityReport:
    truenas_version: str
    checked_methods: tuple[str, ...]


def verify_runtime_compatibility(client: Any, fixture: Any) -> CompatibilityReport:
    """Check exact build and request/return schemas using read-only calls only.

    The client is called only with ``system.version`` and ``core.get_methods``.
    No method whose schema is inspected is invoked.
    """
    if not isinstance(fixture, dict) or fixture.get("format") != _FIXTURE_FORMAT:
        raise CompatibilityError("unsupported or malformed schema fixture")
    expected_version = fixture.get("truenas_version")
    methods = fixture.get("methods")
    if not isinstance(expected_version, str) or not _BUILD.fullmatch(expected_version):
        raise CompatibilityError("schema fixture has an invalid TrueNAS build")
    if not isinstance(methods, dict) or not methods:
        raise CompatibilityError("schema fixture has no method contracts")
    if fixture.get("method_count") != len(methods):
        raise CompatibilityError("schema fixture method count is inconsistent")
    if any(not isinstance(name, str) or not _METHOD.fullmatch(name) for name in methods):
        raise CompatibilityError("schema fixture contains an invalid method name")

    runtime_version = client.call("system.version", [])
    if runtime_version != expected_version:
        raise CompatibilityError(
            f"TrueNAS build mismatch: expected {expected_version}, received {runtime_version!r}"
        )

    runtime_methods = client.call("core.get_methods", [])
    if not isinstance(runtime_methods, dict):
        raise CompatibilityError("core.get_methods did not return a method mapping")

    checked: list[str] = []
    for method in sorted(methods):
        expected = methods[method]
        runtime = runtime_methods.get(method)
        if not isinstance(runtime, dict):
            raise CompatibilityError(f"required middleware method is missing: {method}")
        try:
            expected_contract = normalize_method_contract(expected, method)
            runtime_contract = normalize_method_contract(runtime, method)
        except SchemaError as exc:
            raise CompatibilityError(f"invalid method contract for {method}: {exc}") from exc
        if runtime_contract != expected_contract:
            raise CompatibilityError(f"request/return schema differs from the reviewed fixture: {method}")
        checked.append(method)
    return CompatibilityReport(expected_version, tuple(checked))
