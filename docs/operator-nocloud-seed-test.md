# Operator-only NoCloud seed and console-login test

**Status:** runbook only. No seed media was created or transferred, no CDROM was attached, and no TrueNAS state was changed for this document.

This procedure is for a manually operated, console-only seed-pickup test of the disposable Ubuntu Server VM. It is not installer functionality. It does not attach a NIC or test networking.

## Important evidence boundary

The current disposable VM has already booted once without seed media. Attaching NoCloud media to it tests whether cloud-init discovers and consumes the seed on a later boot; it is **not a pristine first-boot test**. Do not claim first-boot validation from this procedure. A clean first-boot claim requires a fresh disk/VM made from the pinned image.

The TrueNAS host was checked read-only on 2026-10-23: `cloud-localds` and `genisoimage` were absent. Do not install packages or build the ISO on TrueNAS for this test. Build it on a trusted Linux workstation that already has `python3`, `mkpasswd` (from `whois`), `cloud-localds` (from `cloud-utils`), and `genisoimage`. If those tools are not available on the workstation, stop; do not install them on TrueNAS.

No trusted transfer path from the workstation into the VM's child dataset has been verified. Use only an already-approved file-transfer path. If none is available, stop here—do not enable SSH/SMB, change ACLs, weaken permissions, or use a network share just for this test. The mountpoint returned by TrueNAS and the path-prefix checks below are necessary identity checks, not proof that the dataset is mounted, visible to every service, or effectively accessible. The post-attach `runuser` check verifies read access for `libvirt-qemu` only; it does not prove middleware conversion access, cloud-init behavior, or guest provisioning.

## Safety rules

- Keep the VM stopped while attaching seed media. Use the TrueNAS **Stop** action and confirm `STOPPED`; do not use **Power Off** to force it down. If graceful stop fails, stop and investigate.
- Keep the VM's device set limited to its existing loopback-only SPICE display and VirtIO disk plus this one CDROM. Do not add a NIC.
- The seed requests one disposable account named `vmtest` plus eight random hexadecimal characters, with no sudo rule. It does not request cloud-init's distro-default user, but this VM has already booted once, so an existing default account may remain; do not treat this test account as an isolation boundary. Use a unique, high-entropy one-time password; never reuse a production password or send it in chat. The seed contains a password **hash**, which is still sensitive. Keep the ISO private and do not publish it or include it in Git.
- The TrueNAS SPICE/display password controls access to the viewer. It is not the Ubuntu guest password. Do not put the SPICE password in NoCloud data.
- TrueNAS' CDROM delegate may change the ISO owner to the libvirt service user. Read back owner and mode after attachment. Never work around access errors with `chmod 777`, broad ACLs, or blind retries.
- Every live write below is a separate operator action. This document has not been live-runtime tested. Stop on any mismatch or ambiguous response; query exact state before considering a retry.

## 1. Build private NoCloud media on a trusted Linux workstation

The following builder prompts without echoing the password. It passes the password to `mkpasswd` through stdin, writes only the resulting SHA-512 crypt hash into `user-data`, gives files restrictive modes, and prints no password/hash. It creates an ISO with a unique name in the workstation user's home directory. Do not paste the generated credential or ISO contents into chat.

The test account is deliberately not given a sudo rule, extra groups, SSH keys, or network configuration. The one-time password hash remains sensitive even though plaintext is not written to the ISO.

