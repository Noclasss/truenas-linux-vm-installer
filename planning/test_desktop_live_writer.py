"""Offline tests for the guarded Desktop adapter; no middleware host is contacted."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import stat
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from planning import desktop_live_writer as writer_module
from planning.desktop_live_writer import (
    DesktopArtifactInputs, DesktopCreateOnlyWriter, DesktopCreateRequest,
    DesktopWriterError, WRITE_METHODS,
)
from planning.desktop_write_contracts import load_reviewed_desktop_fixture

_GIB = 1024**3


class FakeMiddleware:
    def __init__(self, fixture, *, failure=None, wrong_readback_at=None, display_count=0, healthy=True,
                 fail_vm_readback=False, device_readback_issue=None, hostname="fakehost"):
        self.fixture = fixture
        self.hostname = hostname
        self.failure = failure  # (operation index, "before" | "after")
        self.wrong_readback_at = wrong_readback_at
        self.display_count = display_count
        self.healthy = healthy
        self.fail_vm_readback = fail_vm_readback
        self.device_readback_issue = device_readback_issue
        self.device_create_count = 0
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
            if self.fail_vm_readback and self.vms:
                raise TimeoutError("synthetic VM read-back unavailable")
            filters = params[0] if params else []
            rows = [dict(row) for row in self.vms.values()]
            if filters:
                target = filters[0][2]
                rows = [row for row in rows if row.get("id") == target]
            return rows
        if method == "vm.device.query":
            if self.device_create_count and self.device_readback_issue == "error":
                raise TimeoutError("synthetic device read-back unavailable")
            if self.device_create_count and self.device_readback_issue == "empty":
                return []
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
        if method == "system.info":
            return {"hostname": self.hostname, "physmem": 64 * 1024**3}
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

    def close(self):
        return None

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
            self.device_create_count += 1
            return dict(row)
        raise AssertionError(method)


def _fixture_with_test_pins(testcase, directory):
    iso = Path(directory) / "verified-source.iso"
    seed = Path(directory) / "seed.img"
    iso_bytes = b"synthetic signed desktop image"
    seed_bytes = b"synthetic private seed"
    iso.write_bytes(iso_bytes)
    seed.write_bytes(seed_bytes + bytes(1024 - len(seed_bytes)))
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
        expected_hostname="fakehost",
    )
    return request, artifacts


class ActiveQemuProcessOwnerTests(unittest.TestCase):
    def process(self):
        process = Mock()
        process.stdout.fileno.return_value = 41
        process.poll.return_value = 0
        process.returncode = 0
        return process

    def test_active_qemu_owner_is_parsed_from_bounded_ps_output(self):
        process = self.process()
        account = pwd.struct_passwd(("libvirt-qemu", "x", 986, 986, "VM process", "/", "/usr/sbin/nologin"))
        with (patch.object(writer_module.shutil, "which", return_value="/usr/bin/ps"),
             patch.object(writer_module.subprocess, "Popen", return_value=process) as popen,
             patch.object(writer_module.select, "select", return_value=([process.stdout], [], [])),
             patch.object(writer_module.os, "read", side_effect=[b"0 qemu-ga\n986 qemu-system-x86\n", b""]),
             patch.object(writer_module.pwd, "getpwuid", return_value=account),
        ):
            self.assertEqual(writer_module._active_qemu_process_owner(), (986, "libvirt-qemu"))
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/ps", "-eo", "euid=,comm="])
        process.wait.assert_called_once()
        process.kill.assert_not_called()

    def test_qemu_inventory_must_have_one_mapped_owner(self):
        account = pwd.struct_passwd(("vm-runner", "x", 107, 107, "VM process", "/", "/usr/sbin/nologin"))
        root_account = pwd.struct_passwd(("root", "x", 100, 0, "root", "/root", "/bin/sh"))
        accounts = {100: root_account, 107: account}
        with patch.object(writer_module.pwd, "getpwuid", side_effect=accounts.get):
            self.assertEqual(
                writer_module._parse_qemu_process_owner(b"107 qemu-system-x86_64" + bytes([10])),
                (107, "vm-runner"),
            )
            with self.assertRaisesRegex(DesktopWriterError, "exactly one active QEMU"):
                writer_module._parse_qemu_process_owner(
                    b"100 qemu-system-x86_64" + bytes([10])
                    + b"107 qemu-system-x86_64" + bytes([10])
                )
            with self.assertRaisesRegex(DesktopWriterError, "exactly one active QEMU"):
                writer_module._parse_qemu_process_owner(b"1 systemd" + bytes([10]))

    def test_qemu_inventory_output_cap_kills_process(self):
        process = self.process()
        process.poll.return_value = None
        with (patch.object(writer_module.shutil, "which", return_value="/usr/bin/ps"),
             patch.object(writer_module.subprocess, "Popen", return_value=process),
             patch.object(writer_module.select, "select", return_value=([process.stdout], [], [])),
             patch.object(writer_module.os, "read", side_effect=[b"x" * 4096] * 16 + [b"x"]),
        ):
            with self.assertRaisesRegex(DesktopWriterError, "output bound"):
                writer_module._active_qemu_process_owner()
        process.kill.assert_called_once()


class ServiceAccountPreflightTests(unittest.TestCase):
    def account(self, *, uid=107, gid=107):
        return pwd.struct_passwd((
            "vm-runner", "x", uid, gid, "VM process", "/", "/usr/sbin/nologin",
        ))

    def test_missing_service_account_is_rejected_before_runuser(self):
        with patch.object(writer_module.pwd, "getpwnam", side_effect=KeyError("missing")), \
             patch.object(writer_module.shutil, "which") as which, \
             patch.object(writer_module.subprocess, "run") as run:
            with self.assertRaisesRegex(DesktopWriterError, "service account is unavailable"):
                writer_module._verify_vm_process_account("vm-runner")
        which.assert_not_called()
        run.assert_not_called()

    def test_root_cannot_be_selected_as_vm_process_account(self):
        with patch.object(writer_module.pwd, "getpwnam", return_value=self.account(uid=0, gid=0)), \
             patch.object(writer_module.shutil, "which") as which, \
             patch.object(writer_module.subprocess, "run") as run:
            with self.assertRaisesRegex(DesktopWriterError, "must be a non-root"):
                writer_module._verify_vm_process_account("vm-runner")
        which.assert_not_called()
        run.assert_not_called()

    def test_missing_runuser_is_rejected(self):
        with patch.object(writer_module.pwd, "getpwnam", return_value=self.account()), \
             patch.object(writer_module.shutil, "which", return_value=None), \
             patch.object(writer_module.subprocess, "run") as run:
            with self.assertRaisesRegex(DesktopWriterError, "runuser is required"):
                writer_module._verify_vm_process_account("vm-runner")
        run.assert_not_called()

    def test_runuser_must_execute_the_local_probe_successfully(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(writer_module.pwd, "getpwnam", return_value=self.account()), \
             patch.object(writer_module.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(writer_module.subprocess, "run", return_value=completed) as run:
            with patch.object(writer_module, "_active_qemu_process_owner", return_value=(107, "vm-runner")):
                account = writer_module._verify_vm_process_account("vm-runner")
        self.assertEqual(account.pw_uid, 107)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["/usr/sbin/runuser", "-u", "vm-runner", "--"])
        self.assertEqual(argv[4], "python3")
        self.assertIn("/dev/null", argv[-1])
        self.assertEqual(run.call_args.kwargs["env"]["PATH"], os.defpath)

    def test_active_qemu_owner_must_match_selected_service_account(self):
        with (
            patch.object(writer_module.pwd, "getpwnam", return_value=self.account()),
            patch.object(writer_module.shutil, "which", return_value="/usr/sbin/runuser"),
            patch.object(writer_module, "_active_qemu_process_owner", return_value=(108, "vm-runner")),
            patch.object(writer_module.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(DesktopWriterError, "does not match the active QEMU"):
                writer_module._verify_vm_process_account("vm-runner")
        run.assert_not_called()

    def test_runuser_failure_is_a_prerequisite_refusal(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1)
        with patch.object(writer_module.pwd, "getpwnam", return_value=self.account()), \
             patch.object(writer_module.shutil, "which", return_value="/usr/sbin/runuser"), \
             patch.object(writer_module.subprocess, "run", return_value=completed):
            with patch.object(writer_module, "_active_qemu_process_owner", return_value=(107, "vm-runner")):
                with self.assertRaisesRegex(DesktopWriterError, "cannot execute local checks"):
                    writer_module._verify_vm_process_account("vm-runner")


class DesktopLiveWriterTests(unittest.TestCase):
    def setUp(self):
        service_account = pwd.getpwuid(os.geteuid()).pw_name if os.geteuid() else "nobody"
        self._active_owner_patch = patch.object(
            writer_module, "_active_qemu_process_owner",
            return_value=(pwd.getpwnam(service_account).pw_uid, service_account),
        )
        self._active_owner_patch.start()
        self.addCleanup(self._active_owner_patch.stop)
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        self._runuser_patch = patch.object(writer_module.subprocess, "run", return_value=completed)
        self._runuser_patch.start()
        self.addCleanup(self._runuser_patch.stop)
        # Tests exercise the create path as the allowlisted profile; the empty-allowlist refusal is tested separately.
        self._allowlist_patch = patch.object(writer_module, "LIVE_PROFILE_ALLOWLIST", frozenset({writer_module.desktop.PROFILE_ID}))
        self._allowlist_patch.start()
        self.addCleanup(self._allowlist_patch.stop)

    def writer(self, directory, *, failure=None, wrong_readback_at=None, display_count=0, healthy=True,
               access_probe=None, fail_vm_readback=False, device_readback_issue=None,
               reported_service_account_uid=None):
        fixture = load_reviewed_desktop_fixture()
        request, artifacts = _fixture_with_test_pins(self, directory)
        client = FakeMiddleware(fixture, failure=failure, wrong_readback_at=wrong_readback_at,
                                display_count=display_count, healthy=healthy,
                                fail_vm_readback=fail_vm_readback,
                                device_readback_issue=device_readback_issue)
        if access_probe is None:
            access_probe = lambda path, user: True
        account = pwd.getpwuid(os.geteuid()).pw_name if os.geteuid() else "nobody"
        mountpoint = Path(directory) / "per-vm-dataset"
        mountpoint.mkdir(mode=0o700, exist_ok=True)
        writer = DesktopCreateOnlyWriter(
            client, fixture, request, artifacts,
            manifest_path=Path(directory) / "operation.json",
            service_account=account,
            reported_service_account_uid=reported_service_account_uid,
            access_probe=access_probe,
            target_registry_root=Path(directory) / "targets",
            artifact_verifier=lambda *args: {
                "filename": writer_module.desktop.IMAGE_FILENAME,
                "sha256": writer_module.desktop.ARTIFACT_SHA256,
                "size_bytes": writer_module.desktop.ARTIFACT_SIZE_BYTES,
                "signing_key_fingerprint": writer_module.desktop.SIGNING_KEY_FINGERPRINT,
                "manifest_sha256": "b" * 64,
                "signature_verified": True, "iso_bytes_verified": True,
                "live_install_allowed": False,
            },
            catalogue_verifier=lambda *args: {
                "verified_from_iso": True, "source_id": "ubuntu-desktop-minimal",
                "source_ids": ["ubuntu-desktop-minimal"], "catalogue_sha256": "a" * 64,
            },
        )
        return writer, client, mountpoint

    def confirmation(self, writer):
        with patch.object(writer, "_require_root", return_value=None):
            return writer.confirmation_phrase()

    def assert_no_create_state(self, client, directory):
        self.assertEqual(client.writes, [])
        self.assertEqual(client.datasets, {})
        self.assertEqual(client.vms, {})
        self.assertFalse((Path(directory) / "operation.json").exists())
        self.assertFalse((Path(directory) / "targets").exists())

    def test_missing_service_account_refuses_direct_create_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            confirmation = self.confirmation(writer)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module.pwd, "getpwnam", side_effect=KeyError("missing-account")), \
                 self.assertRaisesRegex(DesktopWriterError, "service account is unavailable"):
                writer.create(confirmation=confirmation)
            self.assert_no_create_state(client, directory)

    def test_root_vm_service_account_refuses_direct_create_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            confirmation = self.confirmation(writer)
            writer.service_account = "root"
            root_account = pwd.struct_passwd(("root", "x", 0, 0, "root", "/root", "/bin/sh"))
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module.pwd, "getpwnam", return_value=root_account), \
                 self.assertRaisesRegex(DesktopWriterError, "must be a non-root"):
                writer.create(confirmation=confirmation)
            self.assert_no_create_state(client, directory)

    def test_missing_runuser_refuses_direct_create_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            confirmation = self.confirmation(writer)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module.shutil, "which", return_value=None), \
                 self.assertRaisesRegex(DesktopWriterError, "runuser is required"):
                writer.create(confirmation=confirmation)
            self.assert_no_create_state(client, directory)

    def test_service_account_unavailable_before_create_refuses_after_prior_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            confirmation = self.confirmation(writer)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module.pwd, "getpwnam", side_effect=KeyError("removed-account")), \
                 self.assertRaisesRegex(DesktopWriterError, "service account is unavailable"):
                writer.create(confirmation=confirmation)
            self.assert_no_create_state(client, directory)

    def test_account_disappearing_during_readonly_preflight_stops_before_state_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            confirmation = self.confirmation(writer)
            account = pwd.getpwnam(writer.service_account)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module.pwd, "getpwnam", side_effect=[account, KeyError("removed-account")]), \
                 self.assertRaisesRegex(DesktopWriterError, "service account is unavailable"):
                writer.create(confirmation=confirmation)
            self.assertGreater(len(client.calls), 0)
            self.assert_no_create_state(client, directory)

    def test_reported_qemu_uid_match_is_bound_to_plan_and_create_stays_stopped(self):
        uid = os.geteuid() or 2001
        gid = os.getegid() if os.geteuid() else 2001
        account = pwd.struct_passwd(("vm-runner", "x", uid, gid, "VM process", "/", "/usr/sbin/nologin"))
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory, reported_service_account_uid=uid)
            writer.service_account = "vm-runner"
            with patch.object(writer_module.pwd, "getpwnam", return_value=account), \
                 patch.object(writer_module, "_active_qemu_process_owner", return_value=(uid, "vm-runner")), \
                 patch.object(writer_module.shutil, "which", return_value="/usr/sbin/runuser"), \
                 patch.object(writer_module.subprocess, "run", return_value=completed), \
                 patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
                confirmation = writer.confirmation_phrase()
                checked = writer._check_inputs()
                plan = writer._confirmation_plan(*checked[1:])
                result = writer.create(confirmation=confirmation)
            self.assertEqual(plan["service_account_identity"]["operator_reported_uid"], uid)
            self.assertEqual(plan["service_account_identity"]["live_verified"], {"name": "vm-runner", "uid": uid})
            self.assertEqual(result["status"], "created_not_started_live_unverified")
            self.assertFalse(result["vm_started"])
            self.assertTrue(client.writes)

    def test_reported_qemu_uid_mismatch_refuses_before_any_persistent_write(self):
        uid = os.geteuid() or 2001
        gid = os.getegid() if os.geteuid() else 2001
        account = pwd.struct_passwd(("vm-runner", "x", uid, gid, "VM process", "/", "/usr/sbin/nologin"))
        changed = pwd.struct_passwd(("vm-runner", "x", uid + 1, gid, "VM process", "/", "/usr/sbin/nologin"))
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory, reported_service_account_uid=uid)
            writer.service_account = "vm-runner"
            with patch.object(writer_module.pwd, "getpwnam", return_value=account), \
                 patch.object(writer_module, "_active_qemu_process_owner", return_value=(uid, "vm-runner")), \
                 patch.object(writer_module.shutil, "which", return_value="/usr/sbin/runuser"), \
                 patch.object(writer_module.subprocess, "run", return_value=subprocess.CompletedProcess(args=[], returncode=0)), \
                 patch.object(writer, "_require_root", return_value=None):
                confirmation = writer.confirmation_phrase()
                with patch.object(writer_module.pwd, "getpwnam", return_value=changed), \
                     patch.object(writer_module, "_active_qemu_process_owner", return_value=(uid + 1, "vm-runner")), \
                     self.assertRaisesRegex(DesktopWriterError, "differs from the operator-reported UID"):
                    writer.create(confirmation=confirmation)
            self.assertEqual(client.calls, [])
            self.assert_no_create_state(client, directory)

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
            self.assertEqual(display_creates[0]["attributes"], {
                "dtype": "DISPLAY", "type": "SPICE", "bind": "127.0.0.1",
                "web": True, "wait": False, "resolution": "1024x768",
                "port": None, "web_port": None, "password": None,
            })
            display_readback = client.devices[result["display_device_id"]]["attributes"]
            self.assertEqual(display_readback["port"], 5900)
            self.assertEqual(display_readback["web_port"], 5901)

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
            self.assertNotIn("password", manifest["resources"][4]["attributes"])
            self.assertTrue(all(
                item["attributes"].get("dtype") != "DISPLAY"
                or item.get("creation_method") == "vm.device.create"
                for item in manifest["resources"] if item["kind"] == "device"
            ))

    def test_existing_shared_dataset_is_reused_without_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory)
            client.datasets["tanktest/linux-vms"] = {
                "id": "tanktest/linux-vms", "type": "FILESYSTEM",
                "mountpoint": str(mountpoint), "available": 100 * _GIB,
            }
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint):
                result = writer.create(confirmation=self.confirmation(writer))
            dataset_writes = [
                params[0]["name"] for method, params in client.writes
                if method == "pool.dataset.create"
            ]
            self.assertNotIn("tanktest/linux-vms", dataset_writes)
            self.assertEqual(len(dataset_writes), 2)
            self.assertEqual(result["resource_count"], 7)
            manifest = json.loads((Path(directory) / "operation.json").read_text())
            self.assertTrue(manifest["inventory_before"]["shared_parent_exists"])

    def test_insufficient_privilege_refuses_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            with self.assertRaisesRegex(DesktopWriterError, "requires an authenticated root"):
                writer.create(confirmation="not relevant")
            self.assertEqual(client.writes, [])
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_wrong_confirmation_refuses_before_any_write_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            with patch.object(writer, "_require_root", return_value=None), self.assertRaises(DesktopWriterError):
                writer.create(confirmation="CREATE DesktopTest wrongphrase")
            self.assertFalse(client.writes)
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_wrong_verified_hostname_refuses_before_any_write_or_manifest(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            writer.request = replace(writer.request, expected_hostname="different-host")
            with patch.object(writer, "_require_root", return_value=None), self.assertRaisesRegex(DesktopWriterError, "hostname"):
                writer.create(confirmation=self.confirmation(writer))
            self.assertFalse(client.writes)
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_failed_artifact_verification_stops_before_middleware_contact(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory)
            writer.artifact_verifier = lambda *args: {"signature_verified": False, "iso_bytes_verified": False}
            with patch.object(writer, "_require_root", return_value=None), self.assertRaises(DesktopWriterError):
                writer.create(confirmation="unused")
            self.assertEqual(client.calls, [])
            self.assertFalse(client.writes)
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_preflight_refusal_precedes_any_write_or_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, _ = self.writer(directory, healthy=False)
            with patch.object(writer, "_require_root", return_value=None), self.assertRaises(DesktopWriterError):
                writer.create(confirmation=self.confirmation(writer))
            self.assertFalse(client.writes)
            self.assertFalse((Path(directory) / "operation.json").exists())

    def test_empty_allowlist_refuses_create_with_zero_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory)
            confirmation = self.confirmation(writer)
            with patch.object(writer_module, "LIVE_PROFILE_ALLOWLIST", frozenset()), \
                 patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                 self.assertRaisesRegex(DesktopWriterError, "not allowlisted"):
                writer.create(confirmation=confirmation)
            self.assertEqual(client.calls, [])
            self.assert_no_create_state(client, directory)

    def _refused_before_seed_attach(self, directory, *, patches=(), seed_bytes=None):
        writer, client, mountpoint = self.writer(directory)
        if seed_bytes is not None:
            (Path(directory) / "seed.img").write_bytes(seed_bytes)
        confirmation = self.confirmation(writer)
        with patch.object(writer, "_require_root", return_value=None), \
             patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
             ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            with self.assertRaises(DesktopWriterError):
                writer.create(confirmation=confirmation)
        seed_attaches = [p for m, p in client.writes if m == "vm.device.create" and p[0]["attributes"]["dtype"] == "RAW"]
        self.assertEqual(seed_attaches, [])
        return client

    def test_seed_pin_mismatch_aborts_with_no_seed_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            mismatch = patch.object(writer_module, "verify_staged_seed",
                                    side_effect=writer_module.DesktopIntegrityError("pin mismatch"))
            client = self._refused_before_seed_attach(directory, patches=[mismatch])
            self.assertTrue(any(m == "vm.device.create" for m, _ in client.writes))  # disk/CDROM attached first
            manifest = json.loads((Path(directory) / "operation.json").read_text())
            self.assertEqual(manifest["status"], "reconcile_required")

    def test_seed_wrong_size_aborts_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory)
            (Path(directory) / "seed.img").write_bytes(b"short")
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                 self.assertRaisesRegex(DesktopWriterError, "fixed seed size"):
                writer.create(confirmation="x")
            self.assert_no_create_state(client, directory)

    def test_missing_seed_aborts_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory)
            (Path(directory) / "seed.img").unlink()
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                 self.assertRaises(DesktopWriterError):
                writer.create(confirmation="x")
            self.assert_no_create_state(client, directory)

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

    def test_vm_readback_failure_preserves_successful_create_id_for_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            writer, client, mountpoint = self.writer(directory, fail_vm_readback=True)
            with patch.object(writer, "_require_root", return_value=None), \
                 patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                 self.assertRaises(DesktopWriterError):
                writer.create(confirmation=self.confirmation(writer))
            manifest = json.loads((Path(directory) / "operation.json").read_text())
            self.assertEqual(len(client.writes), 4)
            self.assertEqual(manifest["pending"]["identifier"], "DesktopTest")
            self.assertEqual(manifest["pending"]["api_id"], 41)
            self.assertEqual(manifest["pending"]["state"], "ambiguous")
            self.assertIn(41, client.vms)

    def test_device_readback_failure_after_valid_create_records_id_and_stops(self):
        for issue in ("empty", "error"):
            with self.subTest(issue=issue), tempfile.TemporaryDirectory() as directory:
                writer, client, mountpoint = self.writer(directory, device_readback_issue=issue)
                with patch.object(writer, "_require_root", return_value=None), \
                     patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                     self.assertRaises(DesktopWriterError):
                    writer.create(confirmation=self.confirmation(writer))
                manifest = json.loads((Path(directory) / "operation.json").read_text())
                self.assertEqual(len(client.writes), 5)
                self.assertEqual(client.writes[-1][0], "vm.device.create")
                self.assertEqual(manifest["pending"]["identifier"], "DesktopTest:display")
                self.assertEqual(manifest["pending"]["api_id"], 91)
                self.assertEqual(manifest["pending"]["vm_id"], 41)
                self.assertEqual(manifest["pending"]["state"], "ambiguous")
                self.assertEqual(len(client.devices), 1)
                self.assertEqual(manifest["status"], "reconcile_required")

    def test_returned_ids_are_durable_before_response_validation_and_stop(self):
        for method in ("vm.create", "vm.device.create"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                writer, client, mountpoint = self.writer(directory)
                original_call = client.call

                def malformed_response(call_method, params=None):
                    result = original_call(call_method, params)
                    if call_method != method or not isinstance(result, dict):
                        return result
                    if method == "vm.create":
                        return {**result, "name": "UnexpectedVM"}
                    vm_id = result.get("vm")
                    if type(vm_id) is int:
                        return {**result, "vm": vm_id + 1000}
                    return result

                with patch.object(writer, "_require_root", return_value=None), \
                     patch.object(writer_module, "_exact_mountpoint", return_value=mountpoint), \
                     patch.object(client, "call", side_effect=malformed_response), \
                     self.assertRaises(DesktopWriterError):
                    writer.create(confirmation=self.confirmation(writer))
                manifest = json.loads((Path(directory) / "operation.json").read_text())
                self.assertEqual(manifest["status"], "reconcile_required")
                self.assertEqual(manifest["pending"]["state"], "ambiguous")
                self.assertEqual(len(client.writes), 4 if method == "vm.create" else 5)
                self.assertEqual(client.writes[-1][0], method)
                self.assertEqual(manifest["pending"]["api_id"], 41 if method == "vm.create" else 91)
                if method == "vm.device.create":
                    self.assertEqual(manifest["pending"]["vm_id"], 41)
                    self.assertEqual(len(client.devices), 1)
                else:
                    self.assertIn(41, client.vms)

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
