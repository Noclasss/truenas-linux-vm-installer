"""Tests for the guided read-only Ubuntu Desktop host preview."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from planning import desktop_readonly_preview as preview
from planning.readonly_preflight import READ_ONLY_METHODS
from planning.ubuntu_desktop import STAGING_RESERVE_BYTES

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "truenas-26.0.0-beta.2-method-contracts.json"
MIB = 1024**2
GIB = 1024**3


class FakeReadOnlyMiddleware:
    def __init__(self, fixture, *, version=None, overrides=None):
        self.fixture = fixture
        self.version = version if version is not None else fixture["truenas_version"]
        self.overrides = overrides or {}
        self.calls = []
        self.closed = False

    def call(self, method, _params):
        self.calls.append(method)
        if method == "system.version":
            return self.version
        if method == "core.get_methods":
            return copy.deepcopy(self.overrides.get(method, self.fixture["methods"]))
        if method not in READ_ONLY_METHODS:
            raise AssertionError(f"preview attempted a non-read-only middleware call: {method}")
        defaults = {
            "pool.query": [{
                "name": "testpool", "healthy": True, "warning": False,
                "status": "ONLINE", "free": 1024 * GIB,
            }],
            "system.info": {"physmem": 32 * GIB},
            "vm.get_available_memory": 16 * GIB,
            "vm.query": [],
            "vm.device.query": [],
            "pool.dataset.query": [{"id": "testpool/linux-vms", "type": "FILESYSTEM"}],
            "vm.supports_virtualization": True,
            "vm.maximum_supported_vcpus": 16,
        }
        return copy.deepcopy(self.overrides.get(method, defaults[method]))

    def close(self):
        self.closed = True


class DesktopReadOnlyPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def run_preview(self, answers, *, overrides=None, version=None):
        client = FakeReadOnlyMiddleware(self.fixture, version=version, overrides=overrides)
        output = []
        values = iter(answers)
        code = preview.main(
            ["--preview"],
            input_fn=lambda _prompt: next(values),
            output_fn=output.append,
            client_factory=lambda _fixture: client,
        )
        return code, "\n".join(output), client

    def default_answers(self, *, vm="PreviewVM", pool="testpool", host_reserve="2048", pool_reserve="20480", final="RUN"):
        return [vm, pool, "", "", "", host_reserve, pool_reserve, final]

    def test_guided_preview_runs_exact_preflight_with_no_writes_or_nic(self):
        client = FakeReadOnlyMiddleware(self.fixture)
        output = []
        answers = iter(self.default_answers())
        code = preview.main(
            ["--preview"],
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            client_factory=lambda _fixture: client,
        )
        self.assertEqual(code, 0, "\n".join(output))
        self.assertEqual(client.calls[:2], ["system.version", "core.get_methods"])
        self.assertEqual(client.calls[2], "pool.query")
        self.assertEqual(client.calls[3:5], ["system.info", "vm.get_available_memory"])
        self.assertEqual(client.calls[5:7], ["system.version", "core.get_methods"])
        self.assertTrue(set(client.calls) <= READ_ONLY_METHODS | {"system.version", "core.get_methods"})
        self.assertNotIn("vm.device.nic_attach_choices", client.calls)
        self.assertTrue(client.closed)
        text = "\n".join(output)
        self.assertIn("vCPUs=2; RAM=6144 MiB; virtual disk=32 GiB", text)
        self.assertIn("Read-only preflight: PASS for TrueNAS-26.0.0-BETA.2", text)
        self.assertIn("matched 20 reviewed method contracts", text)
        self.assertIn("RAM headroom after VM RAM + 2048 MiB host reserve", text)
        self.assertIn("Pool headroom after 32 GiB disk", text)
        self.assertIn("bridge eligibility (deferred because no NIC is requested)", text)
        self.assertIn("VM-process account identity and runuser probe", text)
        self.assertIn("No write methods called", text)

    def test_failed_capacity_gate_still_reports_ram_and_pool_headroom(self):
        pool_free = 32 * GIB + STAGING_RESERVE_BYTES + 20_480 * MIB - 1
        available = (6144 + 2048) * MIB - 1
        code, text, client = self.run_preview(
            self.default_answers(),
            overrides={
                "pool.query": [{
                    "name": "testpool", "healthy": True, "warning": False,
                    "status": "ONLINE", "free": pool_free,
                }],
                "vm.get_available_memory": available,
            },
        )
        self.assertEqual(code, 1)
        self.assertIn("Read-only preflight: FAIL: selected pool has less", text)
        self.assertIn("RAM headroom after VM RAM + 2048 MiB host reserve: physical=", text)
        self.assertIn("-1 bytes", text)
        self.assertIn("Pool headroom after 32 GiB disk", text)
        self.assertIn("preflight checks after the first refusal", text)
        self.assertTrue(set(client.calls) <= READ_ONLY_METHODS | {"system.version", "core.get_methods"})

    def test_exact_build_mismatch_stops_before_resource_queries(self):
        code, text, client = self.run_preview(
            self.default_answers()[:5],
            version="TrueNAS-26.0.0-BETA.3",
        )
        self.assertEqual(code, 1)
        self.assertEqual(client.calls, ["system.version"])
        self.assertIn("FAIL before resource measurements", text)
        self.assertIn("RAM headroom: unchecked; pool headroom: unchecked", text)

    def test_schema_mismatch_stops_before_resource_measurements(self):
        runtime_methods = copy.deepcopy(self.fixture["methods"])
        runtime_methods.pop("vm.create")
        code, text, client = self.run_preview(
            self.default_answers()[:5],
            overrides={"core.get_methods": runtime_methods},
        )
        self.assertEqual(code, 1)
        self.assertEqual(client.calls, ["system.version", "core.get_methods"])
        self.assertIn("required middleware method is missing: vm.create", text)
        self.assertIn("FAIL before resource measurements", text)

    def test_invalid_physical_memory_prevents_available_memory_query(self):
        answers = self.default_answers()
        code, text, client = self.run_preview(
            answers,
            overrides={"system.info": {"physmem": None}},
        )
        self.assertEqual(code, 1)
        self.assertNotIn("vm.get_available_memory", client.calls)
        self.assertIn("available RAM (query stopped because physical RAM was invalid)", text)
        self.assertIn("Read-only preflight: FAIL: host returned an invalid total physical-memory", text)

    def test_change_restarts_questions_and_checks_the_revised_plan(self):
        first = ["PreviewOne", "testpool", "", "", "", "2048", "20480", "CHANGE"]
        second = ["PreviewTwo", "testpool", "4", "8192", "64", "1024", "10240", "RUN"]
        clients = []
        def make_client(fixture):
            result = FakeReadOnlyMiddleware(fixture)
            clients.append(result)
            return result
        output = []
        answers = iter(first + second)
        code = preview.main(
            ["--preview"],
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
            client_factory=make_client,
        )
        self.assertEqual(code, 0, "\n".join(output))
        self.assertEqual(len(clients), 2)
        self.assertTrue(all(client.closed for client in clients))
        text = "\n".join(output)
        self.assertIn("VM name=PreviewTwo; pool=testpool", text)
        self.assertIn("vCPUs=4; RAM=8192 MiB; virtual disk=64 GiB", text)
        self.assertIn("host reserve=1024 MiB; pool reserve=10240 MiB", text)

    def test_direct_cli_imports_from_outside_the_repository(self):
        script = Path(__file__).resolve().parent / "desktop_readonly_preview.py"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--preview", result.stdout)

    def test_noninteractive_preview_stops_before_client_creation(self):
        output = io.StringIO()
        with patch.object(preview.sys, "stdin", io.StringIO("")):
            code = preview.main(
                ["--preview"],
                output_fn=lambda line: print(line, file=output),
                client_factory=lambda _fixture: self.fail("client must not be created"),
            )
        self.assertEqual(code, 2)
        self.assertIn("Interactive terminal required", output.getvalue())


if __name__ == "__main__":
    unittest.main()
