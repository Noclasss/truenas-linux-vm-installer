"""Exercise artifact helpers through the standalone planning-directory import."""
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning.test_ubuntu_server import dummy_public_key


class StandaloneArtifactTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "standalone_ubuntu_server", directory / "ubuntu_server.py"
        )
        self.profile = importlib.util.module_from_spec(spec)
        # Match `import ubuntu_server` with planning on sys.path, without
        # replacing the package import used by the rest of the test suite.
        with patch.object(sys, "path", [str(directory), *sys.path]):
            spec.loader.exec_module(self.profile)
        self.assertFalse(self.profile.__package__)

    def test_standalone_import_builds_private_seed(self):
        def runner(args, **kwargs):
            Path(args[5]).write_bytes(b"mock-iso")
            return subprocess.CompletedProcess(args, 0)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cidata.iso"
            result = self.profile.build_seed_iso(
                output, "TestVM", "alice", [dummy_public_key()], runner=runner
            )
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"mock-iso")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_standalone_import_stages_private_raw_image(self):
        def runner(args, **kwargs):
            if args[1] == "convert":
                Path(args[-1]).write_bytes(b"mock-raw")
                return subprocess.CompletedProcess(args, 0)
            image_format = "raw" if "-f" in args else "qcow2"
            metadata = {
                "format": image_format,
                "virtual-size": self.profile.IMAGE_VIRTUAL_SIZE_BYTES,
                "dirty-flag": False,
            }
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(metadata))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "verified.qcow2"
            source.write_bytes(b"mock-source")
            output = root / "ubuntu-server.raw"
            with patch.object(self.profile, "download_ubuntu_server_image", return_value=source) as download:
                result = self.profile.stage_ubuntu_server_raw_image(
                    root / "cache", output, runner=runner
                )
            download.assert_called_once_with(root / "cache")
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"mock-raw")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse(list(root.glob(".ubuntu-raw-*")))
