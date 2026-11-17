import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAPPER = ROOT / "ubuntu-desktop-preview-bootstrap.sh"


class LauncherTests(unittest.TestCase):
    def test_launcher_runs_interactive_preview_from_any_working_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [str(ROOT / "install.sh"), "--interactive"],
                input="1\nlauncher-vm\ntank\nbr0\n3\n4\n3\n",
                text=True,
                capture_output=True,
                cwd=cwd,
                check=False,
                timeout=15,
            )
            self.assertEqual(list(Path(cwd).iterdir()), [])
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["mode"], "offline-preview")
        self.assertFalse(plan["apply_supported"])
        self.assertEqual(plan["vm"]["name"], "launcher-vm")
        self.assertEqual(plan["vm"]["cpu"]["total_vcpus"], 4)
        self.assertEqual(plan["vm"]["memory_mib"], 8192)
        self.assertEqual(plan["storage"]["disk_bytes"], 64 * 1024**3)
        self.assertFalse(plan["vm"]["start"])

    def _run_bootstrapper(self, *, revision=None, arguments=(), fail_fetch=False, git_env=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            scratch = root / "scratch"
            scratch.mkdir()
            trace = root / "git-trace.json"
            launch_log = root / "launch.log"
            fake_git = fake_bin / "git"
            fake_git.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, stat, sys\n"
                "args = sys.argv[1:]\n"
                "git_config = []\n"
                "while args and args[0] == '-c': git_config.append(args[1]); args = args[2:]\n"
                "repo = None\n"
                "if args and args[0] == '-C': repo, args = pathlib.Path(args[1]), args[2:]\n"
                "command = args[0] if args else ''\n"
                "events = json.loads(pathlib.Path(os.environ['BOOTSTRAP_TRACE']).read_text()) if pathlib.Path(os.environ['BOOTSTRAP_TRACE']).exists() else []\n"
                "event = {'command': command, 'args': args[1:], 'config': git_config, 'git_environment': {key: os.environ.get(key) for key in ('GIT_COMMON_DIR', 'GIT_TEMPLATE_DIR', 'GIT_EXEC_PATH', 'GIT_SSH_COMMAND', 'GIT_PROXY_COMMAND', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0')}}\n"
                "if command == 'init':\n"
                "    repo = pathlib.Path(args[-1]); repo.mkdir(parents=True)\n"
                "    event['repo'] = str(repo)\n"
                "    event['temp_mode'] = stat.S_IMODE(repo.parent.stat().st_mode)\n"
                "elif command == 'fetch':\n"
                "    launcher = repo / 'install.sh'\n"
                "    launcher.write_text('#!/bin/sh\\nprintf \\\"%s\\\\n\\\" \\\"$*\\\" > \\\"$LAUNCH_LOG\\\"\\n')\n"
                "    launcher.chmod(0o700)\n"
                "elif command == 'rev-parse':\n"
                "    print(os.environ.get('FAKE_GIT_REVISION', ''))\n"
                "events.append(event)\n"
                "pathlib.Path(os.environ['BOOTSTRAP_TRACE']).write_text(json.dumps(events))\n"
                "if command == 'fetch' and os.environ.get('FAKE_GIT_FAIL_FETCH') == '1': sys.exit(1)\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            environment = os.environ.copy()
            environment.update({
                "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
                "TMPDIR": str(scratch),
                "BOOTSTRAP_TRACE": str(trace),
                "LAUNCH_LOG": str(launch_log),
            })
            if git_env is not None:
                environment.update(git_env)
            if revision is not None:
                environment["FAKE_GIT_REVISION"] = revision
            if fail_fetch:
                environment["FAKE_GIT_FAIL_FETCH"] = "1"
            result = subprocess.run(
                ["sh", str(BOOTSTRAPPER), *arguments],
                text=True,
                capture_output=True,
                cwd=root,
                env=environment,
                check=False,
                timeout=15,
            )
            events = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else []
            launched_args = launch_log.read_text(encoding="utf-8").strip() if launch_log.exists() else None
            remaining_temp = list(scratch.iterdir())
        return result, events, launched_args, remaining_temp

    def test_bootstrapper_checks_pinned_checkout_and_runs_only_desktop_preview(self):
        script = BOOTSTRAPPER.read_text(encoding="utf-8")
        match = re.search(r"^reviewed_commit='([0-9a-f]{40})'$", script, re.MULTILINE)
        if match is None:
            self.fail("bootstrapper must pin a full commit SHA")
        pinned_commit = match.group(1)
        result, events, launched_args, remaining_temp = self._run_bootstrapper(
            revision=pinned_commit,
            git_env={
                "GIT_COMMON_DIR": "/tmp/injected-common",
                "GIT_TEMPLATE_DIR": "/tmp/injected-template",
                "GIT_EXEC_PATH": "/tmp/injected-exec",
                "GIT_SSH_COMMAND": "false",
                "GIT_PROXY_COMMAND": "false",
                "GIT_CONFIG_KEY_0": "http.sslVerify",
                "GIT_CONFIG_VALUE_0": "false",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(launched_args, "--interactive --profile ubuntu-desktop")
        self.assertTrue(any(event["command"] == "remote" and event["args"] == [
            "add", "origin", "https://github.com/Noclasss/truenas-linux-vm-installer.git"
        ] for event in events))
        fetch_event = next(event for event in events if event["command"] == "fetch")
        self.assertIn(pinned_commit, fetch_event["args"])
        self.assertIn("http.sslVerify=true", fetch_event["config"])
        self.assertIn("http.followRedirects=false", fetch_event["config"])
        for event in events:
            self.assertTrue(all(value is None for value in event["git_environment"].values()))
        self.assertTrue(any(event.get("temp_mode") == 0o700 for event in events))
        self.assertEqual(remaining_temp, [])
        self.assertIn("preview only", result.stderr)

    def test_bootstrapper_refuses_mismatched_revision_before_launch(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(revision="0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(launched_args)
        self.assertTrue(any(event["command"] == "rev-parse" for event in events))
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_refuses_fetch_failure_and_cleans_temporary_tree(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(fail_fetch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(event["command"] == "fetch" for event in events))
        self.assertIsNone(launched_args)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_rejects_arguments_before_network_or_checkout(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(arguments=("--apply",))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIsNone(launched_args)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_digest_matches_documented_one_line_pin(self):
        digest = hashlib.sha256(BOOTSTRAPPER.read_bytes()).hexdigest()
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(digest, readme)
        self.assertIn("curl -fsSL --proto '=https' --proto-redir '=https'", readme)

    def test_desktop_launcher_keeps_source_unset_and_is_preview_only(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [str(ROOT / "install.sh"), "--interactive", "--profile", "ubuntu-desktop"],
                input="preview-desktop\npreviewpool\npreviewbr\n\n\n\n",
                text=True,
                capture_output=True,
                cwd=cwd,
                check=False,
                timeout=15,
            )
            self.assertEqual(list(Path(cwd).iterdir()), [])
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["profile"]["id"], "ubuntu-desktop")
        self.assertIsNone(plan["guest_bootstrap"]["provisioning"]["desktop_source_id"])
        self.assertFalse(plan["apply_supported"])
        self.assertFalse(plan["vm"]["start"])
        self.assertFalse(plan["vm"]["autostart"])
        self.assertFalse(plan["network"]["modify_host_network"])

if __name__ == "__main__":
    unittest.main()
