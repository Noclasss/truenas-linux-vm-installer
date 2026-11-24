"""NoCloud seed pin: build-time digest and pre-attach re-hash (offline fake only)."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from planning.desktop_deploy_mock import DesktopRequest, DeploymentRefused, FakeBackend, rehearse
from planning.desktop_integrity import (
    DesktopIntegrityError, seed_pin_from_file, valid_seed_pin, verify_staged_seed,
)
from planning.desktop_ownership import PersistentFakeBackend, rehearse_persistent
from planning.ubuntu_desktop import SEED_MEDIA_SIZE_BYTES

REQUEST = DesktopRequest("desktop-test", "testpool", "br0", 2, 6144, 32)
PASS_ISO = lambda: None  # test-only fake ISO verifier; the ISO gate has its own tests
SEED_BYTES = b"seed-" + bytes(SEED_MEDIA_SIZE_BYTES - len(b"seed-"))


class SeedFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.seed = self.dir / "cidata.img"
        self.seed.write_bytes(SEED_BYTES)
        os.chmod(self.seed, 0o600)
        self.pin = seed_pin_from_file(self.seed)


class SeedPinTests(SeedFixture):
    def test_build_pin_is_exact_size_and_digest(self):
        self.assertEqual(self.pin, {"sha256": hashlib.sha256(SEED_BYTES).hexdigest(),
                                    "size_bytes": SEED_MEDIA_SIZE_BYTES})
        self.assertTrue(valid_seed_pin(self.pin))

    def test_build_pin_refuses_wrong_size_file(self):
        self.seed.write_bytes(SEED_BYTES[:-1])
        with self.assertRaises(DesktopIntegrityError):
            seed_pin_from_file(self.seed)

    def test_verify_match_passes(self):
        verify_staged_seed(self.seed, self.pin)

    def test_verify_refuses_wrong_hash(self):
        with self.assertRaisesRegex(DesktopIntegrityError, "SHA-256"):
            verify_staged_seed(self.seed, dict(self.pin, sha256="1" * 64))

    def test_verify_refuses_wrong_file_size(self):
        self.seed.write_bytes(SEED_BYTES + b"x")
        with self.assertRaisesRegex(DesktopIntegrityError, "size"):
            verify_staged_seed(self.seed, self.pin)

    def test_verify_refuses_missing_file(self):
        with self.assertRaises(DesktopIntegrityError):
            verify_staged_seed(self.dir / "absent.img", self.pin)

    def test_verify_refuses_missing_or_malformed_pin(self):
        for pin in (None, {}, dict(self.pin, sha256="xyz"), dict(self.pin, size_bytes=1)):
            with self.subTest(pin=pin), self.assertRaises(DesktopIntegrityError):
                verify_staged_seed(self.seed, pin)


class SeedGateRehearsalTests(SeedFixture):
    def abort_case(self, seed_path, pin):
        backend = FakeBackend()
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_ISO,
                     seed_pin=pin, verify_seed=lambda: verify_staged_seed(seed_path, pin))
        self.assertNotIn("attach_seed", backend.events)
        self.assertNotIn("attach_installer", backend.events)

    def test_rehearse_match_attaches_seed(self):
        backend = FakeBackend()
        steps = rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_ISO,
                         seed_pin=self.pin, verify_seed=lambda: verify_staged_seed(self.seed, self.pin))
        self.assertIn("staged-seed-verified", steps)
        self.assertIn("attach_seed", backend.events)

    def test_rehearse_wrong_size_aborts_before_seed_attach(self):
        self.seed.write_bytes(SEED_BYTES[:-1])
        self.abort_case(self.seed, self.pin)

    def test_rehearse_wrong_hash_aborts_before_seed_attach(self):
        self.abort_case(self.seed, dict(self.pin, sha256="1" * 64))

    def test_rehearse_missing_file_aborts_before_seed_attach(self):
        self.abort_case(self.dir / "absent.img", self.pin)

    def test_rehearse_missing_manifest_entry_refused_before_any_write(self):
        backend = FakeBackend()
        with self.assertRaises(DeploymentRefused):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_ISO,
                     seed_pin=None, verify_seed=lambda: verify_staged_seed(self.seed, self.pin))
        self.assertNotIn("attach_seed", backend.events)
        self.assertFalse(any(e.startswith(("create", "attach")) for e in backend.events))


class SeedGatePersistentTests(SeedFixture):
    def test_persistent_manifest_records_build_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installation.json"
            rehearse_persistent(REQUEST, PersistentFakeBackend(), manifest_path=path, confirmation="CREATE",
                                verify_staged_media=PASS_ISO, seed_pin=self.pin,
                                verify_seed=lambda: verify_staged_seed(self.seed, self.pin))
            self.assertEqual(json.loads(path.read_text())["seed_pin"], self.pin)

    def test_persistent_wrong_hash_attaches_no_device(self):
        backend = PersistentFakeBackend()
        bad = dict(self.pin, sha256="1" * 64)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(Exception):
            rehearse_persistent(REQUEST, backend, manifest_path=Path(directory) / "installation.json",
                                confirmation="CREATE", verify_staged_media=PASS_ISO, seed_pin=bad,
                                verify_seed=lambda: verify_staged_seed(self.seed, bad))
        self.assertEqual(backend.devices, {})

    def test_persistent_missing_manifest_entry_refused_before_any_write(self):
        backend = PersistentFakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installation.json"
            with self.assertRaises(DeploymentRefused):
                rehearse_persistent(REQUEST, backend, manifest_path=path, confirmation="CREATE",
                                    verify_staged_media=PASS_ISO, seed_pin=None,
                                    verify_seed=lambda: verify_staged_seed(self.seed, self.pin))
            self.assertFalse(path.exists())
        self.assertEqual(backend.devices, {})
        self.assertEqual(backend.vms, {})


if __name__ == "__main__":
    unittest.main()
