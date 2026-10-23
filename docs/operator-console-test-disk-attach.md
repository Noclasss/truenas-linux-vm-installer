# Operator-only console VM disk-attach runbook

This manual TrueNAS Shell step attaches an existing, disposable 16 GiB zvol to an existing stopped Ubuntu Server test VM on `TrueNAS-26.0.0-BETA.2`. It is not installer code and must not be used as a general deployment command. It creates one VM device only: no NIC, no VM start, and no cleanup.

Before running it, independently confirm that the image-conversion job for this exact test zvol completed successfully. A successful conversion job alone does not prove the guest will boot. The script rechecks the VM, display, pool, zvol size/reservation, and exact device-path collision before writing; it immediately reads the device back after the write. If the command errors or its output is ambiguous, query exact state and do not blindly repeat it.

Paste the block into an authenticated TrueNAS Shell. Enter the exact pool name, VM name, and VM ID for the disposable test resources when prompted. The password on the display device is never printed by this block.

```sh
sudo -v
python3 - <<'PY'
import json
import re
import subprocess


def call(method, *args):
    command = ["sudo", "midclt", "call", method]
    command.extend(json.dumps(arg, separators=(",", ":")) for arg in args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(f"{method} failed: {result.stderr.strip() or result.stdout.strip()}")
    text = result.stdout.strip()
    if method == "system.version" and re.fullmatch(r"TrueNAS-[A-Za-z0-9._-]+", text):
        return text
    return json.loads(text)


pool_name = input("Disposable test pool: ").strip()
vm_name = input("Disposable test VM name: ").strip()
try:
    vm_id = int(input("Disposable test VM numeric ID: ").strip())
except ValueError:
    raise SystemExit("STOP: VM ID must be an integer")

if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name):
    raise SystemExit("STOP: invalid pool name")
if not re.fullmatch(r"[A-Za-z0-9]+", vm_name):
    raise SystemExit("STOP: VM name must contain only ASCII letters and digits")
if vm_id < 1:
    raise SystemExit("STOP: VM ID must be positive")
if call("system.version") != "TrueNAS-26.0.0-BETA.2":
    raise SystemExit("STOP: unsupported TrueNAS build")

vm = call(
    "vm.query",
    [["id", "=", vm_id], ["name", "=", vm_name]],
    {"select": ["id", "name", "status", "vcpus", "memory", "autostart"]},
)
if (
    len(vm) != 1
    or vm[0].get("status", {}).get("state") != "STOPPED"
    or vm[0].get("vcpus") != 2
    or vm[0].get("memory") != 4096
    or vm[0].get("autostart") is not False
):
    raise SystemExit("STOP: VM is not the expected stopped 2-vCPU/4096-MiB test VM")

devices = call("vm.device.query", [["vm", "=", vm_id]])
if len(devices) != 1:
    raise SystemExit("STOP: expected exactly one existing display device")
display = devices[0].get("attributes", {})
if (
    display.get("dtype") != "DISPLAY"
    or display.get("bind") != "127.0.0.1"
    or display.get("type") != "SPICE"
    or display.get("port") == display.get("web_port")
):
    raise SystemExit("STOP: existing display is not the expected loopback-only SPICE display")

pool = call(
    "pool.query",
    [["name", "=", pool_name]],
    {"select": ["status", "healthy", "warning"]},
)
if (
    len(pool) != 1
    or pool[0].get("status") != "ONLINE"
    or pool[0].get("healthy") is not True
    or pool[0].get("warning") is not False
):
    raise SystemExit("STOP: pool health check failed")

zvol_id = f"{pool_name}/linux-vms/{vm_name}/disk0"
path = f"/dev/zvol/{zvol_id}"
zvol = call(
    "pool.dataset.query",
    [["id", "=", zvol_id]],
    {"select": ["id", "type", "volsize", "refreservation"]},
)
if (
    len(zvol) != 1
    or zvol[0].get("type") != "VOLUME"
    or zvol[0].get("volsize", {}).get("parsed") != 16 * 1024**3
    or zvol[0].get("refreservation", {}).get("parsed") != 16 * 1024**3
):
    raise SystemExit("STOP: target is not the expected thick 16 GiB zvol")
if call("vm.device.query", [["attributes.path", "=", path]]):
    raise SystemExit("STOP: target zvol is already attached to a VM")

call(
    "vm.device.create",
    {
        "vm": vm_id,
        "attributes": {"dtype": "DISK", "path": path, "type": "VIRTIO"},
    },
)

devices = call("vm.device.query", [["vm", "=", vm_id]])
disks = [
    device for device in devices
    if device.get("attributes", {}).get("dtype") == "DISK"
    and device.get("attributes", {}).get("path") == path
    and device.get("attributes", {}).get("type") == "VIRTIO"
]
displays = [
    device for device in devices
    if device.get("attributes", {}).get("dtype") == "DISPLAY"
    and device.get("attributes", {}).get("bind") == "127.0.0.1"
]
vm_after = call(
    "vm.query",
    [["id", "=", vm_id], ["name", "=", vm_name]],
    {"select": ["id", "name", "status"]},
)
if len(devices) != 2 or len(disks) != 1 or len(displays) != 1:
    raise SystemExit("STOP: device read-back unexpected; do not retry")
if len(vm_after) != 1 or vm_after[0].get("status", {}).get("state") != "STOPPED":
    raise SystemExit("STOP: VM state changed unexpectedly; do not start it")

print(json.dumps({
    "status": "disk attachment verified",
    "disk_device_id": disks[0].get("id"),
    "device_count": len(devices),
    "vm_state": "STOPPED",
    "nic_attached": False,
}, separators=(",", ":")))
PY
```

A verified attachment is not a boot result. Guest console behavior, network behavior, cloud-init, login, and installer automation remain untested by this step.
