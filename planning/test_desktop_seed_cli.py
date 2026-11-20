"""Offline tests for private seed password prompting and output redaction."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from planning import desktop_seed_cli


class DesktopSeedCliTests(unittest.TestCase):
    def test_password_is_hidden_and_never_printed_or_passed_in_arguments(self):
        password = "OfflineSynthetic-Only-Password!"
        password_hash = "$6$testsalt$" + "A" * 86
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "cidata.img"
            prompts = []

            def hidden_prompt(prompt):
                prompts.append(prompt)
                return password

            argv = [
                "--output", os.fspath(output_path), "--vm-name", "DesktopTest",
                "--realname", "Synthetic Operator", "--username", "testuser",
                "--source-id", "ubuntu-desktop-minimal",
            ]
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(desktop_seed_cli.getpass, "getpass", side_effect=hidden_prompt), \
                 patch.object(desktop_seed_cli, "hash_desktop_password", return_value=password_hash) as hash_password, \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                code = desktop_seed_cli.main(argv)
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(len(prompts), 2)
            self.assertEqual(prompts[0], "Ubuntu guest account password (hidden): ")
            self.assertEqual(prompts[1], "Repeat Ubuntu guest account password (hidden): ")
            hash_password.assert_called_once_with(password)
            self.assertNotIn(password, repr(argv))
            self.assertNotIn(password, stdout.getvalue())
            self.assertNotIn(password_hash, stdout.getvalue())
            self.assertNotIn(password, stderr.getvalue())
            self.assertNotIn(password_hash, stderr.getvalue())
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.stat().st_size, 2 * 1024 * 1024)
            self.assertEqual(os.stat(output_path).st_mode & 0o777, 0o600)
            image = output_path.read_bytes()
            self.assertNotIn(password.encode(), image)
            self.assertIn(password_hash.encode(), image)
            self.assertEqual(__import__("json").loads(stdout.getvalue())["plaintext_or_hash_printed"], False)


if __name__ == "__main__":
    unittest.main()
