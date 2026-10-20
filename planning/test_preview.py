import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning.preview import PROFILES, ValidationError, build_plan, main, read_json


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(profile="ubuntu-server", name="test-vm", pool="tank",
                           bridge="br0", vcpus=4, memory_mib=4096, disk_gib=32)
        self.inventory = dict(vm_names=["home-assistant"], datasets=["tank", "tank/linux-vms"],
                              bridges=["br0"], pools={"tank": {"available_bytes": 100 * 1024**3}})

    def test_all_profiles_remain_experimental(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                plan = build_plan(dict(self.config, profile=profile))
                self.assertEqual(plan["profile"]["status"], "experimental")
                self.assertFalse(plan["apply_supported"])

    def test_cpu_topology_equals_requested_total(self):
        cpu = build_plan(self.config)["vm"]["cpu"]
        self.assertEqual(cpu["sockets"] * cpu["cores"] * cpu["threads"], 4)

    def test_vm_off_network_unchanged(self):
        plan = build_plan(self.config)
        self.assertIs(plan["vm"]["start"], False)
        self.assertIs(plan["vm"]["autostart"], False)
        self.assertIs(plan["network"]["modify_host_network"], False)

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
