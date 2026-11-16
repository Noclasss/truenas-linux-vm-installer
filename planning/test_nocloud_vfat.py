import tempfile
import unittest
from pathlib import Path

from planning.nocloud_vfat import (
    SEED_MEDIA_SIZE_BYTES,
    NoCloudImageError,
    build_nocloud_vfat_image,
    write_nocloud_vfat_image,
)


class NoCloudVfatTests(unittest.TestCase):
    USER_DATA = "#cloud-config\n{\"autoinstall\": {\"version\": 1}}\n"
    META_DATA = "{\"dsmode\":\"local\",\"instance-id\":\"iid-test\"}\n"

    def extract_files(self, image):
        self.assertEqual(len(image), SEED_MEDIA_SIZE_BYTES)
        self.assertEqual(image[43:54], b"CIDATA     ")
        self.assertEqual(image[54:62], b"FAT12   ")
        bytes_per_sector = int.from_bytes(image[11:13], "little")
        sectors_per_cluster = image[13]
        reserved = int.from_bytes(image[14:16], "little")
        fat_count = image[16]
        root_entries = int.from_bytes(image[17:19], "little")
        sectors_per_fat = int.from_bytes(image[22:24], "little")
        self.assertEqual((bytes_per_sector, sectors_per_cluster, reserved, fat_count, root_entries, sectors_per_fat), (512, 1, 1, 2, 128, 12))
        fat_start = reserved * bytes_per_sector
        fat_size = sectors_per_fat * bytes_per_sector
        fat1 = image[fat_start:fat_start + fat_size]
        fat2_start = fat_start + fat_size
        self.assertEqual(fat1, image[fat2_start:fat2_start + fat_size])
        root_start = (reserved + fat_count * sectors_per_fat) * bytes_per_sector
        root = image[root_start:root_start + root_entries * 32]
        data_start = root_start + root_entries * 32
        files = {}
        pending_name = None
        for offset in range(0, len(root), 32):
            entry = root[offset:offset + 32]
            if entry[0] == 0:
                break
            if entry[11] == 0x0F:
                units = entry[1:11] + entry[14:26] + entry[28:32]
                chars = [int.from_bytes(units[index:index + 2], "little") for index in range(0, 26, 2)]
                pending_name = "".join(chr(value) for value in chars if value not in (0, 0xFFFF))
                continue
            if entry[11] & 0x08:
                continue
            name = pending_name
            self.assertIsNotNone(name)
            pending_name = None
            cluster = int.from_bytes(entry[26:28], "little")
            size = int.from_bytes(entry[28:32], "little")
            output = bytearray()
            while cluster >= 2 and cluster < 0xFF8:
                position = data_start + (cluster - 2) * bytes_per_sector
                output.extend(image[position:position + bytes_per_sector])
                offset = cluster + cluster // 2
                pair = int.from_bytes(fat1[offset:offset + 2], "little")
                cluster = (pair >> 4) if cluster & 1 else (pair & 0x0FFF)
            files[name] = bytes(output[:size])
        return files

    def test_builds_labeled_vfat_seed_with_cloud_init_filenames(self):
        image = build_nocloud_vfat_image(self.USER_DATA, self.META_DATA)
        files = self.extract_files(image)
        self.assertEqual(files, {"user-data": self.USER_DATA.encode(), "meta-data": self.META_DATA.encode()})

    def test_two_fat_copies_and_volume_signature_are_valid(self):
        image = build_nocloud_vfat_image(self.USER_DATA, self.META_DATA)
        self.assertEqual(image[510:512], b"\x55\xaa")
        self.assertEqual(image[512:515], b"\xf8\xff\xff")

    def test_rejects_invalid_or_oversized_seed_contents(self):
        for user_data, meta_data in (("plain text", self.META_DATA), (self.USER_DATA, ""), (self.USER_DATA, "x" * 300_000)):
            with self.subTest(user_bytes=len(user_data), meta_bytes=len(meta_data)), self.assertRaises(NoCloudImageError):
                build_nocloud_vfat_image(user_data, meta_data)

    def test_private_writer_creates_mode_0600_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cidata.img"
            result = write_nocloud_vfat_image(path, self.USER_DATA, self.META_DATA)
            self.assertEqual(result, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.extract_files(path.read_bytes())["user-data"], self.USER_DATA.encode())
            with self.assertRaises(NoCloudImageError):
                write_nocloud_vfat_image(path, self.USER_DATA, self.META_DATA)

    def test_rejects_unsafe_output_path_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".." / "bad.img"
            with self.assertRaises(NoCloudImageError):
                write_nocloud_vfat_image(path, self.USER_DATA, self.META_DATA)


if __name__ == "__main__":
    unittest.main()
