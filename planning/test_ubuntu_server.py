import base64
import json
import os
import stat
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from planning.ubuntu_server import (
    ARTIFACT_MAX_BYTES,
    ARTIFACT_SHA256,
    ARTIFACT_SIZE_BYTES,
    ARTIFACT_URL,
    PROFILE_STATUS,
    ProfileValidationError,
    SeedImageError,
    artifact_metadata,
    build_cloud_config,
    build_meta_data,
    build_seed_iso,
    download_ubuntu_server_image,
    stage_ubuntu_server_raw_image,
    ubuntu_server_dataset_stage_paths,
    ubuntu_server_stage_paths_from_dataset_rows,
)
from planning.installer_core.artifacts import ArtifactError


def dummy_public_key(key_type="ssh-ed25519"):
    """Build a structurally plausible but nonfunctional OpenSSH public key."""
    blob = struct.pack(">I", len(key_type)) + key_type.encode("ascii") + b"test-key-material"
    return f"{key_type} {base64.b64encode(blob).decode('ascii')} test-only"


def mapping_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from mapping_keys(child)


class UbuntuServerProfileTests(unittest.TestCase):
    def test_image_pin_and_verified_qcow2_metadata(self):
        metadata = artifact_metadata()
        self.assertEqual(PROFILE_STATUS, "experimental")
        self.assertEqual(metadata["url"], ARTIFACT_URL)
        self.assertIn("release-20261016", ARTIFACT_URL)
        self.assertEqual(metadata["sha256"], ARTIFACT_SHA256)
        self.assertEqual(metadata["size_bytes"], ARTIFACT_SIZE_BYTES)
        self.assertEqual(ARTIFACT_MAX_BYTES, ARTIFACT_SIZE_BYTES)
        self.assertEqual(metadata["image_format"], "qcow2")
        self.assertEqual(metadata["image_virtual_size_bytes"], 3_758_096_384)
        self.assertIn("verified with qemu-img", str(metadata["image_format_status"]))
        self.assertEqual(ARTIFACT_URL, "https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/ubuntu-26.04-server-cloudimg-amd64.img")
        self.assertEqual(ARTIFACT_SHA256, "5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5")
        self.assertEqual(ARTIFACT_SIZE_BYTES, 859_090_944)
        self.assertEqual(len(ARTIFACT_SHA256), 64)

    def test_download_wrapper_uses_static_pin_without_network_in_test(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "verified.img"
            with patch("planning.ubuntu_server.download_and_verify", return_value=expected) as download:
                result = download_ubuntu_server_image(Path(directory) / "cache")
        self.assertEqual(result, expected)
        download.assert_called_once_with(
            url=ARTIFACT_URL,
            sha256=ARTIFACT_SHA256,
            cache_dir=Path(directory) / "cache",
            max_bytes=ARTIFACT_MAX_BYTES,
            expected_size=ARTIFACT_SIZE_BYTES,
        )

    def test_user_data_locks_password_and_uses_only_authorized_public_keys(self):
        key = dummy_public_key()
        rendered = build_cloud_config("alice", [key])
        self.assertTrue(rendered.startswith("#cloud-config\n"))
        config = json.loads(rendered.split("\n", 1)[1])
        self.assertTrue(config["disable_root"])
        self.assertFalse(config["ssh_pwauth"])
        user = next(value for value in config["users"] if isinstance(value, dict))
        self.assertEqual(user["name"], "alice")
        self.assertTrue(user["lock_passwd"])
        self.assertEqual(user["ssh_authorized_keys"], [key])
        self.assertIn("sudo", user["groups"])
        self.assertEqual(user["sudo"], "ALL=(ALL) NOPASSWD:ALL")
        forbidden = {"password", "passwd", "chpasswd", "ssh_private_key", "ssh_private_keys"}
        self.assertFalse(forbidden.intersection(mapping_keys(config)))
        self.assertNotIn("PRIVATE KEY", rendered)

    def test_user_data_rejects_unsafe_usernames_and_key_material(self):
        key = dummy_public_key()
        for username in ("root", "ubuntu", "Alice", "-bad", "bad;name", "a" * 33, ""):
            with self.subTest(username=username), self.assertRaises(ProfileValidationError):
                build_cloud_config(username, [key])
        for invalid in (
            "ssh-ed25519 not-base64",
            "ssh-ed25519 YWJj",
            "ssh-rsa AAAA",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            key + "\nssh-ed25519 AAAA",
            " " + key,
            "ssh-ed25519 " + base64.b64encode(b"wrong key type").decode("ascii"),
        ):
            with self.subTest(key=invalid), self.assertRaises(ProfileValidationError):
                build_cloud_config("alice", [invalid])
        with self.assertRaises(ProfileValidationError):
            build_cloud_config("alice", [])
        with self.assertRaises(ProfileValidationError):
            build_cloud_config("alice", [key, key])

    def test_meta_data_is_deterministic_and_safe(self):
        first = build_meta_data("My_VM-1")
        second = build_meta_data("My_VM-1")
        self.assertEqual(first, second)
        metadata = json.loads(first)
        self.assertEqual(metadata["dsmode"], "local")
        self.assertTrue(metadata["instance-id"].startswith("truenas-"))
        self.assertEqual(metadata["local-hostname"], "my-vm-1")
        with self.assertRaises(ProfileValidationError):
            build_meta_data("../unsafe")

    def test_seed_builder_uses_private_files_and_exact_cloud_localds_args(self):
        key = dummy_public_key()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed.iso"
            observed = {}

            def fake_runner(args, **kwargs):
                observed["args"] = args
                observed["kwargs"] = kwargs
                seed_path = Path(args[5])
                user_data_path = Path(args[6])
                meta_data_path = Path(args[7])
                observed["user_data"] = user_data_path.read_text(encoding="utf-8")
                observed["meta_data"] = meta_data_path.read_text(encoding="utf-8")
                observed["user_mode"] = stat.S_IMODE(user_data_path.stat().st_mode)
                observed["meta_mode"] = stat.S_IMODE(meta_data_path.stat().st_mode)
                observed["stage_mode"] = stat.S_IMODE(user_data_path.parent.stat().st_mode)
                seed_path.write_bytes(b"mocked-iso-image")
                return subprocess.CompletedProcess(args, 0)

            result = build_seed_iso(
                output,
                vm_name="test-vm",
                username="alice",
                ssh_authorized_keys=[key],
                runner=fake_runner,
            )
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"mocked-iso-image")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

        self.assertEqual(
            observed["args"][1:5],
            ["--disk-format", "raw", "--filesystem", "iso"],
        )
        self.assertEqual(observed["args"][0], "cloud-localds")
        self.assertEqual(observed["user_mode"], 0o600)
        self.assertEqual(observed["meta_mode"], 0o600)
        self.assertEqual(observed["stage_mode"], 0o700)
        self.assertEqual(observed["kwargs"]["timeout"], 60)
        self.assertTrue(observed["kwargs"]["check"])
        self.assertTrue(observed["kwargs"]["capture_output"])
        self.assertEqual(observed["kwargs"]["env"]["TMPDIR"], observed["args"][6].rsplit("/", 1)[0])
        self.assertIn("#cloud-config", observed["user_data"])
        self.assertEqual(json.loads(observed["meta_data"])["local-hostname"], "test-vm")

    def test_seed_builder_preserves_existing_output_directory_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "published"
            output_dir.mkdir(mode=0o755)
            output_dir.chmod(0o755)
            output = output_dir / "seed.iso"

            def fake_runner(args, **kwargs):
                Path(args[5]).write_bytes(b"mocked-iso-image")
                return subprocess.CompletedProcess(args, 0)

            result = build_seed_iso(
                output,
                vm_name="test-vm",
                username="alice",
                ssh_authorized_keys=[dummy_public_key()],
                runner=fake_runner,
            )
            self.assertEqual(result, output)
            self.assertEqual(stat.S_IMODE(output_dir.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_seed_builder_refuses_overwrite_before_running_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed.iso"
            output.write_bytes(b"keep")
            runner = Mock()
            with self.assertRaises(SeedImageError):
                build_seed_iso(output, "test-vm", "alice", [dummy_public_key()], runner=runner)
            runner.assert_not_called()
            self.assertEqual(output.read_bytes(), b"keep")

    def test_seed_builder_suppresses_tool_error_output_and_cleans_staging(self):
        key = dummy_public_key()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed.iso"
            leaked = "TEST-ONLY-PUBLIC-KEY-DETAIL"

            def failed_runner(args, **kwargs):
                Path(args[5]).write_bytes(b"partial")
                raise subprocess.CalledProcessError(1, args, stderr=leaked)

            with self.assertRaises(SeedImageError) as error:
                build_seed_iso(
                    output,
                    "test-vm",
                    "alice",
                    [key],
                    runner=failed_runner,
                )
            self.assertNotIn(leaked, str(error.exception))
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_seed_builder_times_out_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed.iso"
            def timeout_runner(args, **kwargs):
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            with self.assertRaisesRegex(SeedImageError, "timed out"):
                build_seed_iso(output, "test-vm", "alice", [dummy_public_key()], runner=timeout_runner)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_seed_builder_rejects_missing_or_invalid_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for behavior in ("missing", "empty", "symlink"):
                output = Path(directory) / f"{behavior}.iso"
                def runner(args, **kwargs):
                    path = Path(args[5])
                    if behavior == "empty":
                        path.write_bytes(b"")
                    elif behavior == "symlink":
                        path.symlink_to(Path(directory) / "outside")
                    return subprocess.CompletedProcess(args, 0)
                with self.subTest(behavior=behavior), self.assertRaises(SeedImageError):
                    build_seed_iso(output, "test-vm", "alice", [dummy_public_key()], runner=runner)
                self.assertFalse(output.exists())

    def test_seed_builder_handles_missing_tool_without_leaking_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "seed.iso"
            def missing_tool(*args, **kwargs):
                raise FileNotFoundError("cloud-localds not installed")
            with self.assertRaises(SeedImageError) as error:
                build_seed_iso(output, "test-vm", "alice", [dummy_public_key()], runner=missing_tool)
            self.assertNotIn("not installed", str(error.exception))
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_raw_staging_requires_raw_suffix_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            with patch("planning.ubuntu_server.download_ubuntu_server_image") as download:
                for name in ("disk.img", "disk.RAW"):
                    with self.subTest(name=name), self.assertRaisesRegex(
                        ProfileValidationError, r"lowercase \.raw"
                    ):
                        stage_ubuntu_server_raw_image(cache, root / name)
            download.assert_not_called()
            self.assertFalse(cache.exists())

    def test_dataset_stage_paths_are_fixed_under_canonical_reported_mountpoint(self):
        paths = ubuntu_server_dataset_stage_paths("/mnt/NVME2/linux-vms/MockUbuntu/")
        self.assertEqual(paths, {
            "image": Path("/mnt/NVME2/linux-vms/MockUbuntu/ubuntu-server.raw"),
            "seed": Path("/mnt/NVME2/linux-vms/MockUbuntu/cidata.iso"),
        })
        for mountpoint in (
            "/mnt", "/mnt/", "/mnt/NVME2", "/mnt/../tmp", "/mnt/pool/../other",
            "/mnt//pool/data", "relative/path", "/tmp/pool/data",
            "/mnt/pool\\data", "/mnt/pool\ndata",
        ):
            with self.subTest(mountpoint=mountpoint), self.assertRaises(ProfileValidationError):
                ubuntu_server_dataset_stage_paths(mountpoint)

    def test_stage_paths_require_one_exact_filesystem_dataset_query_row(self):
        dataset_id = "pool/linux-vms/MockUbuntu"
        rows = [{"id": dataset_id, "type": "FILESYSTEM", "mountpoint": "/mnt/pool/linux-vms/MockUbuntu"}]
        self.assertEqual(
            ubuntu_server_stage_paths_from_dataset_rows(rows, dataset_id),
            {
                "image": Path("/mnt/pool/linux-vms/MockUbuntu/ubuntu-server.raw"),
                "seed": Path("/mnt/pool/linux-vms/MockUbuntu/cidata.iso"),
            },
        )
        invalid = (
            ([], dataset_id),
            (rows + rows, dataset_id),
            ([{"id": dataset_id, "type": "VOLUME", "mountpoint": "/mnt/pool/linux-vms/MockUbuntu"}], dataset_id),
            ([{"id": dataset_id, "type": "FILESYSTEM", "mountpoint": None}], dataset_id),
            ([{"id": dataset_id, "type": "FILESYSTEM", "mountpoint": "/mnt/otherpool/linux-vms/MockUbuntu"}], dataset_id),
            ([{"id": dataset_id, "type": "FILESYSTEM", "mountpoint": "/mnt/pool-extra/linux-vms/MockUbuntu"}], dataset_id),
            (rows, "pool"),
            (rows, "pool/../MockUbuntu"),
            (rows, "pool/linux-vms/OtherVM"),
        )
        for inventory, selected_id in invalid:
            with self.subTest(inventory=inventory, selected_id=selected_id), self.assertRaises(ProfileValidationError):
                ubuntu_server_stage_paths_from_dataset_rows(inventory, selected_id)

    def test_offline_raw_staging_success_and_exact_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "verified.qcow2"
            source.write_bytes(b"verified")
            output = root / "disk.raw"
            calls = []
            runner_kwargs = []
            def runner(args, **kwargs):
                calls.append(args)
                runner_kwargs.append(kwargs)
                if args[1] == "convert":
                    Path(args[-1]).write_bytes(b"raw-data")
                    data = {"format": "raw", "virtual-size": 3_758_096_384}
                elif "-f" in args and args[args.index("-f") + 1] == "raw":
                    data = {"format": "raw", "virtual-size": 3_758_096_384}
                else:
                    data = {"format": "qcow2", "virtual-size": 3_758_096_384, "dirty-flag": False}
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(data))
            with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source) as download:
                result = stage_ubuntu_server_raw_image(root / "cache", output, runner=runner)
            download.assert_called_once_with(root / "cache")
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"raw-data")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(isinstance(call, list) for call in calls))
            self.assertEqual(calls[1][1:4], ["convert", "-n", "-f"])
            self.assertTrue(all(kwargs["check"] and kwargs["capture_output"] for kwargs in runner_kwargs))
            self.assertTrue(all(kwargs["timeout"] == 300 for kwargs in runner_kwargs))
            self.assertTrue(all("shell" not in call for call in calls))
            self.assertTrue(all(not list(root.glob(".ubuntu-raw-*.tmp")) for _ in [0]))

    def test_raw_staging_preserves_existing_output_directory_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "published"
            output_dir.mkdir(mode=0o755)
            output_dir.chmod(0o755)
            source = root / "source"
            source.write_bytes(b"verified")

            def runner(args, **kwargs):
                if args[1] == "convert":
                    Path(args[-1]).write_bytes(b"raw-data")
                    data = {"format": "raw", "virtual-size": 3_758_096_384}
                elif "-f" in args and args[args.index("-f") + 1] == "raw":
                    data = {"format": "raw", "virtual-size": 3_758_096_384}
                else:
                    data = {"format": "qcow2", "virtual-size": 3_758_096_384, "dirty-flag": False}
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(data))

            original_mode = stat.S_IMODE(output_dir.stat().st_mode)
            with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source):
                result = stage_ubuntu_server_raw_image(
                    root / "cache", output_dir / "disk.raw", runner=runner
                )
            self.assertTrue(result.is_file())
            self.assertEqual(stat.S_IMODE(output_dir.stat().st_mode), original_mode)
            self.assertEqual(original_mode, 0o755)

    def test_raw_staging_rejects_corrupt_or_mismatched_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "planning.ubuntu_server.download_and_verify",
                side_effect=ArtifactError("downloaded artifact size or digest mismatch"),
            ) as verifier:
                with self.assertRaises(ArtifactError):
                    download_ubuntu_server_image(root / "cache")
            verifier.assert_called_once_with(
                url=ARTIFACT_URL,
                sha256=ARTIFACT_SHA256,
                cache_dir=root / "cache",
                max_bytes=ARTIFACT_MAX_BYTES,
                expected_size=ARTIFACT_SIZE_BYTES,
            )
            source = root / "verified-qcow2"
            source.write_bytes(b"verified")
            runner = Mock(return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps({"format": "vmdk", "virtual-size": 3_758_096_384})
            ))
            with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source):
                with self.assertRaises(ArtifactError):
                    stage_ubuntu_server_raw_image(root / "cache", root / "out.raw", runner=runner)
            runner.assert_called_once()

    def test_raw_staging_rejects_unexpected_source_and_output_metadata(self):
        cases = [
            {"format": "raw", "virtual-size": 3_758_096_384},
            {"format": "qcow2", "virtual-size": 99, "backing-filename": "parent.qcow2"},
            {"format": "qcow2", "virtual-size": 3_758_096_384, "dirty-flag": True},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                source.write_bytes(b"verified")
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(metadata)))
                with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source):
                    with self.assertRaises(ArtifactError):
                        stage_ubuntu_server_raw_image(root / "cache", root / "out.raw", runner=runner)
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(list(root.glob(".ubuntu-raw-*.tmp")), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"verified")
            def bad_raw_info(args, **kwargs):
                if args[1] == "convert":
                    Path(args[-1]).write_bytes(b"raw")
                    result = {"format": "raw", "virtual-size": 10}
                elif "-f" in args:
                    result = {"format": "raw", "virtual-size": 10}
                else:
                    result = {"format": "qcow2", "virtual-size": 3_758_096_384, "dirty-flag": False}
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result))
            with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source):
                with self.assertRaises(ArtifactError):
                    stage_ubuntu_server_raw_image(root / "cache", root / "out.raw", runner=bad_raw_info)
            self.assertFalse((root / "out.raw").exists())
            self.assertEqual(list(root.glob(".ubuntu-raw-*.tmp")), [])

    def test_raw_staging_refuses_existing_output_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out.raw"
            output.write_bytes(b"keep")
            with patch("planning.ubuntu_server.download_ubuntu_server_image") as download:
                with self.assertRaises(ArtifactError):
                    stage_ubuntu_server_raw_image(root / "cache", output)
                download.assert_not_called()
            self.assertEqual(output.read_bytes(), b"keep")

    def test_raw_staging_cleans_up_after_subprocess_failure_and_timeout(self):
        for failure in (subprocess.CalledProcessError(1, ["qemu-img"]), subprocess.TimeoutExpired(["qemu-img"], 1)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                source.write_bytes(b"verified")
                def runner(args, **kwargs):
                    if args[1] == "info":
                        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"format":"qcow2", "virtual-size":3_758_096_384}))
                    raise failure
                with patch("planning.ubuntu_server.download_ubuntu_server_image", return_value=source):
                    with self.assertRaises(ArtifactError):
                        stage_ubuntu_server_raw_image(root / "cache", root / "out.raw", runner=runner)
                self.assertFalse((root / "out.raw").exists())
                self.assertEqual(list(root.glob(".ubuntu-raw-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
