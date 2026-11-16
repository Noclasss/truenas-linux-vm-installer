"""Persistent ownership and restart-reconciliation tests (offline fake only)."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from planning.desktop_deploy_mock import DesktopRequest, DeploymentRefused
from planning.desktop_ownership import (
    OwnershipError, PersistentFakeBackend, inspect_recovery, rehearse_persistent,
)

REQUEST = DesktopRequest("desktop-test", "testpool", "br0", 2, 6144, 32)
OPERATIONS = (
    "dataset:testpool/linux-vms",
    "dataset:testpool/linux-vms/desktop-test",
    "zvol:testpool/linux-vms/desktop-test/disk0",
    "vm:desktop-test",
    "device:desktop-test:disk",
    "device:desktop-test:installer",
    "device:desktop-test:seed",
    "device:desktop-test:nic",
    "device:desktop-test:display",
)


class DesktopOwnershipTests(unittest.TestCase):
    def path(self, directory):
        return Path(directory) / "installation.json"

    def test_success_manifest_records_identity_ownership_vm_and_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend()
            path = self.path(directory)
            installation_id = rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            data = json.loads(path.read_text())
            self.assertEqual(data["installation_id"], installation_id)
            self.assertEqual(data["vm_name"], REQUEST.name)
            self.assertEqual(data["pool"], REQUEST.pool)
            self.assertEqual(data["status"], "mock_complete_not_installed")
            self.assertEqual(data["vm_id"], backend.vm_ids[REQUEST.name])
            self.assertEqual(len(data["devices"]), 5)
            self.assertEqual(len(data["resources"]), 9)
            self.assertTrue(all(item["created_by_installation"] for item in data["resources"]))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            report = inspect_recovery(path, backend)
            self.assertFalse(report.attention)
            self.assertTrue(all(row["owned"] and row["verified"] for row in report.resources if row["identifier"] != "testpool/linux-vms"))
            self.assertFalse(report.can_continue)
            self.assertFalse(report.can_delete)

    def test_preexisting_shared_dataset_is_reused_but_never_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend(datasets={"testpool/linux-vms": "FILESYSTEM"})
            path = self.path(directory)
            rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            data = json.loads(path.read_text())
            self.assertFalse(any(row["identifier"] == "testpool/linux-vms" for row in data["resources"]))
            shared = next(row for row in inspect_recovery(path, backend).resources if row["identifier"] == "testpool/linux-vms")
            self.assertTrue(shared["exists"])
            self.assertFalse(shared["owned"])

    def test_failure_before_and_lost_response_after_every_resource_create(self):
        for when in ("before", "after"):
            for operation in OPERATIONS:
                with self.subTest(when=when, operation=operation), tempfile.TemporaryDirectory() as directory:
                    backend = PersistentFakeBackend(**{
                        "fail_before" if when == "before" else "lose_response_after": operation
                    })
                    path = self.path(directory)
                    with self.assertRaises(OwnershipError):
                        rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
                    data = json.loads(path.read_text())
                    self.assertEqual(data["status"], "reconcile_required")
                    self.assertIsNotNone(data["pending"])
                    report = inspect_recovery(path, backend)
                    self.assertFalse(report.can_continue)
                    self.assertFalse(report.can_delete)
                    self.assertTrue(report.attention)
                    pending = next(row for row in report.resources if row["identifier"] == data["pending"]["identifier"])
                    self.assertFalse(pending["owned"])
                    self.assertFalse(pending["verified"])
                    self.assertEqual(backend.operations.count(operation), 1)

    def test_duplicate_vm_name_and_existing_zvol_refuse_without_manifest(self):
        for backend in (
            PersistentFakeBackend(),
            PersistentFakeBackend(datasets={"testpool/linux-vms/desktop-test/disk0": "VOLUME"}),
        ):
            if not backend.datasets:
                backend.vms[99] = {"id": 99, "name": "DESKTOP-TEST", "disk": "other"}
            with self.subTest(existing=backend.datasets or backend.vms), tempfile.TemporaryDirectory() as directory:
                path = self.path(directory)
                with self.assertRaises(DeploymentRefused):
                    rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
                self.assertFalse(path.exists())
                self.assertEqual(backend.operations, [])

    def test_existing_incomplete_manifest_blocks_fresh_restart_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend(fail_before=OPERATIONS[0])
            path = self.path(directory)
            with self.assertRaises(OwnershipError):
                rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            original = path.read_bytes()
            before = backend.inventory()
            with self.assertRaises(OwnershipError):
                rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(backend.inventory(), before)
            self.assertEqual(backend.operations, [OPERATIONS[0]])

    def test_corrupt_manifest_fails_closed_without_inventory_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.path(directory)
            path.write_text("{bad json", encoding="utf-8")
            os.chmod(path, 0o600)
            backend = PersistentFakeBackend()
            before = backend.inventory()
            with self.assertRaisesRegex(OwnershipError, "corrupted"):
                inspect_recovery(path, backend)
            self.assertEqual(backend.inventory(), before)
            self.assertEqual(backend.operations, [])

    def test_manifest_must_be_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend()
            path = self.path(directory)
            rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(OwnershipError, "private, trusted"):
                inspect_recovery(path, backend)

    def test_manifest_identifier_alone_cannot_claim_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend()
            path = self.path(directory)
            rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            data = json.loads(path.read_text())
            data["installation_id"] = "00000000-0000-0000-0000-000000000000"
            path.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(path, 0o600)
            report = inspect_recovery(path, backend)
            self.assertTrue(report.attention)
            self.assertFalse(any(row["owned"] for row in report.resources))

    def test_existing_unexplained_vm_is_reported_not_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentFakeBackend(fail_before=OPERATIONS[0])
            path = self.path(directory)
            with self.assertRaises(OwnershipError):
                rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE")
            backend.vms[5] = {"id": 5, "name": REQUEST.name, "disk": "unrelated"}
            # Recovery is read-only and reports a name collision even if no creation was acknowledged.
            data = json.loads(path.read_text(encoding="utf-8"))
            data["resources"] = []
            data["devices"] = []
            data["vm_id"] = None
            path.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(path, 0o600)
            report = inspect_recovery(path, backend)
            self.assertTrue(any("unexplained VM" in item for item in report.attention))
            self.assertFalse(any(row["owned"] for row in report.resources))


if __name__ == "__main__":
    unittest.main()
