"""Offline deployment sequencing regression tests; never touches TrueNAS."""
import unittest
from planning.desktop_deploy_mock import DesktopRequest, FakeBackend, DeploymentRefused, rehearse

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
            "preflight", "create_disk", "create_vm", "attach_installer",
            "attach_seed", "attach_display", "start", "status",
        ])

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
        with self.assertRaises(ValueError):
            rehearse(bad, backend, confirmation="CREATE")
        self.assertEqual(backend.events, [])

if __name__ == "__main__":
    unittest.main()
