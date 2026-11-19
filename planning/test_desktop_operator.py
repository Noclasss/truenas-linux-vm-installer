"""Offline command rehearsal with synthetic inputs and fake middleware only."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from planning import desktop_operator as operator
from planning import desktop_live_writer as writer_module
from planning.desktop_live_writer import DesktopCreateOnlyWriter
from planning.desktop_write_contracts import load_reviewed_desktop_fixture
from planning.test_desktop_live_writer import FakeMiddleware


class DesktopOperatorCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.operations = self.root / "operations"
        self.registry = self.root / "targets"
        self.mountpoint = self.root / "synthetic-mount"
        self.mountpoint.mkdir(mode=0o700)
        self.fixture = load_reviewed_desktop_fixture()
        self.iso_bytes = b"synthetic ISO bytes for offline operator rehearsal"
        self.seed_bytes = b"synthetic NoCloud seed bytes"
        self.iso = self.inputs / "ubuntu-desktop.iso"
        self.seed = self.inputs / "cidata.img"
        self.iso.write_bytes(self.iso_bytes)
        self.seed.write_bytes(self.seed_bytes)
        os.chmod(self.seed, 0o600)
        self.checksums = self.inputs / "SHA256SUMS"
        self.signature = self.inputs / "SHA256SUMS.gpg"
        self.keyring = self.inputs / "ubuntu-keyring.gpg"
        self.checksums.write_text("synthetic signed manifest input\n")
        self.signature.write_bytes(b"synthetic detached signature")
        self.keyring.write_bytes(b"synthetic public keyring")
        accounts = [row for row in pwd.getpwall() if row.pw_uid > 0 and row.pw_name.isascii()]
        if os.geteuid() > 0:
            self.account = pwd.getpwuid(os.geteuid()).pw_name
        elif accounts:
            self.account = accounts[0].pw_name
        else:
            self.skipTest("no non-root service account is available")
        self.record_path = self.root / "desktop-target.json"
        self.record = self._record()
        self._write_record(self.record_path, self.record)

    def _record(self, manifest_name: str | None = None):
        return {
            "schema_version": 1,
            "expected_hostname": "fakehost",
            "expected_build": self.fixture["truenas_version"],
            "vm_name": "DesktopTest",
            "pool_name": "tanktest",
            "vcpus": 2,
            "memory_mib": 6144,
            "disk_gib": 32,
            "host_reserve_mib": 1024,
            "pool_reserve_mib": 1024,
            "source_id": "ubuntu-desktop-minimal",
            "network": "none",
            "cache_on_selected_pool": False,
            "iso_path": os.fspath(self.iso),
            "checksum_manifest_path": os.fspath(self.checksums),
            "signature_path": os.fspath(self.signature),
            "keyring_path": os.fspath(self.keyring),
            "seed_path": os.fspath(self.seed),
            "seed_sha256": hashlib.sha256(self.seed_bytes).hexdigest(),
            "service_account": self.account,
            "manifest_path": os.fspath(self.operations / (manifest_name or f"{uuid.uuid4()}.json")),
        }

    @staticmethod
    def _write_record(path: Path, record: dict):
        path.write_text(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(path, 0o600)

    def test_target_reader_rejects_symlink_directory_fifo_and_oversized_files(self):
        link = self.root / "target-link.json"
        link.symlink_to(self.record_path)
        fifo = self.root / "target-fifo"
        os.mkfifo(fifo, 0o600)
        oversized = self.root / "oversized.json"
        oversized.write_bytes(b" " * (operator._MAX_TARGET_BYTES + 1))
        os.chmod(oversized, 0o600)
        for path in (link, self.root, fifo, oversized):
            with self.subTest(path=path), self.assertRaises(operator.OperatorWorkflowError):
                operator._read_target(path)

    def test_target_reader_uses_opened_inode_if_path_is_replaced(self):
        replacement = self.root / "replacement.json"
        other = dict(self.record, vm_name="AttackerRecord")
        self._write_record(replacement, other)
        original = os.open
        moved = self.root / "original-opened.json"

        def open_then_replace(path, flags, *args, **kwargs):
            fd = original(path, flags, *args, **kwargs)
            if Path(path) == self.record_path:
                self.record_path.rename(moved)
                self.record_path.symlink_to(replacement)
            return fd

        with patch.object(operator.os, "open", side_effect=open_then_replace):
            loaded = operator._read_target(self.record_path)
        self.assertEqual(loaded["vm_name"], "DesktopTest")
        self.assertEqual(self.record_path.resolve(), replacement.resolve())

    @contextmanager
    def _runtime(self, fake: FakeMiddleware, *, verifier_ok=True):
        with ExitStack() as stack:
            stack.enter_context(patch.object(operator, "OPERATIONS_ROOT", self.operations))
            stack.enter_context(patch.object(operator, "TARGET_REGISTRY_ROOT", self.registry))
            stack.enter_context(patch.object(writer_module.desktop, "ARTIFACT_SIZE_BYTES", len(self.iso_bytes)))
            stack.enter_context(patch.object(writer_module.desktop, "ARTIFACT_SHA256", hashlib.sha256(self.iso_bytes).hexdigest()))
            stack.enter_context(patch.object(writer_module.desktop, "SEED_MEDIA_SIZE_BYTES", 1024))
            stack.enter_context(patch.object(operator, "MidcltClient", side_effect=lambda **kwargs: fake))
            stack.enter_context(patch.object(operator, "verify_desktop_artifacts", return_value={
                "filename": writer_module.desktop.IMAGE_FILENAME,
                "sha256": hashlib.sha256(self.iso_bytes).hexdigest(),
                "size_bytes": len(self.iso_bytes),
                "signing_key_fingerprint": writer_module.desktop.SIGNING_KEY_FINGERPRINT,
                "manifest_sha256": "a" * 64, "signature_verified": verifier_ok,
                "iso_bytes_verified": verifier_ok, "live_install_allowed": False,
            }))
            stack.enter_context(patch.object(operator, "verify_desktop_source_id", return_value={
                "source_id": "ubuntu-desktop-minimal", "source_ids": ["ubuntu-desktop-minimal"],
                "catalogue_sha256": "b" * 64, "verified_from_iso": True,
            }))
            self.access_probe = stack.enter_context(patch.object(operator, "_safe_read_probe", return_value=True))
            stack.enter_context(patch.object(operator.shutil, "which", return_value="/usr/sbin/runuser"))
            stack.enter_context(patch.object(
                writer_module, "_verify_vm_process_account", return_value=pwd.getpwnam(self.account),
            ))
            stack.enter_context(patch.object(DesktopCreateOnlyWriter, "_require_root", return_value=None))
            stack.enter_context(patch.object(writer_module, "_exact_mountpoint", return_value=self.mountpoint))
            yield

    def _run(self, argv, *, input_value=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = operator.main(argv, input_fn=(lambda _prompt: input_value) if input_value is not None else None)
        return rc, out.getvalue(), err.getvalue()

    def _phrase(self, fake):
        report, writer, _ = operator._check_target(self.record_path, client_factory=lambda **kwargs: fake)
        writer.client.close()
        return report["confirmation_phrase"]

    def test_check_is_read_only_and_missing_operations_directory_stays_missing(self):
        fake = FakeMiddleware(self.fixture)
        with self._runtime(fake):
            rc, output, _ = self._run(["check", "--target", os.fspath(self.record_path)])
            self.assertEqual(rc, 0)
            report = json.loads(output)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["verified_facts"]["host"]["hostname"], "fakehost")
            self.assertEqual(report["operator_supplied"]["expected_hostname"], "fakehost")
            self.assertEqual(report["verified_facts"]["middleware_write_methods_called"], [])
            self.assertTrue(report["staged_file_access"]["account_existence_verified"])
            self.assertTrue(report["staged_file_access"]["account_nonroot_verified"])
            self.assertTrue(report["staged_file_access"]["runuser_local_probe_verified"])
            self.assertEqual(
                report["staged_file_access"]["staged_copy_probe"],
                "pending; actual host runuser open check runs after local staging and before vm.create",
            )
            self.access_probe.assert_not_called()
            self.assertEqual(fake.writes, [])
            self.assertFalse(self.operations.exists())
            inspect_rc, _, inspect_err = self._run(["inspect", "--manifest", os.fspath(self.operations / "missing.json")])
            self.assertEqual(inspect_rc, 1)
            self.assertTrue(inspect_err)
            self.assertFalse(self.operations.exists())

    def test_command_rehearsal_create_display_timeout_fresh_process_inspection_and_duplicate_refusal(self):
        fake = FakeMiddleware(self.fixture, device_readback_issue="error")
        with self._runtime(fake):
            phrase = self._phrase(fake)
            rc, output, _ = self._run(["create", "--target", os.fspath(self.record_path)], input_value=phrase)
            self.assertEqual(rc, 1)
            self.assertEqual([method for method, _ in fake.writes], [
                "pool.dataset.create", "pool.dataset.create", "pool.dataset.create", "vm.create", "vm.device.create",
            ])
            manifest_path = Path(self.record["manifest_path"])
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["status"], "reconcile_required")
            self.assertEqual(manifest["pending"]["method"], "vm.device.create")
            self.assertEqual(manifest["pending"]["api_id"], 91)
            self.assertEqual(manifest["pending"]["vm_id"], 41)
            self.assertEqual(manifest["pending"]["state"], "ambiguous")
            self.assertNotIn("password", json.dumps(manifest))

            repo_root = Path(__file__).resolve().parents[1]
            state_path = self.root / "fake-inventory.json"
            state_path.write_text(json.dumps({
                "hostname": fake.hostname,
                "datasets": list(fake.datasets.values()),
                "vms": list(fake.vms.values()),
                "devices": list(fake.devices.values()),
            }))
            child_code = '''
import json, os, sys
from pathlib import Path
from planning import desktop_operator as operator
from planning.desktop_write_contracts import load_reviewed_desktop_fixture
from planning.test_desktop_live_writer import FakeMiddleware
fixture = load_reviewed_desktop_fixture()
state = json.loads(Path(sys.argv[2]).read_text())
fake = FakeMiddleware(fixture, hostname=state["hostname"])
fake.datasets = {row["id"]: row for row in state["datasets"]}
fake.vms = {row["id"]: row for row in state["vms"]}
fake.devices = {row["id"]: row for row in state["devices"]}
operator.MidcltClient = lambda **kwargs: fake
raise SystemExit(operator.main(["inspect", "--manifest", sys.argv[1], "--live-inventory"]))
'''
            env = {"PATH": os.defpath, "PYTHONPATH": os.fspath(repo_root), "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"}
            child = subprocess.run(
                [sys.executable, "-c", child_code, os.fspath(manifest_path), os.fspath(state_path)],
                cwd=repo_root, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            inspected = json.loads(child.stdout)
            self.assertEqual(inspected["manifest_recorded"]["returned_api_id"], 91)
            self.assertEqual(inspected["manifest_recorded"]["associated_vm_id"], 41)
            self.assertEqual(inspected["manifest_recorded"]["pending_operation"]["state"], "ambiguous")
            self.assertTrue(inspected["second_create_blocked_by_manifest_state"])
            self.assertTrue(inspected["fresh_inventory"]["performed"])
            self.assertTrue(any(row.get("id") == 41 for row in inspected["fresh_inventory"]["confirms"]["vm_rows_by_recorded_id_or_name"]))
            fresh_devices = inspected["fresh_inventory"]["confirms"]["device_rows_by_returned_id_or_vm_id"]
            self.assertTrue(any(row.get("id") == 91 and row.get("vm") == 41 for row in fresh_devices))
            self.assertNotIn("password", child.stdout)
            self.assertEqual(inspected["fresh_inventory"]["write_methods_called"], [])

            changed = self._record(f"{uuid.uuid4()}.json")
            second_target = self.root / "second-target.json"
            self._write_record(second_target, changed)
            calls_before = len(fake.calls)
            second_rc, _, second_err = self._run(["create", "--target", os.fspath(second_target)], input_value=phrase)
            self.assertEqual(second_rc, 1)
            self.assertIn("operation record", second_err)
            self.assertEqual(len(fake.calls), calls_before)
            self.assertEqual(len(fake.writes), 5)

    def test_command_success_creates_stopped_no_nic_vm(self):
        fake = FakeMiddleware(self.fixture)
        with self._runtime(fake):
            phrase = self._phrase(fake)
            rc, output, _ = self._run(["create", "--target", os.fspath(self.record_path)], input_value=phrase)
            self.assertEqual(rc, 0)
            result = json.loads(output)
            self.assertEqual(result["status"], "create_returned")
            self.assertEqual(result["result"]["status"], "created_not_started_live_unverified")
            self.assertFalse(result["result"]["vm_started"])
            manifest = json.loads(Path(result["result"]["manifest_path"]).read_text())
            self.assertEqual(result["verified_plan"]["proposed_plan"], manifest["plan"])
            vm = fake.vms[41]
            self.assertEqual(vm["status"]["domain_state"], "STOPPED")
            self.assertIs(vm["autostart"], False)
            vm_payload = next(params[0] for method, params in fake.writes if method == "vm.create")
            self.assertIs(vm_payload["autostart"], False)
            self.assertNotIn("nic_attach", json.dumps(vm_payload))
            self.assertFalse(any(method in {"vm.start", "vm.device.nic_attach_choices"} for method, _ in fake.writes))
            self.assertFalse(any(row["attributes"].get("dtype") == "NIC" for row in fake.devices.values()))

    def test_cli_wrong_confirmation_refuses_before_first_write(self):
        fake = FakeMiddleware(self.fixture)
        with self._runtime(fake):
            rc, _, err = self._run(["create", "--target", os.fspath(self.record_path)], input_value="CREATE DesktopTest wrong")
            self.assertEqual(rc, 1)
            self.assertIn("confirmation did not match", err)
            self.assertEqual(fake.writes, [])
            self.assertFalse(self.operations.exists())

    def test_unreadable_history_fails_closed_before_middleware_calls(self):
        fake = FakeMiddleware(self.fixture)
        with self._runtime(fake):
            self.operations.mkdir(mode=0o700)
            history = self.operations / "unreadable.json"
            history.write_text("{}")
            os.chmod(history, 0o600)
            original_read_text = Path.read_text

            def deny_history(path, *args, **kwargs):
                if path == history:
                    raise PermissionError("synthetic unreadable history")
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", deny_history):
                rc, _, err = self._run(
                    ["create", "--target", os.fspath(self.record_path)],
                    input_value="irrelevant",
                )
            self.assertEqual(rc, 1)
            self.assertIn("operation records could not be scanned", err)
            self.assertEqual(fake.calls, [])
            self.assertEqual(fake.writes, [])

    def test_failed_verification_wrong_host_and_malformed_history_stop_before_writes(self):
        failing_fake = FakeMiddleware(self.fixture)
        with self._runtime(failing_fake, verifier_ok=False):
            rc, _, _ = self._run(["create", "--target", os.fspath(self.record_path)], input_value="irrelevant")
            self.assertEqual(rc, 1)
            self.assertEqual(failing_fake.calls, [])
            self.assertEqual(failing_fake.writes, [])
            self.assertFalse(self.operations.exists())

        other_host = FakeMiddleware(self.fixture, hostname="differenthost")
        with self._runtime(other_host):
            rc, _, _ = self._run(["check", "--target", os.fspath(self.record_path)])
            self.assertEqual(rc, 1)
            self.assertEqual(other_host.writes, [])
            self.assertFalse(self.operations.exists())

        malformed_fake = FakeMiddleware(self.fixture)
        with self._runtime(malformed_fake):
            self.operations.mkdir(mode=0o700)
            (self.operations / "unknown-history.json").write_text("not a writer manifest")
            os.chmod(self.operations / "unknown-history.json", 0o600)
            rc, _, err = self._run(["create", "--target", os.fspath(self.record_path)], input_value="irrelevant")
            self.assertEqual(rc, 1)
            self.assertIn("operation records could not be scanned", err)
            self.assertEqual(malformed_fake.calls, [])
            self.assertEqual(malformed_fake.writes, [])


if __name__ == "__main__":
    unittest.main()
