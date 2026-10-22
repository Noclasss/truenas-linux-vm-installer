"""Tests for the read-only, no-write TrueNAS preflight."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from planning.readonly_preflight import (
    PreflightError,
    READ_ONLY_METHODS,
    ReadOnlyPreflightRequest,
    run_read_only_preflight,
)


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "truenas-26.0.0-beta.2-method-contracts.json"


class FakeReadOnlyMiddleware:
    def __init__(self, fixture, overrides=None, version=None):
        self.fixture = fixture
        self.overrides = overrides or {}
        self.version = version if version is not None else fixture["truenas_version"]
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "system.version":
            return self.version
        if method == "core.get_methods":
            return copy.deepcopy(self.overrides.get("core.get_methods", self.fixture["methods"]))
        if method not in READ_ONLY_METHODS:
            raise AssertionError(f"unexpected non-read-only middleware call: {method}")
        return copy.deepcopy(self.overrides[method])


class ReadOnlyPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def setUp(self):
        self.request = ReadOnlyPreflightRequest(
            profile="ubuntu-server",
            vm_name="linuxtest",
            pool_name="tank",
            bridge_name="br0",
            vcpus=2,
            memory_mib=4096,
            disk_gib=32,
            host_reserve_mib=2048,
            pool_reserve_mib=8192,
        )
        self.responses = {
            "pool.query": [{
                "name": "tank", "healthy": True, "warning": False,
                "status": "ONLINE", "free": 100 * 1024**3,
            }],
            "vm.device.nic_attach_choices": {"BRIDGE": ["br0", "br1"], "MACVLAN": []},
            "vm.query": [],
            "vm.device.query": [],
            "pool.dataset.query": [{"id": "tank/linux-vms", "type": "FILESYSTEM"}],
            "vm.supports_virtualization": True,
            "vm.maximum_supported_vcpus": 16,
            "vm.get_available_memory": 16 * 1024**3,
        }

    def run_preflight(self, request=None, responses=None, version=None, fixture=None):
        target_fixture = self.fixture if fixture is None else fixture
        client = FakeReadOnlyMiddleware(target_fixture, responses or self.responses, version=version)
        report = run_read_only_preflight(client, target_fixture, request or self.request)
        return report, client

    def assert_rejected(self, *, request=None, responses=None, version=None, fixture=None, contains=None):
        target_fixture = self.fixture if fixture is None else fixture
        client = FakeReadOnlyMiddleware(target_fixture, responses or self.responses, version=version)
        with self.assertRaises(PreflightError) as caught:
            run_read_only_preflight(client, target_fixture, request or self.request)
        if contains:
            self.assertIn(contains, str(caught.exception))
        return client

    def test_passes_with_valid_memory_in_bytes_and_never_authorizes_writes(self):
        required_memory_bytes = (self.request.memory_mib + self.request.host_reserve_mib) * 1024**2
        report, client = self.run_preflight(responses={
            **self.responses, "vm.get_available_memory": required_memory_bytes,
        })
        self.assertEqual(report.truenas_version, self.fixture["truenas_version"])
        self.assertEqual(report.checked_method_contracts, self.fixture["method_count"])
        self.assertFalse(report.live_write_authorized)
        self.assertFalse(report.vm_started)
        self.assertEqual(len(report.checks), 6)
        called = [method for method, _ in client.calls]
        self.assertEqual(called[:2], ["system.version", "core.get_methods"])
        self.assertEqual(set(called[2:]), set(READ_ONLY_METHODS))
        self.assertTrue(set(called) <= READ_ONLY_METHODS | {"system.version", "core.get_methods"})
        self.assertNotIn("vm.create", called)
        self.assertNotIn("vm.start", called)
        self.assertNotIn("pool.dataset.create", called)
        self.assertNotIn("vm.device.create", called)

    def test_rejects_unknown_available_memory_units_before_host_calls(self):
        cases = (
            ("vm.get_available_memory.result", "unit", None),
            ("vm.get_available_memory.result", "unit", "MiB"),
            ("vm.get_available_memory.result", "truenas_version", "TrueNAS-26.0.0-BETA.3"),
            ("pool.query.free", "unit", "MiB"),
            ("vm.create.memory", "unit", "bytes"),
        )
        for field, key, value in cases:
            with self.subTest(field=field, key=key, value=value):
                fixture = copy.deepcopy(self.fixture)
                semantics = fixture["method_semantics"][field]
                if value is None:
                    semantics.pop(key)
                else:
                    semantics[key] = value
                client = self.assert_rejected(fixture=fixture, contains=f"{field} unit is not verified")
                self.assertEqual(client.calls, [])

    def test_each_read_request_is_checked_against_reviewed_fixture(self):
        _, client = self.run_preflight()
        from planning.installer_core.schema import validate_method_request
        for method, params in client.calls[2:]:
            validate_method_request(self.fixture["methods"], method, params)

    def test_rejects_non_ubuntu_profile_before_any_host_call(self):
        client = self.assert_rejected(request=replace(self.request, profile="debian-server"), contains="only for ubuntu-server")
        self.assertEqual(client.calls, [])

    def test_rejects_invalid_or_boot_pool_input_before_host_calls(self):
        for pool_name in ("boot-pool", "../tank", "tank/child", ""):
            with self.subTest(pool=pool_name):
                client = self.assert_rejected(request=replace(self.request, pool_name=pool_name))
                self.assertEqual(client.calls, [])

    def test_rejects_vm_names_with_punctuation_before_host_calls(self):
        for vm_name in ("linux-test", "linux_test", "../bad", "1bad"):
            with self.subTest(vm_name=vm_name):
                client = self.assert_rejected(request=replace(self.request, vm_name=vm_name))
                self.assertEqual(client.calls, [])

    def test_rejects_version_mismatch_without_resource_queries(self):
        client = self.assert_rejected(version="TrueNAS-26.0.0-OTHER", contains="build mismatch")
        self.assertEqual([method for method, _ in client.calls], ["system.version"])

    def test_rejects_runtime_schema_mismatch(self):
        runtime = copy.deepcopy(self.fixture["methods"])
        runtime.pop("vm.create")
        client = self.assert_rejected(responses={**self.responses, "core.get_methods": runtime}, contains="missing")
        self.assertEqual([method for method, _ in client.calls], ["system.version", "core.get_methods"])

    def test_requires_unique_healthy_online_pool_and_capacity(self):
        cases = [
            ([{"name": "other", "healthy": True, "warning": False, "status": "ONLINE", "free": 1}], "not found exactly once"),
            ([self.responses["pool.query"][0], self.responses["pool.query"][0]], "not found exactly once"),
            ([dict(self.responses["pool.query"][0], healthy=False)], "unhealthy"),
            ([dict(self.responses["pool.query"][0], warning=True)], "unhealthy"),
            ([dict(self.responses["pool.query"][0], status="DEGRADED")], "not ONLINE"),
            ([dict(self.responses["pool.query"][0], free=None)], "free-space byte count"),
            ([dict(self.responses["pool.query"][0], free=1)], "less than the full disk size"),
        ]
        for pool_rows, expected in cases:
            with self.subTest(expected=expected):
                client = self.assert_rejected(responses={**self.responses, "pool.query": pool_rows}, contains=expected)
                self.assertIn("pool.query", [method for method, _ in client.calls])

    def test_selected_bridge_must_be_eligible_not_substituted(self):
        client = self.assert_rejected(
            request=replace(self.request, bridge_name="br-missing"),
            contains="not an eligible",
        )
        self.assertNotIn("interface.query", [method for method, _ in client.calls])

    def test_rejects_duplicate_vm_names_case_insensitively(self):
        self.assert_rejected(responses={**self.responses, "vm.query": [{"name": "LINUXTEST"}]}, contains="already exists")

    def test_rejects_existing_device_reference_to_target_zvol(self):
        path = "/dev/zvol/tank/linux-vms/linuxtest/disk0"
        self.assert_rejected(
            responses={**self.responses, "vm.device.query": [{"id": 22, "vm": 3, "attributes": {"path": path}}]},
            contains="already references the intended zvol",
        )

    def test_rejects_malformed_device_query_rows(self):
        self.assert_rejected(
            responses={**self.responses, "vm.device.query": [{"id": 22, "vm": 3}]},
            contains="valid attributes",
        )

    def test_rejects_dataset_collisions_and_wrong_shared_parent_type(self):
        for dataset_rows, expected in [
            ([{"id": "tank/linux-vms", "type": "VOLUME"}], "wrong dataset type"),
            ([{"id": "tank/linux-vms/linuxtest", "type": "FILESYSTEM"}], "target or descendant"),
            ([{"id": "tank/linux-vms/LINUXTEST/extra", "type": "FILESYSTEM"}], "target or descendant"),
        ]:
            with self.subTest(dataset=dataset_rows[0]["id"]):
                self.assert_rejected(responses={**self.responses, "pool.dataset.query": dataset_rows}, contains=expected)

    def test_rejects_existing_target_zvol(self):
        client = self.assert_rejected(
            responses={**self.responses, "pool.dataset.query": [
                {"id": "tank/linux-vms/linuxtest/disk0", "type": "VOLUME"},
            ]},
            contains="target disk zvol already exists",
        )
        self.assertIn("pool.dataset.query", [method for method, _ in client.calls])

    def test_rejects_malformed_dataset_or_vm_query_rows(self):
        self.assert_rejected(responses={**self.responses, "pool.dataset.query": [{"id": "tank/x"}]}, contains="valid id/type")
        self.assert_rejected(responses={**self.responses, "vm.query": [{"id": 12}]}, contains="valid name")

    def test_rejects_unsupported_virtualization_and_vcpu_over_limit(self):
        self.assert_rejected(responses={**self.responses, "vm.supports_virtualization": False}, contains="virtualization support")
        self.assert_rejected(responses={**self.responses, "vm.maximum_supported_vcpus": 1}, contains="exceeds")

    def test_rejects_insufficient_or_malformed_memory(self):
        required_memory_bytes = (self.request.memory_mib + self.request.host_reserve_mib) * 1024**2
        self.assert_rejected(
            responses={**self.responses, "vm.get_available_memory": required_memory_bytes - 1},
            contains="below the VM request",
        )
        self.assert_rejected(responses={**self.responses, "vm.get_available_memory": True}, contains="invalid available-memory")

    def test_rejects_bad_user_reserves_and_resource_values(self):
        for changes in (
            {"vcpus": True}, {"memory_mib": 0}, {"disk_gib": 8},
            {"host_reserve_mib": 0}, {"pool_reserve_mib": -1},
        ):
            with self.subTest(changes=changes):
                client = self.assert_rejected(request=replace(self.request, **changes))
                self.assertEqual(client.calls, [])


class ReadOnlyPreflightCLITests(unittest.TestCase):
    def test_cli_binds_scalar_parsing_to_reviewed_fixture_version(self):
        from planning import check_readonly_preflight as cli
        from planning.readonly_preflight import ReadOnlyPreflightReport

        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        report = ReadOnlyPreflightReport(
            truenas_version=fixture["truenas_version"],
            checked_method_contracts=fixture["method_count"],
            vm_name="linuxprobe01",
            pool_name="NVME2",
            bridge_name="br01",
            checks=(),
        )
        args = [
            "--profile", "ubuntu-server", "--vm-name", "linuxprobe01",
            "--pool", "NVME2", "--bridge", "br01", "--vcpus", "2",
            "--memory-mib", "4096", "--disk-gib", "16",
            "--host-reserve-mib", "16384", "--pool-reserve-mib", "102400",
        ]
        with mock.patch.object(cli, "load_fixture", return_value=fixture), \
             mock.patch.object(cli, "MidcltClient") as client_factory, \
             mock.patch.object(cli, "run_read_only_preflight", return_value=report), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
        client_factory.assert_called_once_with(timeout=120, expected_version=fixture["truenas_version"])


if __name__ == "__main__":
    unittest.main()
