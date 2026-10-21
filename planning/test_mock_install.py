import json
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from planning.mock_install import (
    LIVE_PROFILE_ALLOWLIST,
    MockAmbiguousWrite,
    MockInstallError,
    MockInstallRequest,
    MockMiddleware,
    live_profile_enabled,
    run_mock_install,
)


FIXTURE = Path(__file__).parent / "fixtures" / "truenas-26.0.0-beta.2-method-contracts.json"


def make_backend(**kwargs):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return MockMiddleware(
        methods=fixture["methods"],
        pools={"mock-pool": {"healthy": True, "available_bytes": 100 * 1024**3}},
        bridges={"mock-bridge"},
        **kwargs,
    )


def request(**kwargs):
    values = {
        "profile": "ubuntu-server",
        "vm_name": "MockUbuntu",
        "pool": "mock-pool",
        "bridge": "mock-bridge",
        "vcpus": 2,
        "memory_mib": 4096,
        "disk_gib": 32,
    }
    values.update(kwargs)
    return MockInstallRequest(**values)


class MockInstallOrchestrationTests(unittest.TestCase):
    def test_live_profile_allowlist_is_empty_for_every_profile(self):
        self.assertEqual(LIVE_PROFILE_ALLOWLIST, frozenset())
        for profile in ("ubuntu-server", "ubuntu-desktop", "debian-server", "debian-desktop"):
            self.assertFalse(live_profile_enabled(profile))

    def test_success_rehearses_writes_readback_and_keeps_vm_stopped(self):
        backend = make_backend()
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "mock-manifest.json"
            result = run_mock_install(
                request(), backend, manifest_path=manifest_path,
                confirmation="CREATE MOCK MockUbuntu",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)

        self.assertEqual(manifest["mode"], "offline-mock-only")
        self.assertEqual(manifest["status"], "mock_verified_not_installed")
        self.assertEqual(manifest["created"]["vm"]["id"], result.vm_id)
        self.assertEqual(set(result.dataset_ids), {
            "mock-pool/linux-vms",
            "mock-pool/linux-vms/MockUbuntu",
            "mock-pool/linux-vms/MockUbuntu/disk0",
        })
        self.assertEqual(manifest["planned"]["shared_parent_dataset"], "mock-pool/linux-vms")
        self.assertEqual(
            {item["id"] for item in manifest["created"]["datasets"]},
            set(result.dataset_ids),
        )
        self.assertEqual(result.final_power_state, "STOPPED")
        self.assertEqual(result.device_ids, tuple(sorted(result.device_ids)))
        self.assertEqual(len(result.device_ids), 4)  # disk, CDROM, NIC, auto DISPLAY
        self.assertEqual(backend.writes.count("vm.create"), 1)
        self.assertEqual(backend.writes.count("pool.dataset.create"), 3)
        dataset_creates = [params[0] for method, params in backend.calls if method == "pool.dataset.create"]
        self.assertEqual(
            [item["name"] for item in dataset_creates],
            [
                "mock-pool/linux-vms",
                "mock-pool/linux-vms/MockUbuntu",
                "mock-pool/linux-vms/MockUbuntu/disk0",
            ],
        )
        self.assertTrue(all("create_ancestors" not in item for item in dataset_creates))
        self.assertEqual(backend.writes.count("vm.device.create"), 3)
        vm_create = next(params[0] for method, params in backend.calls if method == "vm.create")
        self.assertIs(vm_create["autostart"], False)
        self.assertIs(vm_create["enable_secure_boot"], False)
        self.assertEqual(vm_create["name"], "MockUbuntu")
        created_device_types = {
            device["dtype"] for device in manifest["created"]["devices"]
        }
        self.assertEqual(created_device_types, {"DISK", "CDROM", "NIC", "DISPLAY"})
        cdrom = next(device for device in manifest["created"]["devices"] if device["dtype"] == "CDROM")
        self.assertEqual(cdrom["attributes"]["path"], "mock://seed/cidata.iso")
        self.assertNotIn("vm.start", result.middleware_calls)
        self.assertFalse(any(method.endswith(".delete") for method in result.middleware_calls))
        self.assertTrue(any(
            device["dtype"] == "DISPLAY"
            for device in manifest["created"]["devices"]
        ))
        self.assertTrue(all(
            method in {"pool.dataset.query", "vm.query", "vm.device.query", "pool.dataset.create", "vm.create", "vm.device.create"}
            for method in result.middleware_calls
        ))

    def test_mock_backend_rejects_implicit_or_missing_dataset_ancestors(self):
        cases = (
            {
                "name": "mock-pool/linux-vms/MockUbuntu",
                "type": "FILESYSTEM",
                "create_ancestors": True,
            },
            {"name": "mock-pool/linux-vms/MockUbuntu", "type": "FILESYSTEM"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                backend = make_backend()
                with self.assertRaisesRegex(MockInstallError, "explicitly"):
                    backend.call("pool.dataset.create", [payload])
                self.assertEqual(backend.datasets, {})

    def test_existing_shared_parent_is_reused_without_claiming_ownership(self):
        backend = make_backend()
        shared_parent = "mock-pool/linux-vms"
        backend.datasets[shared_parent] = {"id": shared_parent, "type": "FILESYSTEM"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            result = run_mock_install(
                request(), backend, manifest_path=path,
                confirmation="CREATE MOCK MockUbuntu",
            )
            manifest = json.loads(path.read_text(encoding="utf-8"))

        self.assertNotIn(shared_parent, {item["id"] for item in manifest["created"]["datasets"]})
        self.assertNotIn(shared_parent, result.dataset_ids)
        self.assertEqual(backend.writes.count("pool.dataset.create"), 2)
        self.assertEqual(backend.datasets[shared_parent]["type"], "FILESYSTEM")
        self.assertEqual(manifest["planned"]["shared_parent_dataset"], shared_parent)

    def test_confirmation_and_mock_backend_are_mandatory_before_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            backend = make_backend()
            with self.assertRaisesRegex(MockInstallError, "exact per-VM confirmation"):
                run_mock_install(request(), backend, manifest_path=path, confirmation="yes")
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(MockInstallError, "mock-only backend"):
                run_mock_install(request(), cast(Any, object()), manifest_path=path, confirmation="CREATE MOCK MockUbuntu")
            self.assertFalse(path.exists())

    def test_invalid_profile_pool_bridge_and_resource_bounds_fail_before_writes(self):
        cases = [
            ({"profile": "debian-server"}, "experimental Ubuntu Server"),
            ({"pool": "missing"}, "not present"),
            ({"bridge": "missing"}, "not an existing mock bridge"),
            ({"vm_name": "../unsafe"}, "VM name is invalid"),
            ({"vcpus": True}, "vCPU count"),
            ({"memory_mib": 0}, "RAM is outside"),
            ({"disk_gib": 8}, "virtual disk size"),
            ({"seed_identifier": "/tmp/real.iso"}, "fixed mock-only URI"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                backend = make_backend()
                path = Path(directory) / "manifest.json"
                with self.assertRaisesRegex(MockInstallError, message):
                    run_mock_install(request(**overrides), backend, manifest_path=path,
                                     confirmation="CREATE MOCK MockUbuntu")
                self.assertEqual(backend.writes, [])
                self.assertFalse(path.exists())

    def test_unhealthy_or_capacity_limited_pool_fails_without_mutation(self):
        for pool in (
            {"healthy": False, "available_bytes": 100 * 1024**3},
            {"healthy": True, "available_bytes": 1},
            {"healthy": True},
        ):
            with self.subTest(pool=pool), tempfile.TemporaryDirectory() as directory:
                backend = make_backend()
                backend.pools["mock-pool"] = pool
                path = Path(directory) / "manifest.json"
                with self.assertRaises(MockInstallError):
                    run_mock_install(request(), backend, manifest_path=path,
                                     confirmation="CREATE MOCK MockUbuntu")
                self.assertEqual(backend.writes, [])
                self.assertFalse(path.exists())

    def test_exact_existing_dataset_or_vm_is_never_overwritten(self):
        for kind in ("parent", "zvol", "descendant", "shared_wrong", "vm"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                backend = make_backend()
                if kind == "parent":
                    key = "mock-pool/linux-vms/MockUbuntu"
                    backend.datasets[key] = {"id": key, "type": "FILESYSTEM"}
                elif kind == "zvol":
                    key = "mock-pool/linux-vms/MockUbuntu/disk0"
                    backend.datasets[key] = {"id": key, "type": "VOLUME"}
                elif kind == "descendant":
                    key = "mock-pool/linux-vms/MockUbuntu/unknown-child"
                    backend.datasets[key] = {"id": key, "type": "FILESYSTEM"}
                elif kind == "shared_wrong":
                    key = "mock-pool/linux-vms"
                    backend.datasets[key] = {"id": key, "type": "VOLUME"}
                else:
                    backend.vms[99] = {"id": 99, "name": "mockubuntu", "status": {"state": "STOPPED"}}
                path = Path(directory) / "manifest.json"
                with self.assertRaises(MockInstallError):
                    run_mock_install(request(), backend, manifest_path=path,
                                     confirmation="CREATE MOCK MockUbuntu")
                self.assertEqual(backend.writes, [])
                self.assertFalse(path.exists())

    def test_manifest_is_exclusive_and_existing_contents_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("keep", encoding="utf-8")
            backend = make_backend()
            with self.assertRaises(MockInstallError):
                run_mock_install(request(), backend, manifest_path=path,
                                 confirmation="CREATE MOCK MockUbuntu")
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")
            self.assertEqual(backend.writes, [])

    def test_ambiguous_shared_parent_creation_is_not_retried(self):
        backend = make_backend(fail_after_apply_method="pool.dataset.create")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaisesRegex(MockInstallError, "lost response"):
                run_mock_install(
                    request(), backend, manifest_path=path,
                    confirmation="CREATE MOCK MockUbuntu",
                )
            manifest = json.loads(path.read_text(encoding="utf-8"))

        shared_parent = "mock-pool/linux-vms"
        self.assertEqual(manifest["status"], "reconcile_required")
        self.assertEqual(backend.writes.count("pool.dataset.create"), 1)
        self.assertEqual(set(backend.datasets), {shared_parent})
        self.assertEqual(manifest["created"]["datasets"], [])
        self.assertFalse(any(method.endswith(".delete") for method, _ in backend.calls))

    def test_ambiguous_write_is_not_retried_and_manifest_requires_reconciliation(self):
        backend = make_backend(fail_after_apply_method="vm.create")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaisesRegex(MockInstallError, "lost response") as error:
                run_mock_install(request(), backend, manifest_path=path,
                                 confirmation="CREATE MOCK MockUbuntu")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(error.exception, MockAmbiguousWrite)
        self.assertEqual(manifest["status"], "reconcile_required")
        self.assertEqual(backend.writes.count("vm.create"), 1)
        self.assertEqual(len(backend.vms), 1)  # applied despite lost response
        self.assertEqual(backend.writes.count("vm.device.create"), 0)
        self.assertFalse(any(method.endswith(".delete") for method, _ in backend.calls))

    def test_midflow_failure_retains_manifest_and_does_not_rollback_or_retry(self):
        backend = make_backend(fail_after_apply_method="vm.device.create")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaises(MockInstallError):
                run_mock_install(request(), backend, manifest_path=path,
                                 confirmation="CREATE MOCK MockUbuntu")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "reconcile_required")
        self.assertEqual(backend.writes.count("vm.device.create"), 1)
        self.assertEqual(len(backend.vms), 1)
        self.assertGreaterEqual(len(backend.devices), 2)  # auto display + applied device
        self.assertFalse(any(method.endswith(".delete") for method, _ in backend.calls))

    def test_failure_before_vm_create_keeps_manifested_storage_without_cleanup(self):
        backend = make_backend(fail_before_method="vm.create")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaisesRegex(MockInstallError, "before applying vm.create"):
                run_mock_install(request(), backend, manifest_path=path,
                                 confirmation="CREATE MOCK MockUbuntu")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "reconcile_required")
        self.assertEqual(len(manifest["created"]["datasets"]), 3)
        self.assertEqual(backend.writes.count("vm.create"), 0)
        self.assertEqual(len(backend.vms), 0)
        self.assertEqual(len(backend.datasets), 3)
        self.assertFalse(any(method.endswith(".delete") for method, _ in backend.calls))

    def test_schema_mismatch_blocks_vm_write_after_manifesting_prior_resources(self):
        backend = make_backend()
        vm_create_schema = backend.methods["vm.create"]["accepts"][0]
        vm_create_schema["properties"].pop("autostart")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with self.assertRaisesRegex(MockInstallError, "captured schema validation"):
                run_mock_install(request(), backend, manifest_path=path,
                                 confirmation="CREATE MOCK MockUbuntu")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "reconcile_required")
        self.assertEqual(len(manifest["created"]["datasets"]), 3)
        self.assertEqual(backend.writes.count("vm.create"), 0)
        self.assertEqual(len(backend.vms), 0)


if __name__ == "__main__":
    unittest.main()
