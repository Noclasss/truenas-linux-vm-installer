import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from typing import Any, cast
from urllib.request import Request
from unittest import mock

from installer_core import artifacts, middleware, safe_filesystem, schema


class MiddlewareClientTests(unittest.TestCase):
    def client(self, expected_version=None):
        with mock.patch.object(middleware.shutil, "which", return_value="/usr/bin/midclt"):
            return middleware.MidcltClient(timeout=7, expected_version=expected_version)

    def test_calls_midclt_with_json_args_and_timeout(self):
        client = self.client()
        result = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")
        with mock.patch.object(middleware.subprocess, "run", return_value=result) as run:
            self.assertEqual(client.call("vm.query", [["id", "=", 7]]), {"ok": True})
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/midclt", "call", "vm.query", '["id","=",7]'])
        self.assertEqual(kwargs["timeout"], 7)
        self.assertTrue(kwargs["capture_output"])

    def test_plain_text_system_version_is_narrowly_supported(self):
        client = self.client()
        result = subprocess.CompletedProcess([], 0, stdout="TrueNAS-TEST-1\n", stderr="")
        with mock.patch.object(middleware.subprocess, "run", return_value=result):
            self.assertEqual(client.call("system.version"), "TrueNAS-TEST-1")
            with self.assertRaisesRegex(middleware.MiddlewareError, "invalid JSON"):
                client.call("pool.query")

    def test_beta2_virtualization_boolean_literals_are_version_and_method_bound(self):
        for output, expected in (("True", True), ("True\n", True), ("False", False), ("False\n", False)):
            with self.subTest(output=output):
                client = self.client(expected_version="TrueNAS-26.0.0-BETA.2")
                result = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
                with mock.patch.object(middleware.subprocess, "run", return_value=result):
                    self.assertIs(client.call("vm.supports_virtualization"), expected)

        invalid_cases = (
            ("TrueNAS-26.0.0-BETA.1", "vm.supports_virtualization", "True"),
            ("TrueNAS-26.0.0-BETA.2", "pool.query", "True"),
            ("TrueNAS-26.0.0-BETA.2", "vm.supports_virtualization", "TRUE"),
            ("TrueNAS-26.0.0-BETA.2", "vm.supports_virtualization", "yes"),
        )
        for version, method, output in invalid_cases:
            with self.subTest(version=version, method=method, output=output):
                client = self.client(expected_version=version)
                result = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
                with mock.patch.object(middleware.subprocess, "run", return_value=result):
                    with self.assertRaisesRegex(middleware.MiddlewareError, "invalid JSON"):
                        client.call(method)

    def test_bad_json_and_command_errors_do_not_leak_output(self):
        client = self.client()
        bad_json = subprocess.CompletedProcess([], 0, stdout="private-host-data", stderr="")
        with mock.patch.object(middleware.subprocess, "run", return_value=bad_json):
            with self.assertRaises(middleware.MiddlewareError) as caught:
                client.call("pool.query")
        self.assertNotIn("private-host-data", str(caught.exception))
        failed = subprocess.CompletedProcess([], 1, stdout="sensitive stdout", stderr="sensitive stderr")
        with mock.patch.object(middleware.subprocess, "run", return_value=failed):
            with self.assertRaises(middleware.MiddlewareError) as caught:
                client.call("vm.create", [{}])
        self.assertNotIn("sensitive", str(caught.exception))

    def test_timeout_missing_tool_invalid_method_and_params_fail_closed(self):
        with mock.patch.object(middleware.shutil, "which", return_value=None):
            with self.assertRaisesRegex(middleware.MiddlewareError, "midclt was not found"):
                middleware.MidcltClient()
        client = self.client()
        with mock.patch.object(middleware.subprocess, "run", side_effect=subprocess.TimeoutExpired("midclt", 7)):
            with self.assertRaisesRegex(middleware.MiddlewareError, "timed out"):
                client.call("system.version")
        with self.assertRaisesRegex(middleware.MiddlewareError, "invalid middleware method"):
            client.call("vm.create;bad")
        with self.assertRaisesRegex(middleware.MiddlewareError, "positional list"):
            client.call("vm.query", cast(list[Any], {}))


