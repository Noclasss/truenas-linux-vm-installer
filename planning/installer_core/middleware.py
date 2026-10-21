"""Local TrueNAS middleware adapter adapted from the HAOS installer core."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any


class MiddlewareError(RuntimeError):
    """Middleware operation failed without exposing raw host output."""


DEFAULT_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_METHOD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.]*\Z")
_VERSION = re.compile(r"TrueNAS-[A-Za-z0-9._+-]+\Z")


class MidcltClient:
    """Call local middleware with bounded time and conservative output parsing.

    This adapter makes no calls until ``call`` is explicitly invoked. It does
    not install packages, connect remotely, or decide which methods are safe.
    Mutating calls should be routed through ``SchemaCheckedClient``.
    """

    def __init__(self, executable: str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        if type(timeout) is not int or timeout <= 0:
            raise MiddlewareError("middleware timeout must be a positive integer")
        self.executable = executable or shutil.which("midclt")
        if not self.executable:
            raise MiddlewareError("midclt was not found; run from an authenticated TrueNAS host shell")
        self.timeout = timeout

    def close(self) -> None:
        """Match the lifecycle interface of remote middleware clients."""

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        if not isinstance(method, str) or not _METHOD_NAME.fullmatch(method):
            raise MiddlewareError("invalid middleware method name")
        if params is None:
            params = []
        if not isinstance(params, list):
            raise MiddlewareError("middleware parameters must be a positional list")
        command = [self.executable, "call", method]
        try:
            command.extend(json.dumps(value, separators=(",", ":")) for value in params)
        except (TypeError, ValueError) as exc:
            raise MiddlewareError("middleware parameters are not JSON-serializable") from exc
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MiddlewareError(f"midclt call {method} timed out") from exc
        except OSError as exc:
            raise MiddlewareError(f"could not execute midclt call {method}") from exc
        if result.returncode != 0:
            # Do not surface raw middleware output: it may contain host details.
            raise MiddlewareError(f"midclt call {method} failed (exit {result.returncode})")
        output = result.stdout.strip()
        if len(output.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise MiddlewareError("midclt response exceeds the 64 MiB parsing limit")
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            # Some TrueNAS builds emit system.version as an unquoted scalar.
            if method == "system.version" and _VERSION.fullmatch(output):
                return output
            raise MiddlewareError(f"midclt call {method} returned invalid JSON") from exc
