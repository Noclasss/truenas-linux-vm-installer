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
TEST_REF = "a" * 40
BOOTSTRAP_SOURCE_REF = "bf0b64eec8e5cfc3ebdb73f3977d26a030ad6ee3"
GIT_ENV_KEYS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_TEMPLATE_DIR",
    "GIT_EXEC_PATH", "GIT_SSL_NO_VERIFY", "GIT_ASKPASS", "SSH_ASKPASS",
    *(name for index in range(10) for name in (f"GIT_CONFIG_KEY_{index}", f"GIT_CONFIG_VALUE_{index}")),
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG", "GIT_PROXY_COMMAND", "GIT_SSH",
    "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
    "GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_TRACE_SETUP",
    "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF",
)


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

    def _run_bootstrapper(self, *, revision=None, arguments=None, fail_fetch=False, fail_python=False, git_env=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            scratch = root / "scratch"
            scratch_target = root / "scratch-target"
            scratch_target.mkdir()
            scratch.symlink_to(scratch_target, target_is_directory=True)
            trace = root / "git-trace.json"
            launch_log = root / "launch.log"
            fake_git = fake_bin / "git"
            fake_git.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, pathlib, stat, sys\n"
                "args = sys.argv[1:]\n"
                "git_config = []\n"
                "while args and args[0] == '-c': git_config.append(args[1]); args = args[2:]\n"
                "repo = None\n"
                "if args and args[0] == '-C': repo, args = pathlib.Path(args[1]), args[2:]\n"
                "command = args[0] if args else ''\n"
                "events = json.loads(pathlib.Path(os.environ['BOOTSTRAP_TRACE']).read_text()) if pathlib.Path(os.environ['BOOTSTRAP_TRACE']).exists() else []\n"
                "env_keys = " + repr(GIT_ENV_KEYS) + "\n"
                "event = {'command': command, 'args': args[1:], 'config': git_config, 'git_environment': {key: os.environ.get(key) for key in env_keys}, 'fixed_environment': {key: os.environ.get(key) for key in ('GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_TERMINAL_PROMPT', 'GIT_LFS_SKIP_SMUDGE')}}\n"
                "if command == 'init':\n"
                "    repo = pathlib.Path(args[-1]); repo.mkdir(parents=True)\n"
                "    event['repo'] = str(repo)\n"
                "    event['temp_mode'] = stat.S_IMODE(repo.parent.stat().st_mode)\n"
                "elif command == 'fetch':\n"
                "    launcher = repo / 'planning' / 'desktop_readonly_preview.py'\n"
                "    launcher.parent.mkdir(parents=True)\n"
                "    launcher.write_text('# preview stub\\n')\n"
                "elif command == 'rev-parse':\n"
                "    print(os.environ.get('FAKE_GIT_REVISION', ''))\n"
                "events.append(event)\n"
                "pathlib.Path(os.environ['BOOTSTRAP_TRACE']).write_text(json.dumps(events))\n"
                "if command == 'fetch' and os.environ.get('FAKE_GIT_FAIL_FETCH') == '1': sys.exit(1)\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, sys\n"
                "pathlib.Path(os.environ['LAUNCH_LOG']).write_text(' '.join(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
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
            if fail_python:
                fake_python.write_text(
                    fake_python.read_text(encoding="utf-8") + "if os.environ.get('FAKE_PYTHON_FAIL') == '1': sys.exit(2)\n",
                    encoding="utf-8",
                )
                environment["FAKE_PYTHON_FAIL"] = "1"
            if arguments is None:
                arguments = ("--ref", TEST_REF, "--preview")
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

    def _assert_temp_removed(self, events):
        init_event = next(event for event in events if event["command"] == "init")
        self.assertFalse(Path(init_event["repo"]).parent.exists())

    def test_bootstrapper_checks_pinned_checkout_and_runs_only_desktop_preview(self):
        pinned_commit = TEST_REF
        injected_git_env = {key: f"sentinel-{key}" for key in GIT_ENV_KEYS}
        injected_git_env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.sslVerify",
            "GIT_CONFIG_VALUE_0": "false",
        })
        result, events, launched_args, remaining_temp = self._run_bootstrapper(
            revision=pinned_commit, git_env=injected_git_env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(launched_args, str)
        self.assertTrue(launched_args.endswith("planning/desktop_readonly_preview.py --preview"))
        self.assertTrue(any(event["command"] == "remote" and event["args"] == [
            "add", "origin", "https://github.com/Noclasss/truenas-linux-vm-installer.git"
        ] for event in events))
        fetch_event = next(event for event in events if event["command"] == "fetch")
        self.assertIn(pinned_commit, fetch_event["args"])
        self.assertIn("http.sslVerify=true", fetch_event["config"])
        self.assertIn("http.followRedirects=false", fetch_event["config"])
        for event in events:
            self.assertEqual(set(event["git_environment"]), set(GIT_ENV_KEYS))
            self.assertEqual(event["git_environment"]["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertTrue(all(value is None for key, value in event["git_environment"].items() if key != "GIT_CONFIG_GLOBAL"))
            self.assertEqual(event["fixed_environment"], {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_LFS_SKIP_SMUDGE": "1",
            })
        self.assertTrue(any(event.get("temp_mode") == 0o700 for event in events))
        temp_dir = Path(next(event for event in events if event["command"] == "init")["repo"]).parent
        self.assertEqual(temp_dir.parent, Path("/tmp"))
        self.assertTrue(temp_dir.name.startswith("linux-vm-desktop-preview."))
        self.assertFalse(temp_dir.exists())
        self.assertEqual(remaining_temp, [])
        self.assertIn("read-only Ubuntu Desktop preflight", result.stderr)

    def test_bootstrapper_refuses_mismatched_revision_before_launch(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(revision="0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(launched_args)
        self.assertTrue(any(event["command"] == "rev-parse" for event in events))
        self._assert_temp_removed(events)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_refuses_fetch_failure_and_cleans_temporary_tree(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(fail_fetch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(event["command"] == "fetch" for event in events))
        self.assertIsNone(launched_args)
        self._assert_temp_removed(events)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_cleans_temporary_tree_after_preview_failure(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(
            revision=TEST_REF,
            fail_python=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIsInstance(launched_args, str)
        self._assert_temp_removed(events)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_rejects_arguments_before_network_or_checkout(self):
        result, events, launched_args, remaining_temp = self._run_bootstrapper(arguments=("--apply",))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        self.assertIsNone(launched_args)
        self.assertEqual(remaining_temp, [])

    def test_bootstrapper_rejects_malformed_or_missing_revision_before_network(self):
        for arguments in (("--ref", "not-a-sha", "--preview"), ("--ref", TEST_REF)):
            with self.subTest(arguments=arguments):
                result, events, launched_args, remaining_temp = self._run_bootstrapper(arguments=arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events, [])
                self.assertIsNone(launched_args)
                self.assertEqual(remaining_temp, [])

    def test_bootstrapper_digest_matches_documented_one_line_pin(self):
        digest = hashlib.sha256(BOOTSTRAPPER.read_bytes()).hexdigest()
        pinned_script = subprocess.check_output(
            ["git", "show", f"{BOOTSTRAP_SOURCE_REF}:ubuntu-desktop-preview-bootstrap.sh"],
            cwd=ROOT,
        )
        self.assertEqual(BOOTSTRAPPER.read_bytes(), pinned_script)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        wrapper = next(line for line in readme.splitlines() if line.startswith("( set -eu; umask 077;"))
        ref_match = re.search(r"REF='([0-9a-f]{40})'", wrapper)
        digest_match = re.search(r"BOOTSTRAP_SHA256='([0-9a-f]{64})'", wrapper)
        self.assertIsNotNone(ref_match)
        self.assertIsNotNone(digest_match)
        assert ref_match is not None
        assert digest_match is not None
        self.assertEqual(ref_match.group(1), BOOTSTRAP_SOURCE_REF)
        self.assertEqual(digest_match.group(1), digest)
        self.assertIn('mktemp -d "/tmp/linux-vm-desktop-preview.', wrapper)
        self.assertNotIn("${TMPDIR", wrapper)
        self.assertIn("curl -fsSL --proto '=https' --proto-redir '=https'", wrapper)
        self.assertIn('raw.githubusercontent.com/Noclasss/truenas-linux-vm-installer/$REF/', wrapper)
        self.assertIn('sha256sum -c -', wrapper)
        self.assertLess(wrapper.index("sha256sum -c -"), wrapper.index('sh "$d/bootstrap.sh"'))
        self.assertIn('sh "$d/bootstrap.sh" --ref "$REF" --preview', wrapper)


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