class MiddlewareSchemaTests(unittest.TestCase):
    def test_valid_and_invalid_payloads_against_positional_schema(self):
        methods = {"vm.create": {"accepts": [{"type": "object", "_required_": True,
            "properties": {"name": {"type": "string", "_required_": True},
                           "memory": {"type": "integer", "minimum": 512}},
            "additionalProperties": False}]}}
        schema.validate_method_request(methods, "vm.create", [{"name": "Linux", "memory": 2048}])
        with self.assertRaisesRegex(schema.SchemaError, "expected type 'integer'"):
            schema.validate_method_request(methods, "vm.create", [{"name": "Linux", "memory": "2048"}])
        with self.assertRaisesRegex(schema.SchemaError, "field is not accepted"):
            schema.validate_method_request(methods, "vm.create", [{"name": "Linux", "memory": 2048, "extra": True}])

    def test_required_optional_and_too_many_positional_arguments(self):
        methods = {"m": {"accepts": [{"type": "integer", "_required_": True}, {"type": "string"}]}}
        schema.validate_method_request(methods, "m", [5])
        with self.assertRaisesRegex(schema.SchemaError, "omits required"):
            schema.validate_method_request(methods, "m", [])
        with self.assertRaisesRegex(schema.SchemaError, "too many"):
            schema.validate_method_request(methods, "m", [5, "x", False])

    def test_true_nas_private_accepts_key_and_conflict_handling(self):
        schema.validate_method_request({"m": {"__accepts__": [{"type": "string", "_required_": True}]}}, "m", ["ok"])
        with self.assertRaisesRegex(schema.SchemaError, "conflicting accepts"):
            schema.validate_method_request({"m": {"accepts": [], "__accepts__": [{"type": "string"}]}}, "m", [])

    def test_oneof_anyof_allof_refs_and_enum_type_semantics(self):
        root = {"definitions": {"positive": {"type": "integer", "minimum": 1}}}
        schema.validate_method_request({"m": {"accepts": [{"$ref": "#/definitions/positive"}], **root}}, "m", [3])
        with self.assertRaisesRegex(schema.SchemaError, "unresolved"):
            schema.validate_method_request({"m": {"accepts": [{"$ref": "https://bad/schema"}]}}, "m", [3])
        self.assertEqual(schema.schema_errors("x", {"oneOf": [{"type": "string"}, {"enum": ["x"]}]}),
                         ["argument: must match exactly one middleware variant; matched 2"])
        self.assertEqual(schema.schema_errors(True, {"enum": [1]}), ["argument: value is not allowed by middleware enum"])
        self.assertEqual(schema.schema_errors(3, {"allOf": [{"type": "integer"}, {"minimum": 2}]}), [])
        self.assertEqual(schema.schema_errors("ok", {"anyOf": [{"type": "integer"}, {"const": "ok"}]}), [])
        self.assertTrue(schema.schema_errors("x", {"oneOf": [{"type": "string"}], "minLength": 2}))
        self.assertTrue(schema.schema_errors("x", {"const": "x", "minLength": 2}))
        refs = {"m": {"definitions": {"positive": {"type": "integer", "minimum": 1}},
                       "accepts": [{"$ref": "#/definitions/positive", "maximum": 2}]}}
        schema.validate_method_request(refs, "m", [2])
        with self.assertRaisesRegex(schema.SchemaError, "outside middleware bounds"):
            schema.validate_method_request(refs, "m", [3])

    def test_schema_depth_and_recursive_references_fail_closed(self):
        nested = {"type": "string"}
        value = "x"
        for _ in range(schema.MAX_SCHEMA_DEPTH + 2):
            nested = {"type": "array", "items": nested}
            value = [value]
        with self.assertRaisesRegex(schema.SchemaError, "nesting limit"):
            schema.normalize_schema_contract(nested)
        self.assertTrue(any("nesting limit" in message for message in schema.schema_errors(value, nested)))

        recursive = {"definitions": {"loop": {"$ref": "#/definitions/loop"}}}
        errors = schema.schema_errors("x", {"$ref": "#/definitions/loop"}, root=recursive)
        self.assertEqual(errors, ["argument: circular middleware schema reference"])

    def test_schema_validator_rejects_unsupported_assertions_even_in_optional_fields(self):
        self.assertEqual(
            schema.normalize_schema_contract({"type": "string", "title": "Name", "maxLength": 20}),
            {"type": "string", "maxLength": 20},
        )
        for keyword in ("format", "not", "patternProperties", "minProperties", "if", "nullable"):
            with self.subTest(keyword=keyword):
                self.assertTrue(schema.schema_errors("x", {"type": "string", keyword: {}}))
        methods = {"m": {"accepts": [{"type": "object", "properties": {
            "unused": {"type": "string", "format": "hostname"},
        }}]}}
        with self.assertRaisesRegex(schema.SchemaError, "unsupported middleware schema keyword 'format'"):
            schema.validate_method_request(methods, "m", [{}])
        for malformed in (
            {"type": ["string", "invalid"]},
            {"pattern": "["},
            {"type": "array", "additionalItems": []},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(schema.SchemaError):
                schema.normalize_schema_contract(malformed)

    def test_array_and_numeric_bounds_are_checked(self):
        rules = {"type": "array", "minItems": 1, "maxItems": 2, "uniqueItems": True,
                 "items": {"type": "integer", "minimum": 0, "multipleOf": 2}}
        self.assertEqual(schema.schema_errors([2, 4], rules), [])
        self.assertTrue(schema.schema_errors([2, 2, 3], rules))
        self.assertTrue(schema.schema_errors([1], rules))
        tuple_schema = {"type": "array", "items": [{"type": "string"}], "additionalItems": False}
        self.assertTrue(schema.schema_errors(["x", "extra"], tuple_schema))
        tuple_schema["additionalItems"] = {"type": "integer"}
        self.assertEqual(schema.schema_errors(["x", 4], tuple_schema), [])
        self.assertTrue(schema.schema_errors(["x", "bad"], tuple_schema))

    def test_checked_client_loads_schema_before_write_and_blocks_invalid_write(self):
        methods = {"vm.create": {"accepts": [{"type": "object", "_required_": True,
            "properties": {"name": {"type": "string", "_required_": True}}, "additionalProperties": False}]}}
        client = mock.Mock()
        client.call.side_effect = lambda method, params=None: methods if method == "core.get_methods" else {"id": 1}
        checked = schema.SchemaCheckedClient(client, allowed_write_methods={"vm.create"})
        with self.assertRaises(schema.SchemaError):
            checked.call_write("vm.create", [{"name": "Linux", "bad": True}])
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["core.get_methods"])
        self.assertEqual(checked.call_write("vm.create", [{"name": "Linux"}]), {"id": 1})
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["core.get_methods", "vm.create"])

    def test_checked_client_defaults_to_no_write_methods(self):
        client = mock.Mock()
        checked = schema.SchemaCheckedClient(client)
        with self.assertRaisesRegex(schema.SchemaError, "not explicitly allowlisted"):
            checked.call_write("vm.create", [{}])
        with self.assertRaisesRegex(schema.SchemaError, "not explicitly allowlisted"):
            checked.call_write(["pool.destroy"], [])
        with self.assertRaisesRegex(schema.SchemaError, "read method is not explicitly allowlisted"):
            checked.call_read("vm.start", [1])
        client.call.assert_not_called()

    def test_checked_client_allows_only_explicit_read_methods(self):
        client = mock.Mock()
        client.call.return_value = [{"id": 1}]
        checked = schema.SchemaCheckedClient(client, allowed_read_methods={"vm.query"})
        self.assertEqual(checked.call_read("vm.query", []), [{"id": 1}])
        with self.assertRaisesRegex(schema.SchemaError, "read method is not explicitly allowlisted"):
            checked.call_read("vm.start", [1])
        self.assertEqual(client.call.call_args_list, [mock.call("vm.query", [])])


