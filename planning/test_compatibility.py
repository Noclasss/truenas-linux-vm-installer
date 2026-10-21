import copy
import json
import unittest
from pathlib import Path
from typing import Any

from installer_core import compatibility
from installer_core.schema import SchemaError, validate_method_request

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "truenas-26.0.0-beta.2-method-contracts.json"


class FakeReadOnlyClient:
    def __init__(self, version: Any, methods: Any):
        self.version = version
        self.methods = methods
        self.calls: list[str] = []

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        self.calls.append(method)
        if method == "system.version":
            return self.version
        if method == "core.get_methods":
            return self.methods
        raise AssertionError(f"unexpected non-read-only middleware call: {method}")


class CapturedSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.methods = cls.fixture["methods"]

    def test_fixture_is_minimized_to_all_selected_method_contracts(self):
        self.assertEqual(self.fixture["truenas_version"], "TrueNAS-26.0.0-BETA.2")
        self.assertEqual(self.fixture["method_count"], 20)
        self.assertEqual(len(self.methods), 20)
        self.assertNotIn("available_method_count", self.fixture)
        self.assertTrue(all(
            set(metadata) <= {"accepts", "returns", "$defs", "definitions"}
            and "accepts" in metadata
            and "returns" in metadata
            for metadata in self.methods.values()
        ))

    def test_haos_reference_payload_shapes_match_captured_schemas(self):
        sample_vm = {
            "name": "SchemaCheck",
            "description": "Home Assistant OS VM created by haos-truenas-installer (BETA)",
            "vcpus": 2,
            "cores": 2,
            "threads": 1,
            "memory": 4096,
            "min_memory": None,
            "cpu_mode": "HOST-MODEL",
            "bootloader": "UEFI",
            "bootloader_ovmf": "OVMF_CODE.fd",
            "trusted_platform_module": False,
            "autostart": False,
            "ensure_display_device": True,
            "time": "UTC",
        }
        examples = {
            "pool.dataset.create": [
                [{"name": "tank/haos-vms/schemacheck", "type": "FILESYSTEM", "comments": "Created by haos-truenas-installer for HAOS VM disks"}],
                [{"name": "tank/haos-vms/schemacheck", "type": "VOLUME", "volsize": 64 * 1024**3, "comments": "Created by haos-truenas-installer for SchemaCheck"}],
            ],
            "vm.create": [[sample_vm]],
            "vm.device.create": [
                [{"vm": 1, "attributes": {"dtype": "DISK", "path": "/dev/zvol/tank/haos-vms/schemacheck", "type": "VIRTIO", "create_zvol": False, "iotype": "THREADS"}}],
                [{"vm": 1, "attributes": {"dtype": "NIC", "type": "VIRTIO", "nic_attach": "br-schema"}}],
            ],
            "vm.start": [[1, {"overcommit": False}]],
        }
        checked = 0
        for method, requests in examples.items():
            for params in requests:
                validate_method_request(self.methods, method, params)
                checked += 1
        self.assertEqual(checked, 6)

    def test_invalid_enum_variant_and_id_are_rejected(self):
        with self.assertRaises(SchemaError):
            validate_method_request(self.methods, "pool.dataset.create", [{"name": "tank/x", "type": "INVALID"}])
        with self.assertRaises(SchemaError):
            validate_method_request(self.methods, "vm.device.create", [{"vm": 1, "attributes": {"dtype": "DISK", "type": "INVALID"}}])
        with self.assertRaises(SchemaError):
            validate_method_request(self.methods, "vm.start", ["not-an-id", {"overcommit": False}])

    def test_vm_device_convert_request_matches_exact_beta2_contract(self):
        valid = [{
            "source": "synthetic-source",
            "destination": "synthetic-destination",
        }]
        validate_method_request(self.methods, "vm.device.convert", valid)
        for invalid in (
            [{"source": "synthetic-source"}],
            [{"source": "", "destination": "synthetic-destination"}],
            [{"source": "synthetic-source", "destination": "synthetic-destination", "format": "raw"}],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(SchemaError):
                validate_method_request(self.methods, "vm.device.convert", invalid)

    def test_read_only_probe_checks_exact_version_and_all_method_schemas(self):
        client = FakeReadOnlyClient(self.fixture["truenas_version"], self.methods)
        report = compatibility.verify_runtime_compatibility(client, self.fixture)
        self.assertEqual(report.truenas_version, self.fixture["truenas_version"])
        self.assertEqual(len(report.checked_methods), 20)
        self.assertEqual(client.calls, ["system.version", "core.get_methods"])

    def test_build_mismatch_aborts_before_method_schema_query(self):
        client = FakeReadOnlyClient("TrueNAS-OTHER", self.methods)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "build mismatch"):
            compatibility.verify_runtime_compatibility(client, self.fixture)
        self.assertEqual(client.calls, ["system.version"])

    def test_method_schema_mismatch_fails_without_any_write_call(self):
        changed = copy.deepcopy(self.methods)
        changed["vm.start"]["accepts"][0]["type"] = "string"
        client = FakeReadOnlyClient(self.fixture["truenas_version"], changed)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "schema differs"):
            compatibility.verify_runtime_compatibility(client, self.fixture)
        self.assertEqual(client.calls, ["system.version", "core.get_methods"])

    def test_missing_selected_method_is_rejected(self):
        incomplete = dict(self.methods)
        incomplete.pop("vm.create")
        client = FakeReadOnlyClient(self.fixture["truenas_version"], incomplete)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "method is missing"):
            compatibility.verify_runtime_compatibility(client, self.fixture)

    def _synthetic_fixture(self, *, returns=None):
        contract = {"accepts": []}
        if returns is not None:
            contract["returns"] = returns
        return {
            "format": self.fixture["format"],
            "truenas_version": self.fixture["truenas_version"],
            "method_count": 1,
            "methods": {"test.read": contract},
        }

    def test_return_schema_mismatch_is_rejected(self):
        expected = self._synthetic_fixture(returns=[{
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        }])
        runtime = copy.deepcopy(expected["methods"])
        runtime["test.read"]["returns"][0]["properties"]["id"]["type"] = "string"
        client = FakeReadOnlyClient(expected["truenas_version"], runtime)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "schema differs"):
            compatibility.verify_runtime_compatibility(client, expected)
        self.assertEqual(client.calls, ["system.version", "core.get_methods"])

    def test_response_format_annotation_is_compared(self):
        expected = self._synthetic_fixture(returns=[{"type": "string", "format": "date-time"}])
        runtime = copy.deepcopy(expected["methods"])
        runtime["test.read"]["returns"][0]["format"] = "uri"
        client = FakeReadOnlyClient(expected["truenas_version"], runtime)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "schema differs"):
            compatibility.verify_runtime_compatibility(client, expected)

    def test_unsupported_return_schema_keyword_fails_closed(self):
        expected = self._synthetic_fixture(returns=[{"type": "object", "minProperties": 1}])
        client = FakeReadOnlyClient(expected["truenas_version"], expected["methods"])
        with self.assertRaisesRegex(compatibility.CompatibilityError, "invalid method contract"):
            compatibility.verify_runtime_compatibility(client, expected)
        self.assertEqual(client.calls, ["system.version", "core.get_methods"])

    def test_fixture_required_return_schema_cannot_be_missing_at_runtime(self):
        expected = self._synthetic_fixture(returns=[{"type": "boolean"}])
        runtime = {"test.read": {"accepts": []}}
        client = FakeReadOnlyClient(expected["truenas_version"], runtime)
        with self.assertRaisesRegex(compatibility.CompatibilityError, "schema differs"):
            compatibility.verify_runtime_compatibility(client, expected)


if __name__ == "__main__":
    unittest.main()
