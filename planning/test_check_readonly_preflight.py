"""CLI tests for no-bridge Desktop read-only preflight."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from planning import check_readonly_preflight as cli
from planning.ubuntu_desktop import STAGING_RESERVE_BYTES


class ReadonlyPreflightCliTests(unittest.TestCase):
    def test_desktop_accepts_no_bridge_and_reports_read_only_plan(self):
        captured = {}
        report = SimpleNamespace(
            truenas_version="TrueNAS-26.0.0-BETA.2",
            checked_method_contracts=12,
            vm_name="DesktopTrialTest",
            pool_name="testpool",
            bridge_name=None,
            checks=("pool capacity", "VM and storage collision checks"),
            system_hostname="host-test",
            pool_free_bytes=123456789,
            physical_memory_bytes=17179869184,
            available_memory_bytes=8589934592,
            maximum_supported_vcpus=16,
            shared_parent_exists=True,
            target_vm_name_absent=True,
            target_storage_paths_absent=True,
        )
        client = SimpleNamespace(close=lambda: None)

        def run(_client, _fixture, request):
            captured["request"] = request
            return report

        args = [
            "--profile", "ubuntu-desktop", "--vm-name", "DesktopTrialTest",
            "--pool", "testpool", "--vcpus", "2", "--memory-mib", "6144",
            "--disk-gib", "32", "--host-reserve-mib", "16384",
            "--pool-reserve-mib", "16384",
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, "load_fixture", return_value={"truenas_version": "TrueNAS-26.0.0-BETA.2"}), \
                patch.object(cli, "MidcltClient", return_value=client), \
                patch.object(cli, "run_read_only_preflight", side_effect=run), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(args)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIsNone(captured["request"].bridge_name)
        self.assertEqual(captured["request"].staging_reserve_bytes, STAGING_RESERVE_BYTES)
        self.assertIn("No write methods called", stdout.getvalue())
        self.assertIn("bridge=None", stdout.getvalue())
        self.assertIn(
            "hostname=host-test, pool_free_bytes=123456789, physical_memory_bytes=17179869184, "
            "available_memory_bytes=8589934592, maximum_supported_vcpus=16, "
            "shared_parent_exists=True, target_vm_name_absent=True, "
            "target_storage_paths_absent=True",
            stdout.getvalue(),
        )

    def test_server_without_bridge_refuses_before_middleware_calls(self):
        class Client:
            calls = 0
            def call(self, *_args, **_kwargs):
                self.calls += 1
                raise AssertionError("middleware must not be contacted")
            def close(self):
                pass

        client = Client()
        stdout, stderr = io.StringIO(), io.StringIO()
        args = [
            "--profile", "ubuntu-server", "--vm-name", "UbuntuServerTest",
            "--pool", "testpool", "--vcpus", "2", "--memory-mib", "6144",
            "--disk-gib", "32", "--host-reserve-mib", "16384",
            "--pool-reserve-mib", "16384",
        ]
        with patch.object(cli, "load_fixture", return_value={"truenas_version": "TrueNAS-26.0.0-BETA.2"}), \
                patch.object(cli, "MidcltClient", return_value=client), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(args)
        self.assertEqual(code, 1)
        self.assertEqual(client.calls, 0)
        self.assertIn("bridge selection is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