```sh
python3 - <<'PY'
import getpass
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path


def stop(message):
    raise SystemExit("STOP: " + message)


for command in ("mkpasswd", "cloud-localds", "genisoimage"):
    if shutil.which(command) is None:
        stop(f"required workstation tool is missing: {command}; do not install it on TrueNAS")

# Check the hash tool before asking for a real password. Never print its output.
probe = subprocess.run(
    ["mkpasswd", "--method=SHA-512", "--rounds=500000", "--stdin"],
    input="runbook-preflight-only\n",
    capture_output=True,
    text=True,
    timeout=30,
)
if probe.returncode != 0 or not re.fullmatch(
    r"\$6\$rounds=500000\$[A-Za-z0-9./]+\$[A-Za-z0-9./]+\n?",
    probe.stdout,
):
    stop("mkpasswd did not produce the required SHA-512 crypt format")

password = getpass.getpass("One-time guest password (20+ random characters): ")
confirm = getpass.getpass("Confirm one-time guest password: ")
if password != confirm:
    stop("password confirmation did not match")
if len(password) < 20 or any(ord(char) < 32 or ord(char) == 127 for char in password):
    stop("use a unique random password of at least 20 characters without control characters")

hashed = subprocess.run(
    ["mkpasswd", "--method=SHA-512", "--rounds=500000", "--stdin"],
    input=password + "\n",
    capture_output=True,
    text=True,
    timeout=30,
)
password = None
confirm = None
if hashed.returncode != 0:
    stop("password hashing failed; no seed media was published")
password_hash = hashed.stdout.rstrip("\n")
if not re.fullmatch(
    r"\$6\$rounds=500000\$[A-Za-z0-9./]+\$[A-Za-z0-9./]+",
    password_hash,
):
    stop("password hash format was unexpected; no seed media was published")

run_id = uuid.uuid4().hex
username = "vmtest" + run_id[:8]
metadata = {
    "dsmode": "local",
    "instance-id": "iid-" + run_id,
    "local-hostname": username,
}
user_data = {
    "disable_root": True,
    "ssh_pwauth": False,
    "users": [
        {
            "name": username,
            "shell": "/bin/bash",
            "lock_passwd": False,
            "sudo": None,
            "passwd": password_hash,
        },
    ],
}
password_hash = None

home = Path.home()
output = home / f"nocloud-seed-{run_id}.iso"
if output.exists() or output.is_symlink():
    stop("unique output path already exists; do not overwrite it")

old_umask = os.umask(0o077)
published = False
try:
    with tempfile.TemporaryDirectory(prefix=".nocloud-seed-", dir=home) as temp_name:
        temp_dir = Path(temp_name)
        os.chmod(temp_dir, 0o700)
        user_path = temp_dir / "user-data"
        meta_path = temp_dir / "meta-data"
        staged_iso = temp_dir / "seed.iso"
        for path, text in (
            (user_path, "#cloud-config\n" + json.dumps(user_data, sort_keys=True) + "\n"),
            (meta_path, json.dumps(metadata, sort_keys=True) + "\n"),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
        result = subprocess.run(
            [
                "cloud-localds",
                "--disk-format", "raw",
                "--filesystem", "iso",
                str(staged_iso),
                str(user_path),
                str(meta_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stop("cloud-localds failed; output was suppressed to avoid exposing seed data")
        info = staged_iso.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            stop("cloud-localds did not create a nonempty regular ISO")
        os.chmod(staged_iso, 0o600, follow_symlinks=False)
        os.link(staged_iso, output, follow_symlinks=False)
        published = True
        final_info = output.lstat()
        if not stat.S_ISREG(final_info.st_mode) or stat.S_IMODE(final_info.st_mode) != 0o600:
            stop("published ISO failed the mode-0600 check")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        print(json.dumps({
            "status": "private NoCloud ISO created",
            "path": str(output),
            "size_bytes": final_info.st_size,
            "sha256": digest,
            "volume_label_expected": "cidata",
            "guest_username": username,
        }, separators=(",", ":")))
except BaseException:
    if published:
        output.unlink(missing_ok=True)
    raise
finally:
    os.umask(old_umask)
PY
```

If the builder stops, do not proceed with a partial file. The local seed helper in `planning/ubuntu_server.py` is intentionally key-only and creates a locked account; it is not the builder for this console-password test.

## 2. Transfer precondition — no transfer command is provided

