"""Offline deployment sequencing regression tests; never touches TrueNAS."""
import unittest
from planning.desktop_deploy_mock import (
    DesktopRequest, FakeBackend, DeploymentRefused, rehearse, resource_names,
)

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
        steps = rehearse(REQUEST, backend, confirmation="CREATE")
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
            rehearse(REQUEST, backend, confirmation="CREATE")
        self.assertEqual(backend.events, ["preflight"])

    def test_partial_failure_does_not_auto_delete(self):
        backend = FakeBackend(fail_at="attach_seed")
        with self.assertRaisesRegex(DeploymentRefused, "reconcile resources manually"):
            rehearse(REQUEST, backend, confirmation="CREATE")
        self.assertIn("desktop-test", backend.resources["vm"])
        self.assertEqual(backend.states["desktop-test"], "STOPPED")

    def test_arbitrary_backend_cannot_be_used(self):
        class RealishBackend(FakeBackend):
            pass
        with self.assertRaisesRegex(DeploymentRefused, "offline fake"):
            rehearse(REQUEST, RealishBackend(), confirmation="CREATE")

    def test_invalid_request_cannot_create(self):
        backend = FakeBackend()
        bad = DesktopRequest("bad/name", "testpool", "br0", 2, 6144, 32)
        with self.assertRaisesRegex(DeploymentRefused, "offline planner rejected"):
            rehearse(bad, backend, confirmation="CREATE")
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
                    rehearse(REQUEST, backend, confirmation="CREATE")
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
                    rehearse(REQUEST, backend, confirmation="CREATE")
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
                    rehearse(REQUEST, backend, confirmation="CREATE")
                self.assertEqual(backend.events, ["preflight"])
                self.assertFalse(backend.resources["vm"])
                self.assertFalse(backend.resources["disk"])

    def test_preflight_refuses_existing_target_dataset(self):
        backend = FakeBackend(datasets={resource_names(REQUEST)[1]: "VOLUME"})
        with self.assertRaisesRegex(DeploymentRefused, "offline planner rejected.*storage path"):
            rehearse(REQUEST, backend, confirmation="CREATE")
        self.assertEqual(backend.events, ["preflight"])
        self.assertFalse(backend.resources["vm"])
        self.assertFalse(backend.resources["disk"])

    def test_existing_shared_filesystem_is_reused_without_claiming_ownership(self):
        shared_parent = "testpool/linux-vms"
        backend = FakeBackend(datasets={shared_parent: "FILESYSTEM"})
        rehearse(REQUEST, backend, confirmation="CREATE")
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
            rehearse(REQUEST, backend, confirmation="CREATE")
        self.assertEqual(backend.events, ["preflight"])
        self.assertEqual(backend.datasets, {shared_parent: "VOLUME"})
        self.assertEqual(backend.owned_datasets, set())
if __name__ == "__main__":
    unittest.main()
