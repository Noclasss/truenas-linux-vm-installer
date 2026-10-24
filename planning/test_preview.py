import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning.preview import PROFILES, ValidationError, build_plan, collect_interactive_config, main, read_json


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(profile="ubuntu-server", name="test-vm", pool="tank",
                           bridge="br0", vcpus=4, memory_mib=4096, disk_gib=32)
        self.inventory = dict(vm_names=["home-assistant"], datasets=["tank", "tank/linux-vms"],
                              bridges=["br0", "br1"], pools={"bulk": {"available_bytes": 200 * 1024**3},
                                                              "tank": {"available_bytes": 100 * 1024**3}})

    def test_interactive_menu_offers_every_profile_and_resource_choice(self):
        for profile_index, profile in enumerate(PROFILES, start=1):
            with self.subTest(profile=profile):
                answers = iter([str(profile_index), "menu-vm", "2", "1", "4", "4", "3"])
                output = io.StringIO()
                config = collect_interactive_config(
                    self.inventory,
                    input_fn=lambda _prompt: next(answers),
                    output=output,
                )
                self.assertEqual(config["profile"], profile)
                self.assertEqual(config["name"], "menu-vm")
                self.assertEqual(config["pool"], "tank")
                self.assertEqual(config["bridge"], "br0")
                self.assertEqual(config["vcpus"], 8)
                self.assertEqual(config["memory_mib"], 8192)
                self.assertEqual(config["disk_gib"], 64)
                plan = build_plan(config, self.inventory)
                self.assertFalse(plan["apply_supported"])
                self.assertFalse(plan["vm"]["start"])
                self.assertFalse(plan["vm"]["autostart"])
                self.assertIn("storage pool", output.getvalue())
                self.assertIn("not an individual physical drive", output.getvalue())
                self.assertIn("existing network bridge", output.getvalue())
                self.assertIn("LAN attachment", output.getvalue())
                self.assertIn("bulk", output.getvalue())
                self.assertIn("br1", output.getvalue())
                self.assertIn("Custom value", output.getvalue())
                self.assertIn("8 GiB (8192 MiB)", output.getvalue())

    def test_interactive_profile_flag_skips_profile_menu(self):
        answers = iter(["cli-profile-vm", "1", "2", "3", "2", "2"])
        config = collect_interactive_config(
            self.inventory,
            profile_override="debian-desktop",
            input_fn=lambda _prompt: next(answers),
            output=io.StringIO(),
        )
        self.assertEqual(config["profile"], "debian-desktop")
        self.assertEqual(config["pool"], "bulk")
        self.assertEqual(config["bridge"], "br1")

    def test_interactive_pool_menu_excludes_boot_pool(self):
        inventory = json.loads(json.dumps(self.inventory))
        inventory["pools"]["boot-pool"] = {"available_bytes": 2**40}
        answers = iter(["1", "safe-vm", "1", "1", "2", "3", "2"])
        output = io.StringIO()
        config = collect_interactive_config(
            inventory,
            input_fn=lambda _prompt: next(answers),
            output=output,
        )
        self.assertEqual(config["pool"], "bulk")
        self.assertNotIn("boot-pool", output.getvalue())

    def test_interactive_without_inventory_asks_for_pool_and_bridge(self):
        answers = iter(["1", "manual-vm", "tank", "br0", "2", "3", "2"])
        output = io.StringIO()
        config = collect_interactive_config(
            None,
            input_fn=lambda _prompt: next(answers),
            output=output,
        )
        plan = build_plan(config)
        self.assertEqual(config["pool"], "tank")
        self.assertEqual(config["bridge"], "br0")
        self.assertFalse(plan["inventory_checked"])
        self.assertTrue(any("unchecked" in warning for warning in plan["warnings"]))
        self.assertIn("no live query occurs", output.getvalue())

    def test_resource_menus_accept_defaults_and_custom_values(self):
        defaults = iter(["1", "default-vm", "tank", "br0", "", "", ""])
        output = io.StringIO()
        config = collect_interactive_config(None, input_fn=lambda _prompt: next(defaults), output=output)
        self.assertEqual((config["vcpus"], config["memory_mib"], config["disk_gib"]), (2, 4096, 32))
        self.assertIn("2 cores (default)", output.getvalue())
        self.assertIn("4 GiB (4096 MiB) (default)", output.getvalue())
        self.assertIn("32 GiB (default)", output.getvalue())

        custom = iter(["1", "custom-vm", "tank", "br0", "6", "24", "6", "6144", "6", "96"])
        config = collect_interactive_config(None, input_fn=lambda _prompt: next(custom), output=io.StringIO())
        self.assertEqual((config["vcpus"], config["memory_mib"], config["disk_gib"]), (24, 6144, 96))

    def test_interactive_main_emits_json_plan_without_apply(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("planning.preview.sys.stdin", io.StringIO("1\ncli-vm\ntank\nbr0\n2\n3\n2\n")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["--interactive"]), 0)
        plan = json.loads(stdout.getvalue())
        self.assertEqual(plan["mode"], "offline-preview")
        self.assertFalse(plan["apply_supported"])
        self.assertIn("Ubuntu Server", stderr.getvalue())
        self.assertIn("Existing network bridge", stderr.getvalue())

    def test_interactive_profile_cli_override_skips_menu(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        answers = io.StringIO("cli-profile-vm\ntank\nbr0\n2\n3\n2\n")
        with patch("planning.preview.sys.stdin", answers):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["--interactive", "--profile", "debian-server"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["profile"]["id"], "debian-server")
        self.assertNotIn("guest profile", stderr.getvalue())

    def test_interactive_main_eof_returns_without_plan(self):
        output, error = io.StringIO(), io.StringIO()
        def interrupted(_prompt):
            raise EOFError
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(main(["--interactive"], input_fn=interrupted), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("no plan produced", error.getvalue())

    def test_all_profiles_remain_experimental(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan = build_plan(dict(self.config, profile=profile))
                self.assertEqual(plan["profile"]["status"], "experimental")
                self.assertFalse(plan["apply_supported"])

    def test_ubuntu_server_preview_is_static_and_experimental(self):
        with patch("planning.ubuntu_server.download_ubuntu_server_image", side_effect=AssertionError("preview must not download")), patch(
            "planning.ubuntu_server.build_seed_iso", side_effect=AssertionError("preview must not build media")
        ):
            plan = build_plan(self.config)
        self.assertFalse(plan["apply_supported"])
        self.assertEqual(plan["profile"]["status"], "experimental")
        bootstrap = plan["guest_bootstrap"]
        self.assertEqual(bootstrap["artifact"]["url"], "https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/ubuntu-26.04-server-cloudimg-amd64.img")
        self.assertEqual(bootstrap["artifact"]["sha256"], "5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5")
        self.assertEqual(bootstrap["artifact"]["size_bytes"], 859_090_944)
        self.assertEqual(bootstrap["artifact"]["image_format"], "qcow2")
        self.assertEqual(bootstrap["artifact"]["image_virtual_size_bytes"], 3_758_096_384)
        self.assertIn("verified with qemu-img", bootstrap["artifact"]["image_format_status"])
        self.assertEqual(bootstrap["provisioning"]["method"], "cloud-init NoCloud")
        self.assertEqual(bootstrap["provisioning"]["seed_builder"], "cloud-localds")
        self.assertEqual(bootstrap["provisioning"]["media_label"], "CIDATA")
        self.assertEqual(bootstrap["provisioning"]["datasource_mode"], "local")
        self.assertEqual(bootstrap["provisioning"]["device_type"], "CDROM")
        self.assertIn("schema-only", bootstrap["provisioning"]["device_evidence"])

    def test_non_ubuntu_server_profiles_have_no_ubuntu_artifact(self):
        for profile in PROFILES:
            if profile == "ubuntu-server":
                continue
            with self.subTest(profile=profile):
                plan = build_plan(dict(self.config, profile=profile))
                self.assertNotIn("guest_bootstrap", plan)

    def test_cpu_topology_equals_requested_total(self):
        cpu = build_plan(self.config)["vm"]["cpu"]
        self.assertEqual(cpu["sockets"] * cpu["cores"] * cpu["threads"], 4)

    def test_vm_off_network_unchanged(self):
        plan = build_plan(self.config)
        self.assertIs(plan["vm"]["start"], False)
        self.assertIs(plan["vm"]["autostart"], False)
        self.assertIs(plan["network"]["modify_host_network"], False)
        self.assertIn("not configured", plan["network"]["guest_ip_configuration"])

    def test_no_inventory_is_not_claimed_checked(self):
        plan = build_plan(self.config)
        self.assertFalse(plan["inventory_checked"])
        self.assertTrue(any("unchecked" in w for w in plan["warnings"]))

    def test_inventory_success_does_not_authorize_apply(self):
        plan = build_plan(self.config, self.inventory)
        self.assertTrue(plan["inventory_checked"])
        self.assertFalse(plan["apply_supported"])
        self.assertTrue(any("stale" in w for w in plan["warnings"]))

    def test_inputs_unchanged(self):
        before = copy.deepcopy((self.config, self.inventory))
        build_plan(self.config, self.inventory)
        self.assertEqual(before, (self.config, self.inventory))

    def test_paths_and_units(self):
        storage = build_plan(self.config)["storage"]
        self.assertEqual(storage["disk"], "tank/linux-vms/test-vm/disk0")
        self.assertEqual(storage["disk_bytes"], 34359738368)

    def test_reject_invalid_components(self):
        for field in ("name", "pool", "bridge"):
            for value in ("../x", "/tmp/x", "a/b", "a;id", "a\n", "a b", "-x", "a" * 64, "", None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        build_plan(dict(self.config, **{field: value}))

    def test_reject_boot_pool(self):
        for pool in ("boot-pool", "freenas-boot", "BOOT-POOL"):
            with self.assertRaises(ValidationError):
                build_plan(dict(self.config, pool=pool))

    def test_reject_unknown_profile(self):
        for profile in ("windows", [], None):
            with self.assertRaises(ValidationError):
                build_plan(dict(self.config, profile=profile))

    def test_reject_invalid_numbers(self):
        for field in ("vcpus", "memory_mib", "disk_gib"):
            for value in (True, False, "4", 1.5, 0, -1, 2**64, None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        build_plan(dict(self.config, **{field: value}))

    def test_reject_unknown_and_missing_config_fields(self):
        for config in ({}, [], dict(self.config, start=True), dict(self.config, password="example")):
            with self.assertRaises(ValidationError):
                build_plan(config)

    def test_reject_existing_vm_case_insensitively(self):
        self.inventory["vm_names"].append("TEST-VM")
        with self.assertRaises(ValidationError):
            build_plan(self.config, self.inventory)

    def test_reject_existing_dataset_or_descendant(self):
        for path in ("tank/linux-vms/test-vm", "tank/linux-vms/test-vm/disk0"):
            inventory = dict(self.inventory, datasets=[path])
            with self.assertRaises(ValidationError):
                build_plan(self.config, inventory)

    def test_sibling_with_shared_prefix_is_not_collision(self):
        self.inventory["datasets"].append("tank/linux-vms/test-vm-other")
        build_plan(self.config, self.inventory)

    def test_reject_missing_pool(self):
        self.inventory["pools"] = {}
        with self.assertRaises(ValidationError):
            build_plan(self.config, self.inventory)

    def test_reject_missing_bridge(self):
        self.inventory["bridges"] = []
        with self.assertRaises(ValidationError):
            build_plan(self.config, self.inventory)

    def test_reject_insufficient_capacity(self):
        self.inventory["pools"]["tank"]["available_bytes"] = 32 * 1024**3 - 1
        with self.assertRaises(ValidationError):
            build_plan(self.config, self.inventory)

    def test_inventory_malformed_is_not_ignored(self):
        for inventory in ({}, [], dict(self.inventory, pools=[]), dict(self.inventory, vm_names="test"),
                          dict(self.inventory, vm_names=[None]), dict(self.inventory, bridges=["br0", "br0"]),
                          dict(self.inventory, pools={"tank": {"available_bytes": True}})):
            with self.subTest(inventory=inventory):
                with self.assertRaises(ValidationError):
                    build_plan(self.config, inventory)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"name":"safe","name":"other"}', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "duplicate JSON key"):
                read_json(path)

    def test_invalid_json_does_not_echo_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text("sensitive-example", encoding="utf-8")
            with self.assertRaises(ValidationError) as error:
                read_json(path)
            self.assertNotIn("sensitive-example", str(error.exception))

    def test_cli_success_writes_json_only(self):
        with patch("planning.preview.read_json", return_value=self.config):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["--config", "example.json"]), 0)
            self.assertEqual(json.loads(output.getvalue())["mode"], "offline-preview")

    def test_cli_profile_option_overrides_config(self):
        output = io.StringIO()
        with patch("planning.preview.read_json", return_value=self.config), contextlib.redirect_stdout(output):
            self.assertEqual(main(["--config", "example.json", "--profile", "debian-server"]), 0)
        self.assertEqual(json.loads(output.getvalue())["profile"]["id"], "debian-server")

    def test_cli_invalid_input_nonzero_no_plan(self):
        output, error = io.StringIO(), io.StringIO()
        with patch("planning.preview.read_json", return_value={}):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(main(["--config", "bad.json"]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Invalid input", error.getvalue())

    def test_no_apply_flag(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--config", "example.json", "--apply"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