Before any TrueNAS operation, identify an already-authorized path that places this exact ISO in the VM's existing child filesystem dataset. The path must remain on the selected non-boot pool, beneath that VM's child-dataset mountpoint (not the pool root), and the final file must be a regular mode-0600 file whose SHA-256 matches the builder's printed digest. The attach/start gates ask for that digest and verify the file. Verify the exact dataset row and mountpoint from TrueNAS before attaching it.

If an approved transfer path does not preserve or permit verification of the file's exact location and mode, stop. Do not change NAS services, networking, ACLs, or permissions to make this runbook work. No such transfer path was verified while preparing this document.

## 3. Attach the ISO to the stopped VM

Only after the transfer precondition is satisfied, gracefully stop the VM in TrueNAS and confirm it is `STOPPED`. Paste this block into an authenticated TrueNAS Shell. It rechecks the exact build, VM identity/configuration, pool health, child dataset and ISO path, existing device set, and path conflicts. It creates one CDROM, reads it back, then checks effective readability as `libvirt-qemu`. It never prints the SPICE password or any seed contents.

If the tool call or read-back is ambiguous, do not repeat the create call. Query exact device/path state and stop for manual review. If post-attach readability fails, leave the VM stopped; do not chmod or automatically detach.

```sh
sudo -v
sudo python3 - <<'PY'
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path


def call(method, *args):
    command = ["midclt", "call", method]
    command.extend(json.dumps(arg, separators=(",", ":")) for arg in args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"STOP: {method} failed; inspect exact state before retrying")
    text = result.stdout.strip()
    if method == "system.version" and re.fullmatch(r"TrueNAS-[A-Za-z0-9._-]+", text):
        return text
    if method == "vm.supports_virtualization" and text in ("True", "False"):
        return text == "True"
    return json.loads(text)


if os.geteuid() != 0:
    raise SystemExit("STOP: run this block with sudo")
pool_name = input("Disposable test pool: ").strip()
vm_name = input("Disposable test VM name: ").strip()
try:
    vm_id = int(input("Disposable test VM numeric ID: ").strip())
except ValueError:
    raise SystemExit("STOP: VM ID must be an integer")
seed_input = input("Transferred seed ISO full path: ").strip()
expected_sha256 = input("SHA-256 printed by the workstation builder: ").strip()
if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
    raise SystemExit("STOP: expected SHA-256 must be 64 lowercase hexadecimal characters")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name):
    raise SystemExit("STOP: invalid pool name")
if not re.fullmatch(r"[A-Za-z0-9]+", vm_name) or vm_id < 1:
    raise SystemExit("STOP: invalid VM name or ID")
if call("system.version") != "TrueNAS-26.0.0-BETA.2":
    raise SystemExit("STOP: unsupported TrueNAS build")

vm_rows = call("vm.query", [["id", "=", vm_id], ["name", "=", vm_name]], {
    "select": ["id", "name", "status", "vcpus", "memory", "autostart"]
})
if (
    len(vm_rows) != 1
    or vm_rows[0].get("status", {}).get("state") != "STOPPED"
    or vm_rows[0].get("vcpus") != 2
    or vm_rows[0].get("memory") != 4096
    or vm_rows[0].get("autostart") is not False
):
    raise SystemExit("STOP: VM is not the expected stopped 2-vCPU/4096-MiB disposable test VM")

devices = call("vm.device.query", [["vm", "=", vm_id]])
displays = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISPLAY"]
disks = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISK"]
if len(devices) != 2 or len(displays) != 1 or len(disks) != 1:
    raise SystemExit("STOP: expected exactly the existing display and disk; no NIC/CDROM")
da = displays[0].get("attributes", {})
if da.get("type") != "SPICE" or da.get("bind") != "127.0.0.1":
    raise SystemExit("STOP: display is not the expected loopback-only SPICE device")
expected_disk = f"/dev/zvol/{pool_name}/linux-vms/{vm_name}/disk0"
if disks[0].get("attributes", {}).get("path") != expected_disk:
    raise SystemExit("STOP: attached disk does not match the expected test zvol")

pool = call("pool.query", [["name", "=", pool_name]], {
    "select": ["status", "healthy", "warning"]
})
if len(pool) != 1 or pool[0].get("status") != "ONLINE" or pool[0].get("healthy") is not True or pool[0].get("warning") is not False:
    raise SystemExit("STOP: pool health check failed")

dataset_id = f"{pool_name}/linux-vms/{vm_name}"
rows = call("pool.dataset.query", [["id", "=", dataset_id]], {
    "select": ["id", "type", "mountpoint"]
})
if len(rows) != 1 or rows[0].get("type") != "FILESYSTEM":
    raise SystemExit("STOP: exact per-VM filesystem dataset not found")
mountpoint = rows[0].get("mountpoint")
if isinstance(mountpoint, dict):
    mountpoint = mountpoint.get("value") or mountpoint.get("parsed") or mountpoint.get("rawvalue")
if not isinstance(mountpoint, str) or not mountpoint.startswith(f"/mnt/{pool_name}/"):
    raise SystemExit("STOP: dataset mountpoint is not a child of the selected pool")
mount = Path(mountpoint).resolve(strict=True)
if not os.path.ismount(mount):
    raise SystemExit("STOP: exact child dataset mountpoint is not mounted")
seed_candidate = Path(seed_input)
candidate_info = seed_candidate.lstat()
if not stat.S_ISREG(candidate_info.st_mode):
    raise SystemExit("STOP: seed path must be a regular file, not a symlink")
seed_path = seed_candidate.resolve(strict=True)
if seed_path.parent != mount or not re.fullmatch(r"nocloud-seed-[0-9a-f]{32}\.iso", seed_path.name):
    raise SystemExit("STOP: ISO is not the expected unique file directly inside this child dataset")
info = seed_path.lstat()
if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size <= 0:
    raise SystemExit("STOP: ISO must be a nonempty regular mode-0600 file; do not chmod it here")
if hashlib.sha256(seed_path.read_bytes()).hexdigest() != expected_sha256:
    raise SystemExit("STOP: transferred ISO SHA-256 does not match the workstation builder")
if call("vm.device.query", [["attributes.path", "=", str(seed_path)]]):
    raise SystemExit("STOP: ISO path is already referenced by a VM device")

call("vm.device.create", {
    "vm": vm_id,
    "attributes": {"dtype": "CDROM", "path": str(seed_path)},
})

readback = call("vm.device.query", [["vm", "=", vm_id]])
cdroms = [d for d in readback if d.get("attributes", {}).get("dtype") == "CDROM" and d.get("attributes", {}).get("path") == str(seed_path)]
vm_after = call("vm.query", [["id", "=", vm_id], ["name", "=", vm_name]], {"select": ["id", "name", "status"]})
if len(readback) != 3 or len(cdroms) != 1 or len(vm_after) != 1 or vm_after[0].get("status", {}).get("state") != "STOPPED":
    raise SystemExit("STOP: attachment read-back is unexpected; do not retry")
if any(d.get("attributes", {}).get("dtype") == "NIC" for d in readback):
    raise SystemExit("STOP: NIC unexpectedly present; leave VM stopped")
access = subprocess.run(["runuser", "-u", "libvirt-qemu", "--", "test", "-r", str(seed_path)], capture_output=True, timeout=30)
if access.returncode != 0:
    raise SystemExit("STOP: libvirt-qemu cannot read the seed ISO; do not chmod/retry/start; inspect exact owner and device state")
owner = seed_path.stat()
print(json.dumps({
    "status": "CDROM attachment verified",
    "cdrom_device_id": cdroms[0].get("id"),
    "vm_state": "STOPPED",
    "device_count": len(readback),
    "nic_attached": False,
    "iso_owner_uid": owner.st_uid,
    "iso_owner_gid": owner.st_gid,
    "iso_mode_octal": oct(stat.S_IMODE(owner.st_mode)),
}, separators=(",", ":")))
PY
```