class SafeFilesystemTests(unittest.TestCase):
    def test_exclusive_private_file_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = safe_filesystem.write_private_new_file(root / "capture.json", "private", allowed_root=root)
            self.assertEqual(path.read_text(), "private")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(safe_filesystem.FilesystemSafetyError, "refusing to overwrite"):
                safe_filesystem.write_private_new_file(path, "replace", allowed_root=root)
            self.assertEqual(path.read_text(), "private")

    def test_rejects_outside_root_and_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(safe_filesystem.FilesystemSafetyError, "remain under"):
                safe_filesystem.ensure_trusted_directory_chain(Path("/etc"), allowed_root=root)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(safe_filesystem.FilesystemSafetyError, "symlink"):
                safe_filesystem.ensure_trusted_directory_chain(link)

    def test_atomic_update_replaces_trusted_file_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("old")
            safe_filesystem.atomic_replace_private_file(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_atomic_update_rejects_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("keep")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(safe_filesystem.FilesystemSafetyError, "unsafe or untrusted"):
                safe_filesystem.atomic_replace_private_file(link, "bad")
            self.assertEqual(target.read_text(), "keep")


class ArtifactDownloadTests(unittest.TestCase):
    class Response:
        def __init__(self, chunks, headers=None, url="https://example.test/file"):
            self.chunks = iter(chunks)
            self.headers = headers or {}
            self.url = url
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, size=-1):
            return next(self.chunks, b"")
        def geturl(self):
            return self.url

    def test_existing_nonprivate_cache_is_rejected_without_changing_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "shared"
            cache.mkdir(mode=0o755)
            cache.chmod(0o755)
            original_mode = stat.S_IMODE(cache.stat().st_mode)
            with self.assertRaisesRegex(artifacts.ArtifactError, "refusing to change existing directory permissions"):
                artifacts.ensure_private_cache_dir(cache)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), original_mode)
            self.assertEqual(original_mode, 0o755)

    def test_valid_digest_and_size_downloaded_to_private_cache(self):
        data = b"verified artifact"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            response = self.Response([data, b""], {"Content-Length": str(len(data))})
            with mock.patch.object(artifacts, "open_https_url", return_value=response):
                path = artifacts.download_and_verify("https://example.test/file", digest, Path(directory) / "cache", 100, len(data))
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.name, f"sha256-{digest}.artifact")

    def test_bad_scheme_digest_size_and_https_redirect_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(artifacts.ArtifactError, "HTTPS"):
                artifacts.download_and_verify("http://example.test/file", "a" * 64, Path(directory), 100)
            with self.assertRaisesRegex(artifacts.ArtifactError, "SHA-256"):
                artifacts.download_and_verify("https://example.test/file", "bad", Path(directory), 100)
            with self.assertRaisesRegex(artifacts.ArtifactError, "expected artifact size"):
                artifacts.download_and_verify("https://example.test/file", "a" * 64, Path(directory), 100, 101)
        handler = artifacts.HTTPSOnlyRedirectHandler()
        with self.assertRaisesRegex(artifacts.ArtifactError, "leaves HTTPS"):
            handler.redirect_request(Request("https://example.test/source"), io.BytesIO(), 302, "Found", cast(Any, Message()), "http://downgrade.test/file")

    def test_size_and_checksum_failures_remove_partial_files(self):
        data = b"payload"
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            response = self.Response([b"pay", b"load", b""])
            with mock.patch.object(artifacts, "open_https_url", return_value=response):
                with self.assertRaisesRegex(artifacts.ArtifactError, "download limit"):
                    artifacts.download_and_verify("https://example.test/file", hashlib.sha256(data).hexdigest(), cache, 3)
            self.assertEqual(list(cache.iterdir()), [])
            response = self.Response([data, b""])
            with mock.patch.object(artifacts, "open_https_url", return_value=response):
                with self.assertRaisesRegex(artifacts.ArtifactError, "does not match"):
                    artifacts.download_and_verify("https://example.test/file", "0" * 64, cache, 100)
            self.assertEqual(list(cache.iterdir()), [])

    def test_server_content_length_and_truncated_body_are_rejected(self):
        data = b"expected bytes"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            response = self.Response([data, b""], {"Content-Length": str(len(data) + 1)})
            with mock.patch.object(artifacts, "open_https_url", return_value=response):
                with self.assertRaisesRegex(artifacts.ArtifactError, "Content-Length"):
                    artifacts.download_and_verify("https://example.test/file", digest, cache, 100, len(data))
            self.assertEqual(list(cache.iterdir()), [])
            response = self.Response([data, b""])
            with mock.patch.object(artifacts, "open_https_url", return_value=response):
                with self.assertRaisesRegex(artifacts.ArtifactError, "size does not match"):
                    artifacts.download_and_verify("https://example.test/file", digest, cache, 100, len(data) + 1)
            self.assertEqual(list(cache.iterdir()), [])

    def test_verified_cache_reused_and_symlink_cache_entry_rejected(self):
        data = b"cached"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            cache.mkdir(mode=0o700)
            entry = cache / f"sha256-{digest}.artifact"
            entry.write_bytes(data)
            entry.chmod(0o600)
            with mock.patch.object(artifacts, "open_https_url") as open_url:
                path = artifacts.download_and_verify("https://example.test/file", digest, cache, 100, len(data))
            self.assertEqual(path, entry)
            open_url.assert_not_called()
            entry.unlink()
            outside = Path(directory) / "outside"
            outside.write_bytes(data)
            entry.symlink_to(outside)
            with self.assertRaisesRegex(artifacts.ArtifactError, "unsafe or unowned"):
                artifacts.download_and_verify("https://example.test/file", digest, cache, 100, len(data))


if __name__ == "__main__":
    unittest.main()
