import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launch.sh"
PREFIX = "truenas-linux-vm-installer-0123456789abcdef0123456789abcdef01234567"


def build_archive(path, extra_members=(), include_planner=True):
    with tarfile.open(path, "w:gz") as archive:
        if include_planner:
            for source in sorted((ROOT / "planning").rglob("*.py")):
                if "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(ROOT)
                archive.add(source, arcname=f"{PREFIX}/{relative.as_posix()}")
            example = ROOT / "planning" / "examples" / "inventory.json"
            archive.add(example, arcname=f"{PREFIX}/planning/examples/inventory.json")
        for member in extra_members:
            archive.addfile(member[0], member[1])
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def info(name, kind=tarfile.REGTYPE, data=b"", linkname=""):
    entry = tarfile.TarInfo(name)
    entry.type = kind
    entry.linkname = linkname
    entry.size = len(data) if kind == tarfile.REGTYPE else 0
    return entry, io.BytesIO(data) if kind == tarfile.REGTYPE else None


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.tmp_root = self.dir / "tmp"
        self.tmp_root.mkdir()
        self.archive = self.dir / "release.tar.gz"

    def tearDown(self):
        self._tmp.cleanup()

    def run_launcher(self, *args):
        env = {"TMPDIR": str(self.tmp_root), "PATH": "/usr/bin:/bin"}
        return subprocess.run(
            [str(LAUNCHER), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.dir,
            timeout=60,
            check=False,
        )

    def assert_no_leftovers(self):
        self.assertEqual(list(self.tmp_root.iterdir()), [])

    def test_refuses_without_digest_before_touching_anything(self):
        digest = build_archive(self.archive)
        result = self.run_launcher("--archive", str(self.archive), "--", "--interactive")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--sha256 is required", result.stderr)
        self.assert_no_leftovers()
        self.assertTrue(digest)

    def test_rejects_malformed_digest_and_revision(self):
        digest = build_archive(self.archive)
        bad_digests = ["", "ABC", digest.upper(), digest[:-1] + "g"]
        for value in bad_digests:
            result = self.run_launcher("--archive", str(self.archive), "--sha256", value)
            self.assertEqual(result.returncode, 2, (value, result.stderr))
        bad = self.run_launcher("--revision", "main", "--sha256", digest)
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assert_no_leftovers()

    def test_requires_exactly_one_source(self):
        digest = build_archive(self.archive)
        both = self.run_launcher(
            "--archive", str(self.archive), "--revision", "a" * 40, "--sha256", digest
        )
        neither = self.run_launcher("--sha256", digest)
        self.assertEqual(both.returncode, 2)
        self.assertEqual(neither.returncode, 2)

    def test_digest_mismatch_extracts_and_runs_nothing(self):
        build_archive(self.archive)
        wrong = "0" * 64
        result = self.run_launcher("--archive", str(self.archive), "--sha256", wrong, "--", "--interactive")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_no_leftovers()

    def test_verified_archive_runs_preview_and_cleans_up(self):
        digest = build_archive(self.archive)
        config = ROOT / "planning" / "examples" / "vm.json"
        inventory = ROOT / "planning" / "examples" / "inventory.json"
        result = self.run_launcher(
            "--archive", str(self.archive), "--sha256", digest, "--",
            "--config", str(config), "--inventory", str(inventory),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["mode"], "offline-preview")
        self.assertFalse(plan["apply_supported"])
        self.assert_no_leftovers()

    def test_rejects_parent_traversal_member_even_with_matching_digest(self):
        digest = build_archive(
            self.archive,
            extra_members=[info(f"{PREFIX}/../escape.txt", data=b"x")],
        )
        result = self.run_launcher("--archive", str(self.archive), "--sha256", digest, "--", "--interactive")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unsafe member path", result.stderr)
        self.assertFalse((self.tmp_root / "escape.txt").exists())
        self.assert_no_leftovers()

    def test_rejects_absolute_member_path(self):
        digest = build_archive(
            self.archive,
            extra_members=[info("/etc/launch-test", data=b"x")],
        )
        result = self.run_launcher("--archive", str(self.archive), "--sha256", digest, "--", "--interactive")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unsafe member path", result.stderr)
        self.assert_no_leftovers()

    def test_rejects_symlink_member(self):
        digest = build_archive(
            self.archive,
            extra_members=[info(f"{PREFIX}/planning/link.py", kind=tarfile.SYMTYPE, linkname="/etc/passwd")],
        )
        result = self.run_launcher("--archive", str(self.archive), "--sha256", digest, "--", "--interactive")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("link or special file", result.stderr)
        self.assert_no_leftovers()

    def test_rejects_archive_without_planner(self):
        digest = build_archive(self.archive, include_planner=False)
        result = self.run_launcher("--archive", str(self.archive), "--sha256", digest, "--", "--interactive")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_no_leftovers()

    def test_launcher_is_posix_sh_compatible(self):
        result = subprocess.run(["sh", "-n", str(LAUNCHER)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