## 4. Start with no memory overcommit

Proceed only if Step 3 reported `CDROM attachment verified`. Keep the VM stopped until this separate check passes. This block verifies the loopback display, exact VirtIO disk and CDROM, no NIC, pool health, virtualization, and the same memory reserve used by the earlier console test. It prints no display password or seed content.

```sh
sudo -v
sudo python3 - <<'PY'
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path


def call(method, *args):
    command = ["midclt", "call", method]
    command.extend(json.dumps(arg, separators=(",", ":")) for arg in args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"STOP: {method} failed; inspect exact state before retrying")
    text = result.stdout.strip()
    if method == "system.version" and re.fullmatch(r"TrueNAS-[A-Za-z0-9._-]+", text):
        return text
    if method == "vm.supports_virtualization" and text in ("True", "False"):
        return text == "True"
    return json.loads(text)


pool_name = input("Disposable test pool: ").strip()
vm_name = input("Disposable test VM name: ").strip()
try:
    vm_id = int(input("Disposable test VM numeric ID: ").strip())
except ValueError:
    raise SystemExit("STOP: VM ID must be an integer")
seed_path = input("Exact attached seed ISO path: ").strip()
if os.geteuid() != 0:
    raise SystemExit("STOP: run this block with sudo")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name) or not re.fullmatch(r"[A-Za-z0-9]+", vm_name) or vm_id < 1:
    raise SystemExit("STOP: invalid pool name, VM name, or VM ID")
seed_pattern = rf"/mnt/{re.escape(pool_name)}/linux-vms/{re.escape(vm_name)}/nocloud-seed-[0-9a-f]{{32}}\.iso"
if not re.fullmatch(seed_pattern, seed_path):
    raise SystemExit("STOP: seed path is not the expected ISO beneath this test VM's child dataset")
expected_sha256 = input("SHA-256 printed by the workstation builder: ").strip()
if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
    raise SystemExit("STOP: expected SHA-256 must be 64 lowercase hexadecimal characters")
if call("system.version") != "TrueNAS-26.0.0-BETA.2":
    raise SystemExit("STOP: unsupported TrueNAS build")
vm = call("vm.query", [["id", "=", vm_id], ["name", "=", vm_name]], {
    "select": ["id", "name", "status", "vcpus", "memory", "autostart"]
})
if len(vm) != 1 or vm[0].get("status", {}).get("state") != "STOPPED" or vm[0].get("vcpus") != 2 or vm[0].get("memory") != 4096 or vm[0].get("autostart") is not False:
    raise SystemExit("STOP: VM is not the expected stopped disposable test VM")
if call("vm.supports_virtualization") is not True:
    raise SystemExit("STOP: virtualization unavailable")
seed_file = Path(seed_path)
seed_info = seed_file.lstat()
if not stat.S_ISREG(seed_info.st_mode) or stat.S_IMODE(seed_info.st_mode) != 0o600 or seed_info.st_size <= 0:
    raise SystemExit("STOP: seed ISO is not a nonempty regular mode-0600 file")
if hashlib.sha256(seed_file.read_bytes()).hexdigest() != expected_sha256:
    raise SystemExit("STOP: seed ISO SHA-256 changed after attachment")
access = subprocess.run(["runuser", "-u", "libvirt-qemu", "--", "test", "-r", seed_path], capture_output=True, timeout=30)
if access.returncode != 0:
    raise SystemExit("STOP: libvirt-qemu cannot read the seed ISO; do not start the VM")

path = f"/dev/zvol/{pool_name}/linux-vms/{vm_name}/disk0"
devices = call("vm.device.query", [["vm", "=", vm_id]])
displays = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISPLAY"]
disks = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISK"]
cdroms = [d for d in devices if d.get("attributes", {}).get("dtype") == "CDROM"]
if len(devices) != 3 or len(displays) != 1 or len(disks) != 1 or len(cdroms) != 1:
    raise SystemExit("STOP: expected one display, one disk, one CDROM, and no NIC")
da = displays[0].get("attributes", {})
if da.get("type") != "SPICE" or da.get("bind") != "127.0.0.1":
    raise SystemExit("STOP: display is not loopback-only SPICE")
if disks[0].get("attributes", {}).get("path") != path or disks[0].get("attributes", {}).get("type") != "VIRTIO":
    raise SystemExit("STOP: disk does not match the expected test zvol")
if cdroms[0].get("attributes", {}).get("path") != seed_path:
    raise SystemExit("STOP: CDROM path differs from the verified seed ISO")
disk_refs = call("vm.device.query", [["attributes.path", "=", path]])
seed_refs = call("vm.device.query", [["attributes.path", "=", seed_path]])
if len(disk_refs) != 1 or disk_refs[0].get("vm") != vm_id or disk_refs[0].get("id") != disks[0].get("id"):
    raise SystemExit("STOP: zvol device reference is ambiguous")
if len(seed_refs) != 1 or seed_refs[0].get("vm") != vm_id or seed_refs[0].get("id") != cdroms[0].get("id"):
    raise SystemExit("STOP: seed device reference is ambiguous")

pool = call("pool.query", [["name", "=", pool_name]], {"select": ["status", "healthy", "warning"]})
if len(pool) != 1 or pool[0].get("status") != "ONLINE" or pool[0].get("healthy") is not True or pool[0].get("warning") is not False:
    raise SystemExit("STOP: pool health check failed")
zvol_id = f"{pool_name}/linux-vms/{vm_name}/disk0"
zvol = call("pool.dataset.query", [["id", "=", zvol_id]], {"select": ["id", "type", "volsize", "refreservation"]})
if len(zvol) != 1 or zvol[0].get("type") != "VOLUME" or zvol[0].get("volsize", {}).get("parsed") != 16 * 1024**3 or zvol[0].get("refreservation", {}).get("parsed") != 16 * 1024**3:
    raise SystemExit("STOP: test disk is not the expected thick 16 GiB zvol")
available = call("vm.get_available_memory", False)
if not isinstance(available, int) or available < 20 * 1024**3:
    raise SystemExit("STOP: available memory is below the 4-GiB VM plus 16-GiB host reserve")

result = call("vm.start", vm_id, {"overcommit": False})
if result is not None:
    raise SystemExit("STOP: unexpected start response; inspect live state before retrying")
readback = call("vm.query", [["id", "=", vm_id]], {"select": ["id", "name", "status"]})
if len(readback) != 1:
    raise SystemExit("STOP: VM read-back unexpected; do not retry")
status = readback[0].get("status", {})
if status.get("state") != "RUNNING":
    raise SystemExit("STOP: VM did not reach RUNNING; inspect exact state and do not retry")
print(json.dumps({
    "start_call_returned": "success",
    "vm_state": status.get("state"),
    "domain_state": status.get("domain_state"),
    "nic_attached": False,
}, separators=(",", ":")))
PY
```

