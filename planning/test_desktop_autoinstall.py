import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from planning.desktop_autoinstall import (
    DesktopAutoinstallError,
    build_desktop_autoinstall_user_data,
    build_desktop_meta_data,
    build_desktop_seed_image,
    hash_desktop_password,
)


VALID_PASSWORD_HASH = "$6$abcdefghijklmnop$" + "A" * 86


class DesktopAutoinstallTests(unittest.TestCase):
    def build_config(self, **overrides):
        values = {
            "vm_name": "UbuntuDesk01",
            "realname": "Local User",
            "username": "desktopuser",
            "password_hash": VALID_PASSWORD_HASH,
            "source_id": "ubuntu-desktop-minimal",
        }
        values.update(overrides)
        return build_desktop_autoinstall_user_data(**values)

    def test_builds_noninteractive_desktop_autoinstall_config(self):
        rendered = self.build_config()
        self.assertTrue(rendered.startswith("#cloud-config\n"))
        cloud_config = json.loads(rendered.split("\n", 1)[1])
        config = cloud_config["autoinstall"]
        self.assertEqual(config["version"], 1)
        self.assertEqual(config["interactive-sections"], [])
        self.assertEqual(config["source"], {"id": "ubuntu-desktop-minimal", "search_drivers": False})
        self.assertEqual(config["storage"]["layout"]["name"], "direct")
        self.assertEqual(config["shutdown"], "poweroff")
        self.assertEqual(config["identity"], {
            "realname": "Local User",
            "username": "desktopuser",
            "hostname": "ubuntu-ubuntudesk01",
            "password": VALID_PASSWORD_HASH,
        })
        self.assertEqual(config["ssh"], {"install-server": False, "allow-pw": False, "authorized-keys": []})
        self.assertNotIn("plaintext", rendered)

    def test_password_hashing_uses_openssl_stdin_without_secret_in_arguments(self):
        password = "LocalOnly-Test!2026"
        result = subprocess.CompletedProcess(["openssl"], 0, stdout=VALID_PASSWORD_HASH + "\n", stderr="")
        runner = Mock(return_value=result)
        rendered = hash_desktop_password(password, runner=runner)
        self.assertEqual(rendered, VALID_PASSWORD_HASH)
        args, kwargs = runner.call_args
        self.assertEqual(args[0], ["openssl", "passwd", "-6", "-stdin"])
        self.assertEqual(kwargs["input"], password + "\n")
        self.assertNotIn(password, " ".join(args[0]))
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["check"])

    def test_rejects_empty_or_short_password_without_calling_openssl(self):
        runner = Mock()
        for password in ("", "short"):
            with self.subTest(password_length=len(password)), self.assertRaises(DesktopAutoinstallError):
                hash_desktop_password(password, runner=runner)
        runner.assert_not_called()

    def test_password_hash_failure_is_sanitized(self):
        runner = Mock(side_effect=subprocess.CalledProcessError(1, ["openssl"], output="secret echoed"))
        with self.assertRaisesRegex(DesktopAutoinstallError, "could not create a password hash") as error:
            hash_desktop_password("LocalOnly-Test!2026", runner=runner)
        self.assertNotIn("secret echoed", str(error.exception))

    def test_desktop_metadata_is_stable_and_vm_specific(self):
        first = json.loads(build_desktop_meta_data("UbuntuDesk01"))
        repeated = json.loads(build_desktop_meta_data("UbuntuDesk01"))
        different = json.loads(build_desktop_meta_data("UbuntuDesk02"))
        self.assertEqual(first, repeated)
        self.assertNotEqual(first["instance-id"], different["instance-id"])
        self.assertEqual(first["local-hostname"], "ubuntu-ubuntudesk01")
        self.assertEqual(first["dsmode"], "local")

    def test_private_desktop_seed_image_contains_hash_but_not_plaintext(self):
        password = "LocalOnly-Test!2026"
        with tempfile.TemporaryDirectory() as directory:
            path = build_desktop_seed_image(
                Path(directory) / "cidata.img",
                vm_name="UbuntuDesk01",
                realname="Local User",
                username="desktopuser",
                password_hash=VALID_PASSWORD_HASH,
                source_id="ubuntu-desktop",
            )
            data = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn(b"CIDATA     ", data)
            self.assertIn(VALID_PASSWORD_HASH.encode(), data)
            self.assertNotIn(password.encode(), data)

    def test_rejects_unknown_source_id_and_malformed_hash(self):
        with self.assertRaises(DesktopAutoinstallError):
            self.build_config(source_id="ubuntu-desktop-unreviewed")
        with self.assertRaises(DesktopAutoinstallError):
            self.build_config(password_hash="plaintext")

    def test_all_valid_planner_vm_name_forms_render_safe_hostnames(self):
        from planning.preview import build_plan

        examples = (
            "DesktopOne",
            "desktop-with-hyphens",
            "desktop_with_underscores",
            "Desktop-trailing-",
            "A" + "b" * 62,
        )
        for name in examples:
            with self.subTest(name=name):
                plan = build_plan({
                    "profile": "ubuntu-desktop", "name": name,
                    "pool": "tank", "bridge": "br0",
                    "vcpus": 2, "memory_mib": 6144, "disk_gib": 32,
                })
                self.assertEqual(plan["vm"]["name"], name)
                config = json.loads(self.build_config(vm_name=name).split("\n", 1)[1])
                hostname = config["autoinstall"]["identity"]["hostname"]
                meta = json.loads(build_desktop_meta_data(name))
                self.assertEqual(hostname, meta["local-hostname"])
                self.assertLessEqual(len(hostname), 63)
                self.assertRegex(hostname, r"^[a-z][a-z0-9-]*[a-z0-9]$")
                self.assertNotIn("_", hostname)

    def test_punctuation_normalization_keeps_hostnames_distinct(self):
        dash = json.loads(build_desktop_meta_data("desktop-test"))["local-hostname"]
        underscore = json.loads(build_desktop_meta_data("desktop_test"))["local-hostname"]
        self.assertNotEqual(dash, underscore)

    def test_ordinary_short_name_preserves_existing_hostname(self):
        data = json.loads(self.build_config(vm_name="UbuntuDesk01").split("\n", 1)[1])
        self.assertEqual(data["autoinstall"]["identity"]["hostname"], "ubuntu-ubuntudesk01")

    def test_rejects_unsafe_identity_inputs(self):
        for overrides in (
            {"vm_name": "bad/name"},
            {"username": "DesktopUser"},
            {"realname": "  "},
            {"realname": "bad\nname"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(DesktopAutoinstallError):
                self.build_config(**overrides)


if __name__ == "__main__":
    unittest.main()
