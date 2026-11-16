"""Offline planner capacity regression tests; no host or network I/O."""
import unittest

from planning.preview import ValidationError, build_plan
from planning.ubuntu_desktop import STAGING_RESERVE_BYTES

GIB = 1024 ** 3


class DesktopStagingCapacityTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "profile": "ubuntu-desktop",
            "name": "desktop-capacity-test",
            "pool": "tank",
            "bridge": "br0",
            "vcpus": 2,
            "memory_mib": 6144,
            "disk_gib": 32,
        }
        self.inventory = {
            "vm_names": [],
            "datasets": ["tank"],
            "bridges": ["br0"],
            "pools": {"tank": {"available_bytes": 200 * GIB}},
        }

    def test_desktop_plan_exposes_full_staging_requirement(self):
        plan = build_plan(self.config)
        storage = plan["storage"]
        self.assertEqual(storage["disk_bytes"], 32 * GIB)
        self.assertEqual(storage["staging_reserve_bytes"], STAGING_RESERVE_BYTES)
        self.assertEqual(storage["minimum_required_free_bytes"], 32 * GIB + STAGING_RESERVE_BYTES)
        self.assertFalse(plan["apply_supported"])

    def test_capacity_exact_threshold_passes_offline(self):
        minimum = 32 * GIB + STAGING_RESERVE_BYTES
        self.inventory["pools"]["tank"]["available_bytes"] = minimum
        plan = build_plan(self.config, self.inventory)
        self.assertTrue(plan["inventory_checked"])
        self.assertEqual(plan["storage"]["minimum_required_free_bytes"], minimum)

    def test_capacity_one_byte_below_staging_requirement_fails(self):
        self.inventory["pools"]["tank"]["available_bytes"] = 32 * GIB + STAGING_RESERVE_BYTES - 1
        with self.assertRaisesRegex(ValidationError, "artifact/seed staging"):
            build_plan(self.config, self.inventory)

    def test_disk_only_capacity_is_insufficient_for_desktop(self):
        self.inventory["pools"]["tank"]["available_bytes"] = 32 * GIB
        with self.assertRaises(ValidationError):
            build_plan(self.config, self.inventory)

    def test_server_capacity_rules_remain_unchanged(self):
        self.config["profile"] = "ubuntu-server"
        self.config["memory_mib"] = 4096
        self.inventory["pools"]["tank"]["available_bytes"] = 32 * GIB
        plan = build_plan(self.config, self.inventory)
        self.assertEqual(plan["storage"]["staging_reserve_bytes"], 0)
        self.assertEqual(plan["storage"]["minimum_required_free_bytes"], 32 * GIB)

    def test_validation_still_rejects_name_collision(self):
        self.inventory["vm_names"].append(self.config["name"])
        with self.assertRaisesRegex(ValidationError, "already exists"):
            build_plan(self.config, self.inventory)


if __name__ == "__main__":
    unittest.main()
