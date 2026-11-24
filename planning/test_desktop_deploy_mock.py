"""Offline deployment sequencing regression tests; never touches TrueNAS."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning.desktop_deploy_mock import (
    DesktopRequest, FakeBackend, DeploymentRefused, rehearse, resource_names,
)
from planning.desktop_integrity import verify_staged_iso

PASS_VERIFY = lambda: None  # test-only fake verifier; production code has no default
PASS_PIN = {"sha256": "0" * 64, "size_bytes": 2 * 1024 * 1024}  # test-only seed pin; production has no default

REQUEST = DesktopRequest("desktop-test", "testpool", "br0", 2, 6144, 32)

class DesktopDeployMockTests(unittest.TestCase):
    def test_confirmation_required_before_writes(self):
        backend = FakeBackend()
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend)
        self.assertEqual(backend.events, ["preflight"])
        self.assertFalse(backend.resources["vm"])

    def test_success_sequence(self):
        backend = FakeBackend()
        steps = rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(steps[-1], "installer-running")
        self.assertEqual(backend.events, [
            "preflight", "create_parent_datasets", "create_disk", "create_vm", "attach_display",
            "verify_display", "attach_installer", "attach_seed", "start", "status",
        ])
        shared_parent = "testpool/linux-vms"
        vm_parent = "testpool/linux-vms/desktop-test"
        disk_path = vm_parent + "/disk0"
        self.assertEqual(backend.datasets, {
            shared_parent: "FILESYSTEM", vm_parent: "FILESYSTEM", disk_path: "VOLUME",
        })
        self.assertEqual(backend.owned_datasets, {shared_parent, vm_parent, disk_path})

    def test_existing_vm_refused(self):
        backend = FakeBackend()
        backend.resources["vm"].add("desktop-test")
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(backend.events, ["preflight"])

    def test_partial_failure_does_not_auto_delete(self):
        backend = FakeBackend(fail_at="attach_seed")
        with self.assertRaisesRegex(DeploymentRefused, "reconcile resources manually"):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertIn("desktop-test", backend.resources["vm"])
        self.assertEqual(backend.states["desktop-test"], "STOPPED")

    def test_arbitrary_backend_cannot_be_used(self):
        class RealishBackend(FakeBackend):
            pass
        with self.assertRaisesRegex(DeploymentRefused, "offline fake"):
            rehearse(REQUEST, RealishBackend(), confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)

    def test_invalid_request_cannot_create(self):
        backend = FakeBackend()
        bad = DesktopRequest("bad/name", "testpool", "br0", 2, 6144, 32)
        with self.assertRaisesRegex(DeploymentRefused, "offline planner rejected"):
            rehearse(bad, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(backend.events, [])
        self.assertFalse(backend.resources["vm"])

    def test_preflight_refuses_unavailable_pool_and_bridge(self):
        cases = (
            (FakeBackend(pools={"otherpool": {"healthy": True, "available_bytes": 100 * 1024**3}}),
             "pool is missing or unhealthy"),
            (FakeBackend(bridges={"other-bridge"}), "offline planner rejected"),
        )
        for backend, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(DeploymentRefused, message):
                    rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
                self.assertEqual(backend.events, ["preflight"])
                self.assertFalse(backend.resources["vm"])
                self.assertFalse(backend.resources["disk"])

    def test_preflight_refuses_unhealthy_or_insufficient_pool(self):
        from planning.ubuntu_desktop import STAGING_RESERVE_BYTES

        required = REQUEST.disk_gib * 1024**3 + STAGING_RESERVE_BYTES + 1024**3
        cases = (
            ({"healthy": False, "available_bytes": 100 * 1024**3}, "unhealthy"),
            ({"healthy": True, "available_bytes": required - 1}, "lacks disk, artifact staging"),
        )
        for pool, message in cases:
            backend = FakeBackend(pools={"testpool": pool})
            with self.subTest(pool=pool):
                with self.assertRaisesRegex(DeploymentRefused, message):
                    rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
                self.assertEqual(backend.events, ["preflight"])
                self.assertFalse(backend.resources["vm"])
                self.assertFalse(backend.resources["disk"])

    def test_preflight_refuses_insufficient_host_capability(self):
        cases = (
            (FakeBackend(supports_virtualization=False), "virtualization support"),
            (FakeBackend(maximum_vcpus=1), "exceeds mock host support"),
            (FakeBackend(available_memory_mib=6144), "below the request plus reserve"),
        )
        for backend, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(DeploymentRefused, message):
                    rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
                self.assertEqual(backend.events, ["preflight"])
                self.assertFalse(backend.resources["vm"])
                self.assertFalse(backend.resources["disk"])

    def test_preflight_refuses_existing_target_dataset(self):
        backend = FakeBackend(datasets={resource_names(REQUEST)[1]: "VOLUME"})
        with self.assertRaisesRegex(DeploymentRefused, "offline planner rejected.*storage path"):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(backend.events, ["preflight"])
        self.assertFalse(backend.resources["vm"])
        self.assertFalse(backend.resources["disk"])

    def test_existing_shared_filesystem_is_reused_without_claiming_ownership(self):
        shared_parent = "testpool/linux-vms"
        backend = FakeBackend(datasets={shared_parent: "FILESYSTEM"})
        rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(backend.datasets[shared_parent], "FILESYSTEM")
        self.assertNotIn(shared_parent, backend.owned_datasets)
        self.assertEqual(
            backend.owned_datasets,
            {"testpool/linux-vms/desktop-test", "testpool/linux-vms/desktop-test/disk0"},
        )

    def test_wrong_type_shared_parent_is_refused_before_confirmation(self):
        shared_parent = "testpool/linux-vms"
        backend = FakeBackend(datasets={shared_parent: "VOLUME"})
        with self.assertRaisesRegex(DeploymentRefused, "shared parent is not a filesystem"):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertEqual(backend.events, ["preflight"])
        self.assertEqual(backend.datasets, {shared_parent: "VOLUME"})
        self.assertEqual(backend.owned_datasets, set())


class StagedIsoGateTests(unittest.TestCase):
    """Real verify_staged_iso over temp files; only the pinned size/digest are patched."""

    DATA = b"fake-staged-iso"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.iso = Path(self.tmp.name) / "ubuntu-installer.iso"
        self.iso.write_bytes(self.DATA)
        self.mod = sys.modules[verify_staged_iso.__module__]

    def run_rehearsal(self, size=None, digest=None, path=None):
        size = len(self.DATA) if size is None else size
        digest = hashlib.sha256(self.DATA).hexdigest() if digest is None else digest
        backend = FakeBackend()
        with patch.object(self.mod.desktop, "ARTIFACT_SIZE_BYTES", size), \
                patch.object(self.mod.desktop, "ARTIFACT_SHA256", digest):
            verify = lambda: verify_staged_iso(path or self.iso)
            return backend, rehearse(REQUEST, backend, confirmation="CREATE",
                                     verify_staged_media=verify, seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)

    def test_match_passes_and_attaches(self):
        backend, steps = self.run_rehearsal()
        self.assertIn("staged-iso-verified", steps)
        self.assertIn("attach_installer", backend.events)

    def test_wrong_size_aborts_before_attach(self):
        backend = FakeBackend()
        with patch.object(self.mod.desktop, "ARTIFACT_SIZE_BYTES", len(self.DATA) + 1), \
                self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE",
                     verify_staged_media=lambda: verify_staged_iso(self.iso), seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertNotIn("attach_installer", backend.events)

    def test_wrong_sha256_aborts_before_attach(self):
        backend = FakeBackend()
        with patch.object(self.mod.desktop, "ARTIFACT_SIZE_BYTES", len(self.DATA)), \
                patch.object(self.mod.desktop, "ARTIFACT_SHA256", "0" * 64), \
                self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE",
                     verify_staged_media=lambda: verify_staged_iso(self.iso), seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertNotIn("attach_installer", backend.events)

    def test_missing_file_aborts_before_attach(self):
        backend = FakeBackend()
        missing = Path(self.tmp.name) / "absent.iso"
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE",
                     verify_staged_media=lambda: verify_staged_iso(missing), seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertNotIn("attach_installer", backend.events)

    def test_missing_verifier_refused_before_any_write(self):
        backend = FakeBackend()
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE")
        self.assertFalse(any(e.startswith(("create", "attach")) for e in backend.events))

    def test_media_check_failure_precedes_disk_and_vm_creation(self):
        backend = FakeBackend()
        with patch.object(self.mod.desktop, "ARTIFACT_SIZE_BYTES", len(self.DATA)), \
                patch.object(self.mod.desktop, "ARTIFACT_SHA256", "0" * 64):
            with self.assertRaises(DeploymentRefused):
                rehearse(REQUEST, backend, confirmation="CREATE",
                         verify_staged_media=lambda: verify_staged_iso(self.iso),
                         seed_pin=PASS_PIN, verify_seed=PASS_VERIFY)
        self.assertNotIn("create_disk", backend.events)
        self.assertNotIn("create_vm", backend.events)

    def test_verification_runs_before_disk_and_vm(self):
        backend, steps = self.run_rehearsal()
        self.assertLess(steps.index("staged-seed-verified"), steps.index("disk-created"))
        self.assertLess(steps.index("staged-iso-verified"), steps.index("vm-created"))


if __name__ == "__main__":
    unittest.main()
