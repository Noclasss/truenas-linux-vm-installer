"""Tests for exact-ISO catalogue extraction with existing system tools."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning import desktop_catalogue as catalogue

_CATALOGUE = b"id: ubuntu-desktop-minimal\nid: ubuntu-desktop\n"


class _FakeProcess:
    def __init__(self, content=b"", exit_code=0):
        self.stdout = tempfile.TemporaryFile()
        self.stdout.write(content)
        self.stdout.seek(0)
        self.exit_code = exit_code
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -9 if self.killed else self.exit_code
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class DesktopCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="catalogue-test-")
        self.addCleanup(self.temp.cleanup)
        self.iso = Path(self.temp.name) / "synthetic.iso"
        self.iso.write_bytes(b"synthetic pinned ISO")

    def test_prefers_xorriso_when_present(self):
        def which(name, path=None):
            return {"xorriso": "/usr/bin/xorriso", "7z": "/usr/bin/7z"}.get(name)

        def runner(argv, **kwargs):
            self.assertEqual(argv[0], "/usr/bin/xorriso")
            self.assertTrue(argv[argv.index("-indev") + 1].startswith("/proc/self/fd/"))
            fd = int(Path(argv[argv.index("-indev") + 1]).name)
            self.assertEqual(kwargs["pass_fds"], (fd,))
            self.assertIn("/casper/install-sources.yaml", argv)
            Path(argv[-1]).write_bytes(_CATALOGUE)
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(catalogue.shutil, "which", side_effect=which):
            result = catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal", runner=runner
            )
        self.assertEqual(result["source_ids"], ["ubuntu-desktop", "ubuntu-desktop-minimal"])
        self.assertEqual(result["catalogue_sha256"], hashlib.sha256(_CATALOGUE).hexdigest())
        self.assertTrue(result["iso_unchanged_during_extraction"])

    def test_xorriso_failure_or_missing_extracted_catalogue_is_refused(self):
        def which(name, path=None):
            return "/usr/bin/xorriso" if name == "xorriso" else None

        def fail_runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 2)

        with patch.object(catalogue.shutil, "which", side_effect=which), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "xorriso failed"):
            catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal", runner=fail_runner
            )

        def success_without_output(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(catalogue.shutil, "which", side_effect=which), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "could not be read safely"):
            catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal", runner=success_without_output
            )

    def test_refuses_iso_changed_during_extraction(self):
        def which(name, path=None):
            return "/usr/bin/xorriso" if name == "xorriso" else None

        def runner(argv, **kwargs):
            Path(argv[-1]).write_bytes(_CATALOGUE)
            self.iso.write_bytes(b"damaged during extraction")
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(catalogue.shutil, "which", side_effect=which), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "ISO changed"):
            catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal", runner=runner
            )

    def test_refuses_iso_path_replacement_during_extraction(self):
        def which(name, path=None):
            return "/usr/bin/xorriso" if name == "xorriso" else None

        def runner(argv, **kwargs):
            original = Path(self.temp.name) / "original.iso"
            self.iso.rename(original)
            self.iso.write_bytes(b"replacement ISO")
            Path(argv[-1]).write_bytes(_CATALOGUE)
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(catalogue.shutil, "which", side_effect=which), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "ISO changed"):
            catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal", runner=runner
            )

    def test_uses_7z_stdout_fallback_when_xorriso_is_absent(self):
        def which(name, path=None):
            return "/usr/bin/7z" if name == "7z" else None

        def spawn(argv, **kwargs):
            self.assertEqual(argv[:5], ["/usr/bin/7z", "e", "-so", "-spd", "-bd"])
            self.assertTrue(argv[5].startswith("/proc/self/fd/"))
            self.assertEqual(argv[6], "casper/install-sources.yaml")
            self.assertEqual(kwargs["pass_fds"], (int(Path(argv[5]).name),))
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["bufsize"], 0)
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(kwargs["env"], {"PATH": os.defpath, "LC_ALL": "C"})
            return _FakeProcess(_CATALOGUE)

        with patch.object(catalogue.shutil, "which", side_effect=which), \
                patch.object(catalogue.subprocess, "Popen", side_effect=spawn):
            result = catalogue.verify_desktop_source_id(
                self.iso, "ubuntu-desktop-minimal"
            )
        self.assertEqual(result["source_id"], "ubuntu-desktop-minimal")
        self.assertEqual(result["catalogue_sha256"], hashlib.sha256(_CATALOGUE).hexdigest())

    def test_rejects_7z_failure_or_unbounded_output(self):
        def which(name, path=None):
            return "/usr/bin/7z" if name == "7z" else None

        processes = iter((_FakeProcess(_CATALOGUE, exit_code=1),
                          _FakeProcess(b"x" * (catalogue._MAX_CATALOGUE_BYTES + 1))))
        with patch.object(catalogue.shutil, "which", side_effect=which), \
                patch.object(catalogue.subprocess, "Popen", side_effect=lambda *a, **k: next(processes)):
            with self.assertRaisesRegex(catalogue.DesktopCatalogueError, "7z failed"):
                catalogue.verify_desktop_source_id(self.iso, "ubuntu-desktop-minimal")
            with self.assertRaisesRegex(catalogue.DesktopCatalogueError, "byte limit"):
                catalogue.verify_desktop_source_id(self.iso, "ubuntu-desktop-minimal")

    def test_terminates_7z_when_catalogue_stream_times_out(self):
        process = _FakeProcess(_CATALOGUE)
        with patch.object(catalogue.shutil, "which", side_effect=lambda name, path=None: "/usr/bin/7z" if name == "7z" else None), \
                patch.object(catalogue.subprocess, "Popen", return_value=process), \
                patch.object(catalogue.select, "select", return_value=([], [], [])), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "timed out"):
            catalogue.verify_desktop_source_id(self.iso, "ubuntu-desktop-minimal")
        self.assertTrue(process.killed)

    def test_refuses_when_neither_extractor_exists(self):
        with patch.object(catalogue.shutil, "which", return_value=None), \
                self.assertRaisesRegex(catalogue.DesktopCatalogueError, "no supported"):
            catalogue.verify_desktop_source_id(self.iso, "ubuntu-desktop-minimal")


if __name__ == "__main__":
    unittest.main()
