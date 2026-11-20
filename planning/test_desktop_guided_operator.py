"""Local-only tests for the pinned guided create entry point."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from planning import desktop_guided_operator as guided
from planning import desktop_seed_cli
from planning.desktop_write_contracts import load_reviewed_desktop_fixture


class DesktopGuidedOperatorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_reviewed_desktop_fixture()
        self.args = [
            "--expected-hostname", "example-host", "--vm-name", "ExampleVm",
            "--pool-name", "testpool", "--vcpus", "2", "--memory-mib", "6144",
            "--disk-gib", "32", "--host-reserve-mib", "800",
            "--pool-reserve-mib", "5000", "--source-id", "ubuntu-desktop-minimal",
            "--realname", "Example Operator", "--username", "vmguest",
            "--locale", "en_US.UTF-8", "--keyboard-layout", "us",
            "--timezone", "UTC", "--service-account", "vm-runner",
            "--reported-service-account-uid", "2001",
            "--manifest-path", "/var/lib/truenas-linux-desktop/operations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.json",
        ]

    def _invoke(self, answers, *, prompts=None):
        output, errors = io.StringIO(), io.StringIO()
        def answer(prompt):
            if prompts is not None:
                prompts.append(prompt)
            return answers.pop(0)
        with redirect_stdout(output), redirect_stderr(errors):
            result = guided.main(self.args, input_fn=answer)
        return result, output.getvalue(), errors.getvalue()

    def test_cancel_at_settings_review_does_not_create_temp_files_or_escalate(self):
        with patch.object(guided, "download_and_verify") as download, \
             patch.object(desktop_seed_cli, "main") as build_seed, \
             patch.object(guided.subprocess, "run") as run, \
             patch.object(guided.tempfile, "TemporaryDirectory") as temporary:
            result, output, _ = self._invoke(["C"])
        self.assertEqual(result, 0)
        self.assertIn("testpool", output)
        download.assert_not_called()
        build_seed.assert_not_called()
        run.assert_not_called()
        temporary.assert_not_called()

    def test_edit_updates_reviewed_plan_and_prepare_cancel_cleans_local_workspace(self):
        with patch.object(guided, "download_and_verify") as download, \
             patch.object(desktop_seed_cli, "main") as build_seed, \
             patch.object(guided.subprocess, "run") as run:
            result, output, _ = self._invoke(["E", "2", "EditedVm", "A", "CANCEL"])
        self.assertEqual(result, 0)
        self.assertIn("VM name: EditedVm", output)
        self.assertIn("Temporary workspace:", output)
        self.assertIn("cidata.img", output)
        self.assertIn(f"cache/sha256-{guided.ARTIFACT_SHA256}.artifact", output)
        workspace = Path(next(line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("Temporary workspace: ")))
        download.assert_not_called()
        build_seed.assert_not_called()
        run.assert_not_called()
        self.assertFalse(workspace.exists())

    def test_pinned_download_failure_cleans_private_workspace_before_sudo(self):
        captured = {}

        def fail_download(_url, _digest, cache_dir, _max_bytes, expected_size=None):
            del expected_size
            cache = Path(cache_dir)
            captured["workspace"] = cache.parent
            (cache / "partial-download").write_text("synthetic partial input")
            raise OSError("synthetic pinned download failure")

        with patch.object(guided, "download_and_verify", side_effect=fail_download), \
             patch.object(desktop_seed_cli, "main") as build_seed, \
             patch.object(guided.subprocess, "run") as run:
            result, _, errors = self._invoke(["A", "PREPARE"])
        self.assertEqual(result, 1)
        self.assertIn("synthetic pinned download failure", errors)
        build_seed.assert_not_called()
        run.assert_not_called()
        self.assertFalse(captured["workspace"].exists())

    def test_prepare_verifies_inputs_builds_private_seed_and_only_launches_guarded_create(self):
        captured = {}
        prompts = []

        def fake_download(url, digest, cache_dir, max_bytes, expected_size=None):
            path = Path(cache_dir) / f"sha256-{digest}.artifact"
            path.write_bytes(url.encode("ascii"))
            os.chmod(path, 0o600)
            captured.setdefault("downloads", []).append((url, digest, max_bytes, expected_size))
            return path

        def fake_seed(argv):
            captured["seed_argv"] = list(argv)
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"synthetic credential-bearing seed")
            os.chmod(output, 0o600)
            return 0

        def fake_create(argv, **kwargs):
            captured["create_argv"] = list(argv)
            captured["create_kwargs"] = kwargs
            target_path = Path(argv[argv.index("--target") + 1])
            captured["target"] = json.loads(target_path.read_text())
            captured["target_mode"] = os.stat(target_path).st_mode & 0o777
            captured["seed_mode"] = os.stat(captured["target"]["seed_path"]).st_mode & 0o777
            captured["temp_root"] = target_path.parent
            return subprocess.CompletedProcess(argv, 0)

        command = ["sudo", "--", sys.executable, "-B", "-m", "planning.desktop_operator", "create"]
        with patch.object(guided, "download_and_verify", side_effect=fake_download), \
             patch.object(guided, "verify_desktop_artifacts", return_value={"signature_verified": True, "iso_bytes_verified": True, "filename": guided.IMAGE_FILENAME, "sha256": guided.ARTIFACT_SHA256, "size_bytes": guided.ARTIFACT_SIZE_BYTES, "signing_key_fingerprint": guided.SIGNING_KEY_FINGERPRINT, "manifest_sha256": guided.CHECKSUM_MANIFEST_SHA256}), \
             patch.object(guided.desktop_catalogue, "verify_desktop_source_id", return_value={"verified_from_iso": True, "source_id": "ubuntu-desktop-minimal"}), \
             patch.object(desktop_seed_cli, "main", side_effect=fake_seed), \
             patch.object(guided, "_create_command", side_effect=lambda target: [*command, "--target", os.fspath(target)]), \
             patch.object(guided.subprocess, "run", side_effect=fake_create):
            result, output, errors = self._invoke(["A", "PREPARE"], prompts=prompts)

        self.assertEqual(result, 0, errors)
        self.assertIn("CREATE", output)
        self.assertIn("Type PREPARE to download/build these temporary local inputs; anything else cancels: ", prompts)
        self.assertEqual(len(captured["downloads"]), 4)
        self.assertEqual(captured["target"]["vm_name"], "ExampleVm")
        self.assertEqual(captured["target"]["pool_name"], "testpool")
        self.assertEqual(captured["target"]["vcpus"], 2)
        self.assertEqual(captured["target"]["memory_mib"], 6144)
        self.assertEqual(captured["target"]["disk_gib"], 32)
        self.assertEqual(captured["target"]["reported_service_account_uid"], 2001)
        self.assertEqual(captured["target"]["guest_identity"], {
            "realname": "Example Operator", "username": "vmguest", "locale": "en_US.UTF-8",
            "keyboard_layout": "us", "timezone": "UTC",
        })
        self.assertEqual(captured["target"]["host_reserve_mib"], 800)
        self.assertEqual(captured["target"]["pool_reserve_mib"], 5000)
        self.assertEqual(captured["target"]["network"], "none")
        self.assertFalse(captured["target"]["cache_on_selected_pool"])
        self.assertEqual(captured["target_mode"], 0o600)
        self.assertEqual(captured["seed_mode"], 0o600)
        self.assertEqual(captured["create_argv"][0], "sudo")
        self.assertNotIn("-n", captured["create_argv"])
        self.assertNotIn("-S", captured["create_argv"])
        self.assertIn("create", captured["create_argv"])
        self.assertNotIn("password", " ".join(captured["create_argv"]).lower())
        self.assertNotIn("--yes", captured["create_argv"])
        self.assertNotIn("input", captured["create_kwargs"])
        self.assertFalse(captured["temp_root"].exists())
        self.assertNotIn("Synthetic", errors)

    def test_create_command_uses_normal_sudo_only_for_nonroot(self):
        target = Path("/tmp/private/target.json")
        with patch.object(guided.os, "geteuid", return_value=1234):
            command = guided._create_command(target)
        self.assertEqual(command[:2], ["sudo", "--"])
        self.assertIn("planning.desktop_operator", command)
        self.assertIn("create", command)
        self.assertEqual(command[-1], os.fspath(target))
        self.assertNotIn("-n", command)
        self.assertNotIn("-S", command)


if __name__ == "__main__":
    unittest.main()