A successful start request is not proof that NoCloud ran. Open the guest console. Use the `guest_username` printed by the workstation builder and the one-time guest password entered there. The TrueNAS SPICE password, if requested by the viewer, is a different credential. If the seed account does not appear or one careful login attempt fails, capture the console and stop; do not repeatedly guess or rebuild/re-attach the seed blindly.

## 5. Evidence and limited cleanup

Record only non-secret evidence: TrueNAS build, VM settings, the `CDROM attachment verified` read-back, start/read-back state, and whether the generated test account reached the guest login. Do not capture or share the password, password hash, full seed ISO, or output containing seed contents.

After the test, use TrueNAS **Stop** and confirm the VM is stopped. This block deletes only the exact CDROM device with all destructive options explicitly false, then reads back both the VM device list and exact ISO-path references. It does not delete the ISO.

```sh
sudo -v
sudo python3 - <<'PY'
import json
import os
import re
import subprocess
from pathlib import Path


def call(method, *args):
    command = ["midclt", "call", method]
    command.extend(json.dumps(arg, separators=(",", ":")) for arg in args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"STOP: {method} failed; query exact state before retrying")
    text = result.stdout.strip()
    if method == "system.version" and re.fullmatch(r"TrueNAS-[A-Za-z0-9._-]+", text):
        return text
    return json.loads(text)


pool_name = input("Disposable test pool: ").strip()
vm_name = input("Disposable test VM name: ").strip()
try:
    vm_id = int(input("Disposable test VM numeric ID: ").strip())
    device_id = int(input("Exact CDROM device ID: ").strip())
except ValueError:
    raise SystemExit("STOP: VM and device IDs must be integers")
seed_path = input("Exact attached seed ISO path: ").strip()
if os.geteuid() != 0:
    raise SystemExit("STOP: run this block with sudo")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name) or not re.fullmatch(r"[A-Za-z0-9]+", vm_name) or vm_id < 1 or device_id < 1:
    raise SystemExit("STOP: invalid pool, VM, or device identifier")
seed_pattern = rf"/mnt/{re.escape(pool_name)}/linux-vms/{re.escape(vm_name)}/nocloud-seed-[0-9a-f]{{32}}\.iso"
if not re.fullmatch(seed_pattern, seed_path):
    raise SystemExit("STOP: seed path is outside the exact test VM child dataset")
if call("system.version") != "TrueNAS-26.0.0-BETA.2":
    raise SystemExit("STOP: unsupported TrueNAS build")
vm = call("vm.query", [["id", "=", vm_id], ["name", "=", vm_name]], {"select": ["id", "name", "status"]})
if len(vm) != 1 or vm[0].get("status", {}).get("state") != "STOPPED":
    raise SystemExit("STOP: exact VM is not confirmed stopped")
devices = call("vm.device.query", [["vm", "=", vm_id]])
match = [d for d in devices if d.get("id") == device_id]
if len(match) != 1 or match[0].get("attributes", {}).get("dtype") != "CDROM" or match[0].get("attributes", {}).get("path") != seed_path:
    raise SystemExit("STOP: device ID/type/path do not match the exact seed CDROM")
refs = call("vm.device.query", [["attributes.path", "=", seed_path]])
if len(refs) != 1 or refs[0].get("id") != device_id or refs[0].get("vm") != vm_id:
    raise SystemExit("STOP: seed path reference is ambiguous")
if call("vm.device.delete", device_id, {"force": False, "raw_file": False, "zvol": False}) is not True:
    raise SystemExit("STOP: delete did not return true; query exact state before retrying")
after_vm = call("vm.device.query", [["vm", "=", vm_id]])
after_path = call("vm.device.query", [["attributes.path", "=", seed_path]])
if any(d.get("id") == device_id for d in after_vm) or after_path:
    raise SystemExit("STOP: exact CDROM/path reference remains; do not unlink ISO")
print(json.dumps({"status": "seed CDROM detached", "device_id": device_id, "vm_state": "STOPPED", "path_references": 0}, separators=(",", ":")))
PY
```

