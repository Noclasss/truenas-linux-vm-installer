"""Offline tests for the guarded Desktop adapter; no middleware host is contacted."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import stat
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from planning import desktop_live_writer as writer_module
from planning.desktop_live_writer import (
    DesktopArtifactInputs, DesktopCreateOnlyWriter, DesktopCreateRequest,
    DesktopWriterError, WRITE_METHODS,
)
from planning.desktop_write_contracts import load_reviewed_desktop_fixture

_GIB = 1024**3
_TEST_DISPLAY_PASSWORD = "SYNTHETIC_ONLY_SPICE_TEST_PASSWORD"


class FakeMiddleware:
    def __init__(self, fixture, *, failure=None, wrong_readback_at=None, display_count=0, healthy=True):
        self.fixture = fixture
        self.failure = failure  # (operation index, "before" | "after")
        self.wrong_readback_at = wrong_readback_at
        self.display_count = display_count
        self.healthy = healthy
        self.datasets = {}
        self.vms = {}
        self.devices = {}
        self.calls = []
        self.writes = []
        self._next_vm = 41
        self._next_device = 91

    def call(self, method, params=None):
        params = [] if params is None else params
        self.calls.append((method, params))
        if method == "system.version":
            return self.fixture["truenas_version"]
        if method == "core.get_methods":
            return self.fixture["methods"]
        if method == "pool.query":
            return [{"name": "tanktest", "healthy": self.healthy,
                     "warning": False, "status": "ONLINE",
                     "free": 100 * _GIB}]
        if method == "pool.dataset.query":
            filters = params[0] if params else []
            rows = [dict(row) for row in self.datasets.values()]
            if filters:
                target = filters[0][2]
                rows = [row for row in rows if row.get("id") == target]
            return rows
        if method == "vm.query":
            filters = params[0] if params else []
            rows = [dict(row) for row in self.vms.values()]
            if filters:
                target = filters[0][2]
                rows = [row for row in rows if row.get("id") == target]
            return rows
        if method == "vm.device.query":
            filters = params[0] if params else []
            rows = [dict(row) for row in self.devices.values()]
            if filters:
                field, _, target = filters[0]
                rows = [row for row in rows if row.get(field) == target]
            return rows
        if method == "vm.supports_virtualization":
            return True
        if method == "vm.maximum_supported_vcpus":
            return 8
        if method == "vm.get_available_memory":
            return 32 * 1024 * 1024**2

        if method not in WRITE_METHODS:
            raise AssertionError(f"unexpected middleware call: {method}")
        index = len(self.writes)
        payload = params[0]
        self.writes.append((method, params))
        if self.failure == (index, "before"):
            raise RuntimeError("synthetic pre-apply failure")
        result = self._apply(method, params)
        if index == self.wrong_readback_at:
            if method == "pool.dataset.create":
                row = self.datasets[payload["name"]]
                if row["type"] == "FILESYSTEM":
                    row["type"] = "VOLUME"
                else:
                    row["volsize"]["parsed"] += 1
            elif method == "vm.create":
                self.vms[result["id"]]["memory"] += 1
            else:
                self.devices[result["id"]]["attributes"]["dtype"] = "BROKEN"
        if self.failure == (index, "after"):
            raise TimeoutError("synthetic applied write with lost response")
        return result

    def _apply(self, method, params):
        payload = params[0]
        if method == "pool.dataset.create":
            identifier = payload["name"]
            kind = payload["type"]
            row = {"id": identifier, "type": kind,
                   "mountpoint": f"/mnt/tanktest/linux-vms/{identifier.rsplit('/', 1)[-1]}" if kind == "FILESYSTEM" else None,
                   "volsize": {"parsed": payload.get("volsize")}}
            self.datasets[identifier] = row
            return dict(row)
        if method == "vm.create":
            vm_id = self._next_vm
            self._next_vm += 1
            display_rows = []
            for _ in range(self.display_count):
                device_id = self._next_device
                self._next_device += 1
                display = {"id": device_id, "order": 0, "vm": vm_id,
                           "attributes": {"dtype": "DISPLAY"}}
                self.devices[device_id] = display
                display_rows.append(display)
            row = {
                "id": vm_id, "name": payload["name"], "memory": payload["memory"],
                "cores": payload["cores"], "vcpus": payload["vcpus"],
                "threads": payload["threads"], "bootloader": payload["bootloader"],
                "enable_secure_boot": payload["enable_secure_boot"],
                "autostart": payload["autostart"],
                "ensure_display_device": payload["ensure_display_device"],
                "devices": [dict(item) for item in display_rows],
                "display_available": bool(display_rows),
                "status": {"domain_state": "STOPPED", "pid": None},
            }
            self.vms[vm_id] = row
            return dict(row)
        if method == "vm.device.create":
            device_id = self._next_device
            self._next_device += 1
            attributes = dict(payload["attributes"])
            if attributes.get("dtype") == "DISPLAY":
                attributes.update({"port": 5900, "web_port": 5901})
            row = {"id": device_id, "order": len(self.devices), "vm": payload["vm"],
                   "attributes": attributes}
            self.devices[device_id] = row
            return dict(row)
        raise AssertionError(method)


def _fixture_with_test_pins(testcase, directory):
    iso = Path(directory) / "verified-source.iso"
    seed = Path(directory) / "seed.img"
    iso_bytes = b"synthetic signed desktop image"
    seed_bytes = b"synthetic private seed"
    iso.write_bytes(iso_bytes)
    seed.write_bytes(seed_bytes)
    os.chmod(seed, 0o600)
    digest = hashlib.sha256(iso_bytes).hexdigest()
    testcase.enterContext(patch.object(writer_module.desktop, "ARTIFACT_SIZE_BYTES", len(iso_bytes)))
    testcase.enterContext(patch.object(writer_module.desktop, "ARTIFACT_SHA256", digest))
    testcase.enterContext(patch.object(writer_module.desktop, "SEED_MEDIA_SIZE_BYTES", 1024))
    artifacts = DesktopArtifactInputs(
        iso=iso, checksum_manifest=Path(directory) / "sum.txt",
        signature=Path(directory) / "sum.sig", keyring=Path(directory) / "keyring.gpg",
        seed=seed,
    )
    request = DesktopCreateRequest(
        vm_name="DesktopTest", pool_name="tanktest", vcpus=2,
        memory_mib=6144, disk_gib=32, host_reserve_mib=1024,
        pool_reserve_mib=1024, source_id="ubuntu-desktop-minimal",
        cache_on_selected_pool=False, bridge_name=None,
    )
    return request, artifacts


class DesktopLiveWriterTests(unittest.TestCase):
    def setUp(self):
        self.display_password_prompt = patch.object(
            writer_module.getpass, "getpass", return_value=_TEST_DISPLAY_PASSWORD
        )
        self.display_password_prompt.start()
        self.addCleanup(self.display_password_prompt.stop)

    def writer(self, directory, *, failure=None, wrong_readback_at=None, display_count=0, healthy=True, access_probe=None):
        fixture = load_reviewed_desktop_fixture()
        request, artifacts = _fixture_with_test_pins(self, directory)
        client = FakeMiddleware(fixture, failure=failure, wrong_readback_at=wrong_readback_at,
                                display_count=display_count, healthy=healthy)
        if access_probe is None:
            access_probe = lambda path, user: True
        account = pwd.getpwuid(os.geteuid()).pw_name
        mountpoint = Path(directory) / "per-vm-dataset"
        mountpoint.mkdir(mode=0o700, exist_ok=True)
        writer = DesktopCreateOnlyWriter(
            client, fixture, request, artifacts,
            manifest_path=Path(directory) / "operation.json",
            service_account=account,
            access_probe=access_probe,
            artifact_verifier=lambda *args: {
                "signature_verified": True, "iso_bytes_verified": True,
                "live_install_allowed": False,
            },
        )
        return writer, client, mountpoint

    def confirmation(self, writer):
        with patch.object(writer, "_require_root", return_value=None):
            return writer.confirmation_phrase()

    def run_writer(self, directory, *, failure=None, display_count=0, healthy=True):
        writer, client, mountpoint = self.writer(
            directory, failure=failure, display_count=display_count, healthy=healthy
        )
        with patch.object(writer, "_require_root", return_value=None), \
             patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
            result = writer.create(confirmation=self.confirmation(writer))
        return result, client, writer

    def test_success_exact_payloads_readbacks_and_no_display_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            result, client, writer = self.run_writer(directory)
            self.assertEqual(result["status"], "created_not_started_live_unverified")
            self.assertFalse(result["vm_started"])
            self.assertEqual(result["resource_count"], 8)
            writes = client.writes
            self.assertEqual([method for method, _ in writes], [
                "pool.dataset.create", "pool.dataset.create", "pool.dataset.create",
                "vm.create", "vm.device.create", "vm.device.create", "vm.device.create", "vm.device.create",
            ])
            vm_payload = next(params[0] for method, params in writes if method == "vm.create")
            self.assertIs(vm_payload["ensure_display_device"], True)
            self.assertIs(vm_payload["autostart"], False)
            self.assertNotIn("vm.start", [method for method, _ in writes])
            self.assertNotIn("vm.device.delete", [method for method, _ in writes])
            display_creates = [
                params[0] for method, params in writes if method == "vm.device.create"
                and params[0].get("attributes", {}).get("dtype") == "DISPLAY"
            ]
            self.assertEqual(len(display_creates), 1)
            self.assertEqual(sum(
                _TEST_DISPLAY_PASSWORD in json.dumps(params)
                for _, params in writes
            ), 1)
            self.assertEqual(display_creates[0]["attributes"], {
                "dtype": "DISPLAY", "type": "SPICE", "bind": "127.0.0.1",
                "web": True, "wait": False, "resolution": "1024x768",
                "port": None, "web_port": None, "password": _TEST_DISPLAY_PASSWORD,
            })
            display_readback = client.devices[result["display_device_id"]]["attributes"]
            self.assertEqual(display_readback["port"], 5900)
            self.assertEqual(display_readback["web_port"], 5901)
            self.assertNotIn(_TEST_DISPLAY_PASSWORD, json.dumps(result))
            self.assertEqual(sum(
                1 for device in client.devices.values()
                if device["attributes"].get("dtype") == "DISPLAY"
            ), 1)
            manifest_path = Path(directory) / "operation.json"
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
            manifest = json.loads(manifest_path.read_text())
            display_resource = next(
                item for item in manifest["resources"]
                if item["kind"] == "device" and item["attributes"].get("dtype") == "DISPLAY"
            )
            self.assertEqual(display_resource["api_id"], result["display_device_id"])
            self.assertEqual(display_resource["vm_id"], result["vm_id"])
            self.assertEqual(display_resource["creation_method"], "vm.device.create")
            self.assertEqual(manifest["status"], "created_not_started_live_unverified")
            self.assertEqual(manifest["pending"], None)
            self.assertEqual(len(manifest["resources"]), 8)
            self.assertEqual(manifest["inventory_before"]["shared_parent_exists"], False)
            self.assertEqual(len(manifest["files"]), 2)
            self.assertTrue(all(row["state"] == "confirmed" for row in manifest["files"]))
            self.assertTrue(all(row["access_probe"] == "injected_test_probe" for row in manifest["files"]))
            self.assertTrue(all(not row["selected_service_account_open_verified"] for row in manifest["files"]))
            self.assertTrue(all(not row["live_vm_process_readable_verified"] for row in manifest["files"]))
            text = manifest_path.read_text()
            self.assertNotIn("synthetic private seed", text)
            self.assertNotIn(_TEST_DISPLAY_PASSWORD, text)
            self.assertNotIn("password", manifest["resources"][4]["attributes"])
            self.assertTrue(all(
                item["attributes"].get("dtype") != "DISPLAY"
                or item.get("creation_method") == "vm.device.create"
                for item in manifest["resources"] if item["kind"] == "device"
            ))

    def test_insufficient_privilege_refuses_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            with self.assertRaisesRegex(DesktopWriterError, "requires an authenticated root"):
                writer.create(confirmation="not relevant")
            self.assertEqual(client.writes, [])
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_display_password_refusal_precedes_any_resource_creation(self):
        for password in ("", None, "contains\ncontrol"):
            with self.subTest(password_type=type(password).__name__), tempfile.TemporaryDirectory() as directory:
                writer, client, _ = self.writer(directory)
                with patch.object(writer, "_require_root", return_value=None), \
                     patch.object(writer_module.getpass, "getpass", return_value=password), \
                     self.assertRaises(DesktopWriterError):
                    writer.create(confirmation=self.confirmation(writer))
                self.assertEqual(client.writes, [])
                self.assertFalse((Path(directory) / "operation.json").exists())

    def test_display_password_never_enters_result_manifest_or_failure_text(self):
        for mode in ("before", "after"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                writer, client, mountpoint = self.writer(directory, failure=(4, mode))
                with patch.object(writer, "_require_root", return_value=None), \
                     patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                     self.assertRaises(DesktopWriterError) as caught:
                    writer.create(confirmation=self.confirmation(writer))
                self.assertEqual(len(client.writes), 5)
                self.assertEqual(client.writes[4][0], "vm.device.create")
                self.assertEqual(client.writes[4][1][0]["attributes"]["password"], _TEST_DISPLAY_PASSWORD)
                manifest_path = Path(directory) / "operation.json"
                text = manifest_path.read_text()
                self.assertNotIn(_TEST_DISPLAY_PASSWORD, text)
                self.assertNotIn(_TEST_DISPLAY_PASSWORD, str(caught.exception))
                self.assertNotIn(_TEST_DISPLAY_PASSWORD, "".join(traceback.format_exception(caught.exception)))
                self.assertEqual(json.loads(text)["pending"]["state"], "ambiguous")
                self.assertEqual(len(client.devices), int(mode == "after"))

    def test_preflight_refusal_precedes_any_write_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory, healthy=False)
            with patch.object(writer, "_require_root", return_value=None), self.assertRaises(DesktopWriterError):
                writer.create(confirmation=self.confirmation(writer))
            self.assertFalse(client.writes)
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_unexpected_displays_from_vm_create_stop_and_record_reconciliation_state(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory, display_count=2)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                 self.assertRaises(DesktopWriterError):
                writer.create(confirmation=self.confirmation(writer))
            self.assertEqual([method for method, _ in client.writes], [
                "pool.dataset.create", "pool.dataset.create", "pool.dataset.create", "vm.create",
            ])
            manifest = json.loads((Path(directory) / "operation.json").read_text())
            self.assertEqual(manifest["status"], "reconcile_required")
            self.assertEqual(manifest["resources"][-1]["kind"], "vm")
            pending = manifest["pending"]
            self.assertEqual(pending["method"], "vm.create")
            self.assertEqual(pending["state"], "ambiguous")
            self.assertEqual(pending["observed_state"]["readback_device_count"], 2)
            self.assertEqual(len(pending["observed_resources"]), 4)
            self.assertEqual(len(client.writes), 4)

    def test_failure_before_and_lost_response_after_every_api_write(self):
        for index in range(8):
            for mode in ("before", "after"):
                with self.subTest(index=index, mode=mode), tempfile.TemporaryDirectory() as directory:
                    writer, client, mountpoint = self.writer(directory, failure=(index, mode))
                    with patch.object(writer, "_require_root", return_value=None), \
                         patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                         self.assertRaises(DesktopWriterError):
                        writer.create(confirmation=self.confirmation(writer))
                    self.assertEqual(len(client.writes), index + 1)
                    self.assertFalse(any(method in {"vm.start", "vm.stop", "vm.device.delete", "vm.device.convert"}
                                         for method, _ in client.writes))
                    manifest = json.loads((Path(directory) / "operation.json").read_text())
                    self.assertEqual(manifest["status"], "reconcile_required")
                    self.assertIsNotNone(manifest["pending"])
                    self.assertEqual(manifest["pending"]["state"], "ambiguous")
                    self.assertTrue(all(row["state"] == "confirmed" for row in manifest["resources"]))
                    self.assertTrue(any(
                        row["identifier"] == manifest["pending"]["identifier"]
                        for row in manifest["intended_resources"]
                    ))
                    self.assertIs(manifest["policy"]["automatic_retry"], False)
                    self.assertIs(manifest["policy"]["rollback"], False)
                    self.assertIs(manifest["policy"]["deletion"], False)

    def test_incorrect_readback_stops_before_every_later_mutation(self):
        for index in range(8):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                writer, client, mountpoint = self.writer(directory, wrong_readback_at=index)
                with patch.object(writer, "_require_root", return_value=None):
                    with patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
                        with self.assertRaises(DesktopWriterError):
                            writer.create(confirmation=self.confirmation(writer))
                self.assertEqual(len(client.writes), index + 1)
                manifest = json.loads((Path(directory) / "operation.json").read_text())
                self.assertEqual(manifest["status"], "reconcile_required")
                self.assertEqual(manifest["pending"]["state"], "ambiguous")
                self.assertTrue(all(row["state"] == "confirmed" for row in manifest["resources"]))

    def test_staging_failure_before_and_after_file_creation_stops_api_mutations(self):
        for label, filename in (("ISO", "ubuntu-installer.iso"), ("seed", "cidata.img")):
            for mode in ("before", "after"):
                with self.subTest(label=label, mode=mode), tempfile.TemporaryDirectory() as directory:
                    mountpoint = Path(directory) / "per-vm-dataset"
                    mountpoint.mkdir(mode=0o700)
                    target = mountpoint / filename
                    if mode == "before":
                        target.write_bytes(b"pre-existing protected file")
                    probe = lambda path, user, target=target, mode=mode: not (
                        mode == "after" and path == target
                    )
                    writer, client, mountpoint = self.writer(directory, access_probe=probe)
                    with patch.object(writer, "_require_root", return_value=None):
                        with patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
                            with self.assertRaises(DesktopWriterError):
                                writer.create(confirmation=self.confirmation(writer))
                    self.assertEqual([method for method, _ in client.writes], [
                        "pool.dataset.create", "pool.dataset.create", "pool.dataset.create",
                    ])
                    manifest = json.loads((Path(directory) / "operation.json").read_text())
                    self.assertEqual(manifest["status"], "reconcile_required")
                    self.assertEqual(manifest["pending"]["method"], "filesystem.stage")
                    self.assertEqual(manifest["pending"]["state"], "ambiguous")
                    self.assertIs(manifest["policy"]["automatic_retry"], False)
                    self.assertIs(manifest["policy"]["rollback"], False)
                    self.assertIs(manifest["policy"]["deletion"], False)
                    if mode == "before":
                        self.assertEqual(target.read_bytes(), b"pre-existing protected file")

    def test_vm_create_ambiguous_outcomes_reconcile_vm_and_implicit_display_read_only(self):
        for mode in ("before", "after"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                writer, client, mountpoint = self.writer(directory, failure=(3, mode))
                with patch.object(writer, "_require_root", return_value=None):
                    with patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
                        with self.assertRaises(DesktopWriterError):
                            writer.create(confirmation=self.confirmation(writer))
                writes_before_reconcile = len(client.writes)
                vms = client.call("vm.query", [[], {"select": ["id", "name"]}])
                display_devices = [
                    device for device in client.call("vm.device.query", [[], {"select": ["id", "vm", "attributes"]}])
                    if device.get("attributes", {}).get("dtype") == "DISPLAY"
                ]
                self.assertEqual(len(client.writes), writes_before_reconcile)
                manifest = json.loads((Path(directory) / "operation.json").read_text())
                self.assertEqual(manifest["pending"]["identifier"], "DesktopTest")
                self.assertTrue(any(
                    row["identifier"] == "DesktopTest:display"
                    and row["creation_method"] == "vm.device.create"
                    for row in manifest["intended_resources"]
                ))
                self.assertEqual(bool(vms), mode == "after")
                self.assertEqual(len(display_devices), 0)
                self.assertEqual(len(client.writes), writes_before_reconcile)

    def test_preflight_request_refuses_unselected_network_and_settings_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            self.assertIsNone(writer.request.bridge_name)
            self.assertIs(writer.request.cache_on_selected_pool, False)
            self.assertFalse(any(method == "vm.device.nic_attach_choices" for method, _ in client.calls))

    def test_default_process_read_probe_opens_only_as_selected_account(self):
        from planning.desktop_live_writer import _safe_read_probe
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readable"
            path.write_bytes(b"x")
            account = pwd.getpwuid(os.geteuid()).pw_name
            if not shutil_which("runuser"):
                self.skipTest("runuser is unavailable in this test environment")
            if os.geteuid() != 0:
                self.assertFalse(_safe_read_probe(path, account))
            else:
                self.assertTrue(_safe_read_probe(path, account))


def shutil_which(command):
    import shutil
    return shutil.which(command)


if __name__ == "__main__":
    unittest.main()
