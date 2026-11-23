"""Offline post-install media-detach checks; never touches TrueNAS."""
import unittest

from planning.desktop_deploy_mock import DeploymentRefused, DesktopRequest, FakeBackend, rehearse, resource_names
from planning.desktop_finalize import expected_media, finish_installation, verify_media_readback
from planning.desktop_write_contracts import build_desktop_write_intents, load_reviewed_desktop_fixture

REQUEST = DesktopRequest("desktop-test", "testpool", "br0", 2, 6144, 32)


def installed_backend(**kwargs):
    """A fake that completed the create sequence and whose guest powered off."""
    backend = FakeBackend(**kwargs)
    rehearse(REQUEST, backend, confirmation="CREATE")
    return backend


class DesktopFinalizeTests(unittest.TestCase):
    def test_refuses_while_guest_still_running(self):
        backend = installed_backend()
        with self.assertRaisesRegex(DeploymentRefused, "poweroff not confirmed"):
            finish_installation(backend, REQUEST)
        self.assertEqual({d["dtype"] for d in backend.media_devices["desktop-test"]},
                         {"DISK", "CDROM", "RAW"})
        self.assertFalse(any(e.startswith("detach_") for e in backend.events))

    def test_detaches_only_media_after_poweroff(self):
        backend = installed_backend()
        backend.simulate_guest_poweroff("desktop-test")
        steps = finish_installation(backend, REQUEST)
        self.assertEqual(steps, ("installer-media-detached", "seed-media-detached", "media-detach-verified"))
        self.assertEqual(backend.device_list("desktop-test"),
                         [{"dtype": "DISK", "path": "/dev/zvol/testpool/linux-vms/desktop-test/disk0"}])
        self.assertEqual(backend.states["desktop-test"], "STOPPED")
        self.assertIn("desktop-test", backend.resources["vm"])
        self.assertNotIn("start", backend.events[backend.events.index("guest_poweroff") + 1:])

    def test_readback_rejects_missing_seed_before_any_detach(self):
        backend = installed_backend()
        backend.simulate_guest_poweroff("desktop-test")
        backend.media_devices["desktop-test"] = [d for d in backend.media_devices["desktop-test"] if d["dtype"] != "RAW"]
        with self.assertRaisesRegex(DeploymentRefused, "exactly one RAW"):
            finish_installation(backend, REQUEST)
        self.assertFalse(any(e.startswith("detach_") for e in backend.events))

    def test_readback_rejects_wrong_cdrom_path(self):
        backend = installed_backend()
        backend.simulate_guest_poweroff("desktop-test")
        for device in backend.media_devices["desktop-test"]:
            if device["dtype"] == "CDROM":
                device["path"] = "/mnt/testpool/other/ubuntu-installer.iso"
        with self.assertRaisesRegex(DeploymentRefused, "CDROM path does not match"):
            finish_installation(backend, REQUEST)
        self.assertFalse(any(e.startswith("detach_") for e in backend.events))

    def test_readback_rejects_duplicate_and_unplanned_devices(self):
        for extra, message in (
            ({"dtype": "CDROM", "path": "/mnt/x"}, "exactly one CDROM"),
            ({"dtype": "NIC", "nic_attach": "br0"}, "unplanned device types"),
        ):
            with self.subTest(extra=extra["dtype"]):
                backend = installed_backend()
                backend.simulate_guest_poweroff("desktop-test")
                backend.media_devices["desktop-test"].append(extra)
                with self.assertRaisesRegex(DeploymentRefused, message):
                    finish_installation(backend, REQUEST)

    def test_partial_detach_is_reported_not_retried(self):
        backend = installed_backend(fail_at="detach_raw")
        backend.simulate_guest_poweroff("desktop-test")
        with self.assertRaisesRegex(DeploymentRefused, "reconcile manually; completed steps: installer-media-detached"):
            finish_installation(backend, REQUEST)
        remaining = {d["dtype"] for d in backend.media_devices["desktop-test"]}
        self.assertEqual(remaining, {"DISK", "RAW"})
        self.assertEqual(backend.events.count("detach_cdrom"), 1)
        self.assertNotIn("detach_raw", backend.events)  # injected failure is not logged

    def test_arbitrary_backend_cannot_be_used(self):
        class RealishBackend(FakeBackend):
            pass
        with self.assertRaisesRegex(DeploymentRefused, "offline fake"):
            finish_installation(RealishBackend(), REQUEST)

    def test_planned_media_paths_match_reviewed_write_intents(self):
        fixture = load_reviewed_desktop_fixture()
        intents = build_desktop_write_intents(REQUEST, fixture, vm_id=7)
        attributes = [params[0]["attributes"] for method, params in intents if method == "vm.device.create"]
        cdrom = next(a for a in attributes if a["dtype"] == "CDROM")
        raw = next(a for a in attributes if a["dtype"] == "RAW")
        self.assertEqual(expected_media(REQUEST), {"CDROM": cdrom["path"], "RAW": raw["path"]})

    def test_verify_rejects_disk_path_mismatch(self):
        _, disk = resource_names(REQUEST)
        expected = expected_media(REQUEST)
        devices = [
            {"dtype": "DISK", "path": "/dev/zvol/testpool/other/disk0"},
            {"dtype": "CDROM", "path": expected["CDROM"]},
            {"dtype": "RAW", "path": expected["RAW"]},
        ]
        with self.assertRaisesRegex(DeploymentRefused, "disk device"):
            verify_media_readback(devices, disk, expected)


if __name__ == "__main__":
    unittest.main()
