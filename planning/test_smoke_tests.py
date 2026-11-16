"""Smoke driver refusal/evidence tests; never recursively run the full suite."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from planning import smoke_tests as smoke

SHA = "a" * 40
SUITE = ("test_real_signature_tampering_and_wrong_identity (test.RealSignatureTests) ... ok\n"
         "Ran 12 tests in 0.1s\n\nOK\n")


class SmokeTests(unittest.TestCase):
    def test_real_offline_desktop_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            report = smoke.exercise_desktop(Path(directory))
        self.assertEqual(report["acknowledged_resources"], 9)
        self.assertTrue(report["lost_response_refused"])
        self.assertTrue(report["recovery_read_only"])
        self.assertFalse(report["live_install_allowed"])
        self.assertEqual(len(report["profiles_previewed"]), 4)

    def test_checkout_requires_exact_sha_and_clean_tree(self):
        for responses in ((SHA, " M planning/preview.py"), ("b" * 40,), (SHA, "?? unexpected.py")):
            with self.subTest(responses=responses), patch.object(smoke, "_git", side_effect=responses), \
                    self.assertRaises(smoke.SmokeError):
                smoke.check_checkout(Path("."), SHA)
        with patch.object(smoke, "_git") as git, self.assertRaises(smoke.SmokeError):
            smoke.check_checkout(Path("."), "main")
        git.assert_not_called()

    def test_skips_or_missing_crypto_are_not_full_verification(self):
        evidence = smoke.suite_evidence(SUITE)
        self.assertEqual(evidence["tests_run"], 12)
        self.assertTrue(evidence["real_crypto_test_passed"])
        skipped = smoke.suite_evidence("Ran 12 tests in 0.1s\n\nOK (skipped=1)\n")
        self.assertEqual(skipped["skipped"], 1)
        self.assertFalse(skipped["real_crypto_test_passed"])
        with self.assertRaises(smoke.SmokeError):
            smoke.suite_evidence("OK\n")

    def fake_command(self, repo, argv, timeout=180):
        if "unittest" in argv:
            output = SUITE
        elif "install.sh" in argv:
            output = json.dumps({"apply_supported": False, "vm": {"start": False}})
        else:
            output = ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    def test_driver_writes_private_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            output = root / "evidence"
            with patch.object(smoke, "check_checkout"), patch.object(smoke, "_git", return_value=""), \
                    patch.object(smoke, "_command", side_effect=self.fake_command), \
                    patch.object(smoke, "exercise_desktop", return_value={"live_install_allowed": False}):
                report = smoke.run_smoke(repo, SHA, output)
                self.assertEqual(report["status"], "passed")
                self.assertFalse(report["actual_iso_verified"])
                self.assertFalse(report["host_preflight_run"])
                self.assertFalse(report["live_install_allowed"])
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                for path in output.iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                original = (output / "report.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    smoke.run_smoke(repo, SHA, output)
                self.assertEqual((output / "report.json").read_bytes(), original)

    def test_failed_or_timed_out_suite_retains_failure_report(self):
        for result in (subprocess.CompletedProcess([], 1, "", "test failure"),
                       subprocess.TimeoutExpired("python", 180)):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = root / "repo"
                repo.mkdir()
                output = root / "evidence"
                def command(*args, **kwargs):
                    if isinstance(result, Exception):
                        raise result
                    return result
                with patch.object(smoke, "check_checkout"), \
                        patch.object(smoke, "_command", side_effect=command), \
                        patch.object(smoke, "exercise_desktop") as exercise:
                    report = smoke.run_smoke(repo, SHA, output)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(json.loads((output / "report.json").read_text())["status"], "failed")
                self.assertTrue((output / "unit-tests.log").exists())
                exercise.assert_not_called()

    def test_driver_returns_incomplete_when_crypto_was_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            def command(repo, argv, timeout=180):
                if "unittest" in argv:
                    return subprocess.CompletedProcess(argv, 0, "", "Ran 12 tests in 0.1s\n\nOK (skipped=1)\n")
                return self.fake_command(repo, argv, timeout)
            with patch.object(smoke, "check_checkout"), patch.object(smoke, "_git", return_value=""), \
                    patch.object(smoke, "_command", side_effect=command), \
                    patch.object(smoke, "exercise_desktop", return_value={}):
                report = smoke.run_smoke(repo, SHA, root / "evidence")
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["suite"]["skipped"], 1)

    def test_output_inside_checkout_refused(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke, "check_checkout"):
            with self.assertRaises(smoke.SmokeError):
                smoke.run_smoke(Path(directory), SHA, Path(directory) / "evidence")
            self.assertFalse((Path(directory) / "evidence").exists())


if __name__ == "__main__":
    unittest.main()
