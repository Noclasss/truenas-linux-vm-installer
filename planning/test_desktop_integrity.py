"""Offline refusal tests and a disposable local GnuPG round trip."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import desktop_integrity as integrity


FINGERPRINT = "843938DF228D22F7B3742BC0D94AA3F0EFE21092"
OTHER = "A" * 40


def status(signer=FINGERPRINT, primary=FINGERPRINT, algorithm="8", sigclass="00"):
    return (f"[GNUPG:] VALIDSIG {signer} 2026-11-16 1794873600 0 4 0 1 "
            f"{algorithm} {sigclass} {primary}\n")


class DesktopIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = b"synthetic Desktop ISO bytes"
        self.digest = hashlib.sha256(self.image).hexdigest()
        self.filename = "ubuntu-test-desktop-amd64.iso"
        self.manifest = f"{self.digest} *{self.filename}\n".encode()
        self.paths = [self.root / name for name in ("image.iso", "SHA256SUMS",
                                                    "SHA256SUMS.gpg", "keyring.gpg")]
        for path, value in zip(self.paths, (self.image, self.manifest, b"signature", b"key")):
            path.write_bytes(value)

    def verify(self):
        with patch.multiple(integrity.desktop, IMAGE_FILENAME=self.filename,
                            ARTIFACT_SHA256=self.digest, ARTIFACT_SIZE_BYTES=len(self.image)):
            return integrity.verify_desktop_artifacts(*self.paths)

    def test_pinned_primary_or_signing_subkey(self):
        integrity._check_status(status(), FINGERPRINT)
        integrity._check_status(status(signer=OTHER), FINGERPRINT)

    def test_untrusted_missing_malformed_or_weak_signature(self):
        values = ["", "[GNUPG:] GOODSIG D94AA3F0EFE21092 Ubuntu\n",
                  status(primary=OTHER), status(signer="invalid"),
                  status(algorithm="2"), status(sigclass="01"),
                  "[GNUPG:] VALIDSIG " + FINGERPRINT]
        for value in values:
            with self.subTest(value=value), self.assertRaises(integrity.DesktopIntegrityError):
                integrity._check_status(value, FINGERPRINT)

    def test_error_status_overrides_valid_signature(self):
        for error in ("BADSIG", "ERRSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY",
                      "EXPSIG", "FAILURE", "ERROR", "NODATA", "KEYEXPIRED", "SIGEXPIRED"):
            with self.subTest(error=error), self.assertRaises(integrity.DesktopIntegrityError):
                integrity._check_status(status() + f"[GNUPG:] {error} detail\n", FINGERPRINT)

    def test_exact_filename_unique_and_matching_pin(self):
        self.assertEqual(integrity._manifest_digest(self.manifest, self.filename, self.digest),
                         self.digest)
        for value in (self.manifest * 2, b"", b"\xff", b"malformed",
                      self.manifest.replace(self.filename.encode(), b"other.iso"),
                      self.manifest.replace(self.digest.encode(), b"0" * 64)):
            with self.subTest(value=value), self.assertRaises(integrity.DesktopIntegrityError):
                integrity._manifest_digest(value, self.filename, self.digest)

    def test_success_is_not_live_or_source_authorization(self):
        with patch.object(integrity, "_verify_signature") as verifier:
            result = self.verify()
        verifier.assert_called_once_with(self.manifest, b"signature", b"key", FINGERPRINT)
        self.assertTrue(result["signature_verified"])
        self.assertTrue(result["iso_bytes_verified"])
        self.assertFalse(result["live_install_allowed"])
        self.assertFalse(result["source_id_verified"])

    def test_signature_failure_stops_before_iso_read(self):
        with patch.object(integrity, "_verify_signature",
                          side_effect=integrity.DesktopIntegrityError("bad signature")), \
                patch.object(integrity, "_hash_image") as image:
            with self.assertRaises(integrity.DesktopIntegrityError):
                self.verify()
        image.assert_not_called()

    def test_wrong_size_or_digest_refused(self):
        for value in (b"short", b"x" * len(self.image)):
            self.paths[0].write_bytes(value)
            with patch.object(integrity, "_verify_signature"), \
                    self.assertRaises(integrity.DesktopIntegrityError):
                self.verify()

    def test_changed_image_metadata_refused(self):
        real_fstat = os.fstat
        calls = []
        def changed(fd):
            actual = real_fstat(fd)
            calls.append(actual)
            if len(calls) == 3:
                from types import SimpleNamespace
                return SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino,
                                       st_size=actual.st_size, st_mtime_ns=actual.st_mtime_ns + 1,
                                       st_ctime_ns=actual.st_ctime_ns)
            return actual
        with patch.object(integrity.os, "fstat", side_effect=changed), \
                self.assertRaisesRegex(integrity.DesktopIntegrityError, "changed"):
            integrity._hash_image(self.paths[0], len(self.image), self.digest)

    def test_symlink_directory_fifo_and_oversized_input_refused(self):
        link = self.root / "link"
        link.symlink_to(self.paths[1])
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for path in (link, fifo, self.root):
            with self.subTest(path=path), self.assertRaises((OSError, integrity.DesktopIntegrityError)):
                integrity._read_small(path, 100)
        with self.assertRaises(integrity.DesktopIntegrityError):
            integrity._read_small(self.paths[1], 1)

    def test_gpgv_uses_private_snapshot_and_explicit_trust(self):
        snapshot = []
        def runner(argv, **kwargs):
            root = Path(argv[argv.index("--homedir") + 1])
            snapshot.append(root)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            self.assertEqual((root / "SHA256SUMS").read_bytes(), self.manifest)
            self.assertEqual(os.stat(root / "trustedkeys.gpg").st_mode & 0o777, 0o600)
            self.assertEqual(argv[0], "gpgv")
            self.assertEqual(kwargs["timeout"], 30)
            self.assertEqual(kwargs["env"], {"PATH": os.defpath, "LC_ALL": "C"})
            self.assertNotIn("shell", kwargs)
            return subprocess.CompletedProcess(argv, 0, status(), "")
        with patch.object(integrity.subprocess, "run", side_effect=runner):
            integrity._verify_signature(self.manifest, b"signature", b"key", FINGERPRINT)
        self.assertFalse(snapshot[0].exists())

    def test_gpgv_error_timeout_and_missing_tool(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("gpgv", 30)):
            with patch.object(integrity.subprocess, "run", side_effect=error), \
                    self.assertRaises(integrity.DesktopIntegrityError):
                integrity._verify_signature(self.manifest, b"signature", b"key", FINGERPRINT)
        with patch.object(integrity.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1, status(), "")), \
                self.assertRaises(integrity.DesktopIntegrityError):
            integrity._verify_signature(self.manifest, b"signature", b"key", FINGERPRINT)

    def test_cli_reports_only_verified_result(self):
        args = [part for flag, path in zip(("--iso", "--manifest", "--signature", "--keyring"),
                                           self.paths) for part in (flag, str(path))]
        with patch.object(integrity, "verify_desktop_artifacts",
                          return_value={"live_install_allowed": False}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(integrity.main(args), 0)
        self.assertFalse(json.loads(output.getvalue())["live_install_allowed"])
        with patch.object(integrity, "verify_desktop_artifacts",
                          side_effect=integrity.DesktopIntegrityError("bad signature")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            integrity.main(args)
        self.assertEqual(exit.exception.code, 1)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv") and shutil.which("gpgconf"),
                     "disposable crypto round trip needs existing GnuPG tools")
class RealSignatureTests(unittest.TestCase):
    def test_real_signature_tampering_and_wrong_identity(self):
        with tempfile.TemporaryDirectory(prefix="desktop-test-gpg-") as directory:
            home = Path(directory)
            def gpg(*args):
                return subprocess.run(
                    ["gpg", "--homedir", str(home), "--batch", "--no-tty", *args],
                    stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=30,
                ).stdout
            try:
                gpg("--pinentry-mode", "loopback", "--passphrase", "",
                    "--quick-generate-key", "Disposable test <test@example.invalid>",
                    "ed25519", "sign", "0")
                listing = gpg("--with-colons", "--list-keys").decode()
                fingerprint = next(line.split(":")[9] for line in listing.splitlines()
                                   if line.startswith("fpr:"))
                manifest = b"0" * 64 + b" *synthetic.iso\n"
                (home / "manifest").write_bytes(manifest)
                gpg("--pinentry-mode", "loopback", "--passphrase", "", "--digest-algo", "SHA256",
                    "--output", str(home / "signature"), "--detach-sign", str(home / "manifest"))
                signature = (home / "signature").read_bytes()
                keyring = gpg("--export", fingerprint)
                integrity._verify_signature(manifest, signature, keyring, fingerprint)
                with self.assertRaises(integrity.DesktopIntegrityError):
                    integrity._verify_signature(manifest + b"tampered", signature, keyring, fingerprint)
                with self.assertRaises(integrity.DesktopIntegrityError):
                    integrity._verify_signature(manifest, signature, keyring, FINGERPRINT)
            finally:
                subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