Only after that block reports `seed CDROM detached`, this separate block may unlink the exact ISO. It rechecks the exact dataset row, mountpoint, regular-file status, and absence of any VM device reference. It never removes directories or other files.

```sh
sudo -v
sudo python3 - <<'PY'
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path


def call(method, *args):
    command = ["midclt", "call", method]
    command.extend(json.dumps(arg, separators=(",", ":")) for arg in args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"STOP: {method} failed; inspect exact state")
    text = result.stdout.strip()
    if method == "system.version" and re.fullmatch(r"TrueNAS-[A-Za-z0-9._-]+", text):
        return text
    return json.loads(text)


pool_name = input("Disposable test pool: ").strip()
vm_name = input("Disposable test VM name: ").strip()
seed_input = input("Exact detached seed ISO path: ").strip()
if os.geteuid() != 0:
    raise SystemExit("STOP: run this block with sudo")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name) or not re.fullmatch(r"[A-Za-z0-9]+", vm_name):
    raise SystemExit("STOP: invalid pool or VM name")
seed_pattern = rf"/mnt/{re.escape(pool_name)}/linux-vms/{re.escape(vm_name)}/nocloud-seed-[0-9a-f]{{32}}\.iso"
if not re.fullmatch(seed_pattern, seed_input):
    raise SystemExit("STOP: path is outside the exact test VM child dataset")
if call("system.version") != "TrueNAS-26.0.0-BETA.2":
    raise SystemExit("STOP: unsupported TrueNAS build")
dataset_id = f"{pool_name}/linux-vms/{vm_name}"
rows = call("pool.dataset.query", [["id", "=", dataset_id]], {"select": ["id", "type", "mountpoint"]})
if len(rows) != 1 or rows[0].get("type") != "FILESYSTEM":
    raise SystemExit("STOP: exact per-VM filesystem dataset not found")
mountpoint = rows[0].get("mountpoint")
if isinstance(mountpoint, dict):
    mountpoint = mountpoint.get("value") or mountpoint.get("parsed") or mountpoint.get("rawvalue")
if not isinstance(mountpoint, str) or not mountpoint.startswith(f"/mnt/{pool_name}/"):
    raise SystemExit("STOP: dataset mountpoint is not below the selected pool")
mount = Path(mountpoint).resolve(strict=True)
if not os.path.ismount(mount):
    raise SystemExit("STOP: exact child dataset mountpoint is not mounted")
candidate = Path(seed_input)
info = candidate.lstat()
if not stat.S_ISREG(info.st_mode):
    raise SystemExit("STOP: seed path must be a regular file, not a symlink")
seed_path = candidate.resolve(strict=True)
if seed_path.parent != mount:
    raise SystemExit("STOP: seed file is not directly inside the exact child dataset")
vm_rows = call("vm.query", [["name", "=", vm_name]], {"select": ["id", "name", "status"]})
if len(vm_rows) != 1 or vm_rows[0].get("status", {}).get("state") != "STOPPED":
    raise SystemExit("STOP: exact VM is not confirmed stopped")
if call("vm.device.query", [["attributes.path", "=", str(seed_path)]]):
    raise SystemExit("STOP: a VM device still references this ISO")
seed_path.unlink()
if seed_path.exists():
    raise SystemExit("STOP: ISO still exists after unlink; do not retry blindly")
print(json.dumps({"status": "exact seed ISO removed", "remaining_vm_path_references": 0}, separators=(",", ":")))
PY
```

The workstation's original ISO is a separate file; remove only that exact file if no longer needed. Do not use recursive/forced deletion and do not delete the VM, zvol, dataset, or source image as part of this seed cleanup.

Removing the ISO does not guarantee secure erasure from ZFS snapshots or underlying media. Rotate the one-time guest password immediately after a successful login. Do not snapshot or publish the seed ISO. These cleanup blocks do not delete the VM, zvol, dataset, or source image, and have not been live-validated.

## References

- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [cloud-init user and password configuration](https://docs.cloud-init.io/en/latest/reference/modules.html)
- [Canonical `cloud-localds` source](https://github.com/canonical/cloud-utils/blob/main/bin/cloud-localds)
- [Debian `mkpasswd` manual](https://manpages.debian.org/bookworm/whois/mkpasswd.1.en.html)
