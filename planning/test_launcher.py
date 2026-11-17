import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
