"""Offline-only Desktop write-contract checks; no TrueNAS calls or host writes."""
import copy
import unittest

from planning.desktop_deploy_mock import (
    DeploymentRefused, DesktopRequest, FakeBackend, rehearse,
)
from planning.desktop_write_contracts import (
    DesktopWriteContractError, REVIEWED_BUILD, build_desktop_write_intents,
    load_reviewed_desktop_fixture,
)
from planning.installer_core.schema import validate_method_request

PASS_VERIFY = lambda: None  # test-only fake verifier; production code has no default

REQUEST = DesktopRequest("desktop-test", "testpool", "br0", 2, 6144, 32)
_GIB = 1024**3


class DesktopWriteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_reviewed_desktop_fixture()

    def build(self, **kwargs):
        return build_desktop_write_intents(
            REQUEST, self.fixture, vm_id=17, **kwargs
        )

    def test_reviewed_fixture_is_pinned_to_exact_build(self):
        self.assertEqual(self.fixture["truenas_version"], REVIEWED_BUILD)

    def test_all_proposed_writes_match_exact_build_method_schemas(self):
        intents = self.build()
        self.assertEqual([method for method, _ in intents], [
            "pool.dataset.create", "pool.dataset.create", "pool.dataset.create",
            "vm.create", "vm.device.create", "vm.device.create",
            "vm.device.create", "vm.device.create", "vm.start",
        ])
        for method, params in intents:
            with self.subTest(method=method, params=params):
                validate_method_request(self.fixture["methods"], method, params)
        self.assertEqual(
            [args[0]["type"] for _, args in intents[:3]],
            ["FILESYSTEM", "FILESYSTEM", "VOLUME"],
        )
        self.assertTrue(all(
            args[0]["create_ancestors"] is False for _, args in intents[:3]
        ))
        self.assertEqual(intents[2][1][0]["volsize"], 32 * _GIB)
        self.assertEqual(intents[2][1][0]["name"], "testpool/linux-vms/desktop-test/disk0")
        vm = intents[3][1][0]
        self.assertEqual(vm["memory"], 6144)
        # One socket, two cores, one thread: exactly the requested two vCPUs.
        self.assertEqual((vm["vcpus"], vm["cores"], vm["threads"]), (1, 2, 1))
        self.assertIs(vm["autostart"], False)
        self.assertIs(vm["ensure_display_device"], True)
        self.assertEqual(
            [args[0]["attributes"]["dtype"] for _, args in intents[4:8]],
            ["DISK", "CDROM", "RAW", "NIC"],
        )
        self.assertEqual(intents[5][1][0]["attributes"]["path"],
                         "/mnt/testpool/linux-vms/desktop-test/ubuntu-installer.iso")
        self.assertEqual(intents[6][1][0]["attributes"]["path"],
                         "/mnt/testpool/linux-vms/desktop-test/cidata.img")
        self.assertEqual(intents[-1], ("vm.start", [17, {"overcommit": False}]))

    def test_existing_shared_parent_skips_creation(self):
        intents = self.build(shared_parent_exists=True)
        self.assertEqual(intents[0][1][0]["name"],
                         "testpool/linux-vms/desktop-test")
        self.assertFalse(any(
            method == "pool.dataset.create" and args[0]["name"] == "testpool/linux-vms"
            for method, args in intents
        ))

    def test_rejects_other_build_or_missing_unit_semantics(self):
        other = copy.deepcopy(self.fixture)
        other["truenas_version"] = "TrueNAS-unknown"
        with self.assertRaisesRegex(DesktopWriteContractError, "exact reviewed"):
            build_desktop_write_intents(REQUEST, other, vm_id=17)
        other = copy.deepcopy(self.fixture)
        other["method_semantics"]["vm.create.memory"]["unit"] = "bytes"
        with self.assertRaisesRegex(DesktopWriteContractError, "unit semantics"):
            build_desktop_write_intents(REQUEST, other, vm_id=17)

    def test_missing_or_mutated_write_method_is_refused(self):
        for method in ("pool.dataset.create", "vm.create", "vm.device.create", "vm.start"):
            with self.subTest(method=method):
                broken = dict(self.fixture)
                broken["methods"] = dict(self.fixture["methods"])
                broken["methods"].pop(method)
                with self.assertRaisesRegex(DesktopWriteContractError, method):
                    build_desktop_write_intents(REQUEST, broken, vm_id=17)

    def test_invalid_mock_id_and_request_are_refused(self):
        for vm_id in (True, 0, -1, "1"):
            with self.subTest(vm_id=vm_id), self.assertRaises(DesktopWriteContractError):
                build_desktop_write_intents(REQUEST, self.fixture, vm_id=vm_id)
        invalid = DesktopRequest("bad/name", "testpool", "br0", 2, 6144, 32)
        with self.assertRaisesRegex(DesktopWriteContractError, "planner rejected"):
            build_desktop_write_intents(invalid, self.fixture, vm_id=1)

    def test_fake_preflight_requires_valid_fixture_before_create(self):
        broken = copy.deepcopy(self.fixture)
        broken["methods"].pop("vm.device.create")
        backend = FakeBackend(contract_fixture=broken)
        with self.assertRaisesRegex(DeploymentRefused, "offline fixture rejected"):
            rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY)
        self.assertEqual(backend.events, ["preflight"])
        self.assertEqual(backend.datasets, {})
        self.assertEqual(backend.resources["vm"], set())
        self.assertEqual(backend.write_intents, ())

    def test_fake_rehearsal_retains_reviewed_write_intents(self):
        backend = FakeBackend()
        rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY)
        self.assertEqual(len(backend.write_intents), 9)
        self.assertEqual(backend.write_intents[-1][0], "vm.start")
        self.assertEqual(backend.write_intents[-1][1][1], {"overcommit": False})

    def test_fake_preexisting_parent_does_not_propose_recreation(self):
        backend = FakeBackend(datasets={"testpool/linux-vms": "FILESYSTEM"})
        rehearse(REQUEST, backend, confirmation="CREATE", verify_staged_media=PASS_VERIFY)
        self.assertFalse(any(
            method == "pool.dataset.create" and args[0]["name"] == "testpool/linux-vms"
            for method, args in backend.write_intents
        ))


if __name__ == "__main__":
    unittest.main()
