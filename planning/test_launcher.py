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
                input="1\nlauncher-vm\ntank\nbr0\n2\n4096\n32\n",
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
        self.assertFalse(plan["vm"]["start"])


if __name__ == "__main__":
    unittest.main()
