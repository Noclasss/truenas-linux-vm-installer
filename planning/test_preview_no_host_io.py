"""Phase 2 gate: preview must not reach subprocess, sockets, HTTP, or file writes.

Each case runs the planner in a child interpreter whose host-I/O entry points
are replaced with functions that raise. A plan must still be produced, which
shows preview works without those paths.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLANNING = Path(__file__).resolve().parent
INVENTORY = PLANNING / "examples" / "inventory.json"

GUARD = r'''
import builtins, io, os, socket, subprocess, sys, urllib.request

def _denied(*args, **kwargs):
    raise RuntimeError("host I/O attempted by preview")

for name in ("Popen", "run", "call", "check_call", "check_output"):
    setattr(subprocess, name, _denied)
os.system = _denied
os.popen = _denied
os.spawnv = _denied
os.execv = _denied
socket.socket = _denied
socket.create_connection = _denied
urllib.request.urlopen = _denied

WRITE_MODES = set("wax+")
_real_open = io.open

def _read_only_open(file, mode="r", *args, **kwargs):
    if any(flag in WRITE_MODES for flag in mode):
        raise RuntimeError("file write attempted by preview")
    return _real_open(file, mode, *args, **kwargs)

builtins.open = _read_only_open
io.open = _read_only_open

sys.path.insert(0, sys.argv[1])
import preview
sys.exit(preview.main(sys.argv[2:]))
'''


def base_config(**overrides):
    config = {
        "profile": "ubuntu-server",
        "name": "gate-vm",
        "pool": "tank",
        "bridge": "br0",
        "vcpus": 2,
        "memory_mib": 6144,
        "disk_gib": 32,
    }
    config.update(overrides)
    return config


class PreviewNoHostIOTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_guarded(self, argv, stdin=""):
        result = subprocess.run(
            [sys.executable, "-E", "-s", "-c", GUARD, str(PLANNING), *argv],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=self.dir,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def write_config(self, config):
        path = self.dir / "vm.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_every_profile_plans_without_host_io(self):
        for profile in ("ubuntu-server", "ubuntu-desktop", "debian-server", "debian-desktop"):
            with self.subTest(profile=profile):
                config = self.write_config(base_config(profile=profile))
                result = self.run_guarded(
                    ["--config", str(config), "--inventory", str(INVENTORY)]
                )
                plan = json.loads(result.stdout)
                self.assertEqual(plan["mode"], "offline-preview")
                self.assertFalse(plan["apply_supported"])

    def test_interactive_plan_without_host_io(self):
        answers = "1\nguard-vm\n1\n1\n\n\n\n"
        result = self.run_guarded(["--interactive", "--inventory", str(INVENTORY)], stdin=answers)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["vm"]["name"], "guard-vm")
        self.assertFalse(plan["apply_supported"])

    def test_guard_actually_blocks_host_io(self):
        # Control case: the guard must fail if preview-style code tries I/O,
        # otherwise the tests above prove nothing.
        probe = GUARD.replace("import preview\nsys.exit(preview.main(sys.argv[2:]))",
                              "subprocess.run(['true'])")
        result = subprocess.run(
            [sys.executable, "-E", "-s", "-c", probe, str(PLANNING)],
            capture_output=True, text=True, cwd=self.dir, timeout=60, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("host I/O attempted", result.stderr)


if __name__ == "__main__":
    unittest.main()
