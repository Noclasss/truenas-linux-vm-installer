import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from planning import capture_schema as tool


class SchemaCaptureTests(unittest.TestCase):
    def setUp(self):
        self.version = "TrueNAS-TEST-1"
        self.methods = {
            name: {
                "__accepts__": [{"name": "payload", "type": "object"}],
                "__returns__": {"type": "object"},
            }
            for name in tool.METHODS_TO_CAPTURE
        }

    def caller_for(self, methods=None, version=None):
        calls = []
        methods = self.methods if methods is None else methods
        version = self.version if version is None else version

        def caller(method):
            calls.append(method)
            if method == "system.version":
                return json.dumps(version)
            if method == "core.get_methods":
                return json.dumps(methods)
            raise AssertionError(f"Unexpected middleware call: {method}")

        return caller, calls

    def capture(self, output_path, caller, output_root):
        return tool.capture_schema(
            self.version,
            output_path,
            caller=caller,
            output_root=output_root,
        )

    def test_capture_filters_methods_and_writes_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "schema.json"
            methods = dict(self.methods)
            methods["system.unrelated_private_data"] = {"example": "must not be copied"}
            caller, calls = self.caller_for(methods)

            path, capture = self.capture(output, caller=caller, output_root=root)

            self.assertEqual(path, output)
            self.assertEqual(calls, ["system.version", "core.get_methods"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            decoded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(decoded["truenas_version"], self.version)
            self.assertEqual(set(decoded["methods"]), set(tool.METHODS_TO_CAPTURE))
            self.assertNotIn("system.unrelated_private_data", decoded["methods"])
            self.assertEqual(decoded["missing_methods"], [])
            self.assertIn("do not commit", decoded["warning"])
            self.assertEqual(capture["available_method_count"], len(methods))

    def test_missing_methods_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            methods = dict(self.methods)
            methods.pop("vm.device.create")
            methods.pop("vm.device.convert")
            caller, calls = self.caller_for(methods)

            path, capture = self.capture(root / "schema.json", caller=caller, output_root=root)

            self.assertIn("vm.device.create", capture["missing_methods"])
            self.assertIn("vm.device.convert", capture["missing_methods"])
            self.assertNotIn("vm.device.create", capture["methods"])
            self.assertNotIn("vm.device.convert", capture["methods"])
            self.assertEqual(calls, ["system.version", "core.get_methods"])
            self.assertTrue(path.is_file())

    def test_capture_requests_conversion_metadata_without_calling_the_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller, calls = self.caller_for()
            _, capture = self.capture(root / "schema.json", caller=caller, output_root=root)
            self.assertIn("vm.device.convert", capture["requested_methods"])
            self.assertIn("vm.device.convert", capture["methods"])
            self.assertEqual(calls, ["system.version", "core.get_methods"])
            self.assertNotIn("vm.device.convert", calls)

    def test_expected_version_accepts_json_encoded_midclt_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller, _ = self.caller_for()
            path, capture = tool.capture_schema(
                json.dumps(self.version),
                root / "schema.json",
                caller=caller,
                output_root=root,
            )
            self.assertEqual(capture["truenas_version"], self.version)
            self.assertTrue(path.is_file())

    def test_wrong_version_aborts_before_schema_query_or_file_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "schema.json"
            caller, calls = self.caller_for(version="TrueNAS-OTHER-2")

            with self.assertRaisesRegex(tool.CaptureError, "expected TrueNAS-TEST-1"):
                self.capture(output, caller=caller, output_root=root)

            self.assertEqual(calls, ["system.version"])
            self.assertFalse(output.exists())

    def test_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "schema.json"
            output.write_text("keep this", encoding="utf-8")

            with self.assertRaisesRegex(tool.CaptureError, "Refusing to overwrite"):
                tool.write_private_new_file(output, "replacement", root)

            self.assertEqual(output.read_text(encoding="utf-8"), "keep this")

    def test_output_must_stay_under_selected_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(tool.CaptureError, "must remain under"):
                tool.validate_output_parent(Path("/mnt/pool/schema.json"), root)

    def test_symlinked_output_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(tool.CaptureError, "symlink"):
                tool.validate_output_parent(link / "schema.json", root)

    def test_method_response_must_be_valid_json_object(self):
        with self.assertRaisesRegex(tool.CaptureError, "valid JSON"):
            tool.decode_methods("not json")
        with self.assertRaisesRegex(tool.CaptureError, "unexpected structure"):
            tool.decode_methods("[]")

    def test_oversized_method_response_is_rejected(self):
        original = tool.MAX_METHODS_RESPONSE_BYTES
        try:
            tool.MAX_METHODS_RESPONSE_BYTES = 4
            with self.assertRaisesRegex(tool.CaptureError, "64 MiB parsing limit"):
                tool.decode_methods('{"x": 1}')
        finally:
            tool.MAX_METHODS_RESPONSE_BYTES = original

    def test_malformed_selected_method_entry_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            methods: dict[str, object] = dict(self.methods)
            methods["vm.create"] = "not a schema object"
            caller, _ = self.caller_for(methods)
            with self.assertRaisesRegex(tool.CaptureError, "vm.create is not an object"):
                self.capture(root / "schema.json", caller=caller, output_root=root)
            self.assertFalse((root / "schema.json").exists())

    def test_version_accepts_json_string_and_plain_text(self):
        self.assertEqual(tool.decode_version(json.dumps(self.version)), self.version)
        self.assertEqual(tool.decode_version(self.version), self.version)
        with self.assertRaisesRegex(tool.CaptureError, "unexpected value"):
            tool.decode_version("26.0.0")

    def test_missing_midclt_and_timeout_fail_safely(self):
        with mock.patch.object(tool.shutil, "which", return_value=None):
            with self.assertRaisesRegex(tool.CaptureError, "midclt is unavailable"):
                tool.run_midclt("system.version")

        with mock.patch.object(tool.shutil, "which", return_value="/usr/bin/midclt"), mock.patch.object(
            tool.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["midclt"], tool.MIDCLT_TIMEOUT_SECONDS),
        ):
            with self.assertRaisesRegex(tool.CaptureError, "timed out"):
                tool.run_midclt("core.get_methods")

    def test_no_other_middleware_method_can_be_called(self):
        with self.assertRaisesRegex(tool.CaptureError, "Only system.version and core.get_methods"):
            tool.run_midclt("vm.create")

    def test_default_output_path_is_version_sanitized(self):
        path = tool.default_output_path("TrueNAS-TEST-1/BETA", Path("/tmp"))
        self.assertEqual(path.parent, Path("/tmp"))
        self.assertEqual(path.name, "truenas-linux-vm-installer-methods-TrueNAS-TEST-1_BETA.json")


if __name__ == "__main__":
    unittest.main()
