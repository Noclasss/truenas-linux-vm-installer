# Operator-only console VM disk-attach runbook

This manual TrueNAS Shell runbook has two separate steps for a disposable 16 GiB Ubuntu Server console test on `TrueNAS-26.0.0-BETA.2`: attach the zvol to the stopped VM, then optionally request a no-overcommit start. It is not installer code and must not be used as a general deployment command. No NIC is added and no cleanup is performed.

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

An attachment is not a boot result. The following separate step requests a console-only VM start; it does not prove guest boot or first-boot behavior.

## Step 2: Start the test VM without memory overcommit

Run only after Step 1 reported `disk attachment verified`. Enter the same exact disposable pool, VM name, and VM ID. This step checks the VM is still stopped, confirms its two devices are the loopback display and expected VirtIO zvol, checks current pool/memory/virtualization state, then requests start with overcommit disabled. It prints only selected state fields. If the response is unclear or the VM remains stopped, inspect state and logs before considering any retry.

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
    if method == "system.version" and text.startswith("TrueNAS-"):
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
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", pool_name):
    raise SystemExit("STOP: invalid pool name")
if not re.fullmatch(r"[A-Za-z0-9]+", vm_name) or vm_id < 1:
    raise SystemExit("STOP: invalid VM name or ID")
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
if call("vm.supports_virtualization") is not True:
    raise SystemExit("STOP: virtualization is unavailable")

path = f"/dev/zvol/{pool_name}/linux-vms/{vm_name}/disk0"
devices = call("vm.device.query", [["vm", "=", vm_id]])
displays = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISPLAY"]
disks = [d for d in devices if d.get("attributes", {}).get("dtype") == "DISK"]
if len(devices) != 2 or len(displays) != 1 or len(disks) != 1:
    raise SystemExit("STOP: expected exactly one display and one disk; no NIC")
a = displays[0].get("attributes", {})
if a.get("type") != "SPICE" or a.get("bind") != "127.0.0.1" or a.get("port") == a.get("web_port"):
    raise SystemExit("STOP: display is not the expected loopback-only SPICE device")
d = disks[0].get("attributes", {})
if d.get("path") != path or d.get("type") != "VIRTIO":
    raise SystemExit("STOP: attached disk differs from the planned zvol")
refs = call("vm.device.query", [["attributes.path", "=", path]])
if len(refs) != 1 or refs[0].get("vm") != vm_id or refs[0].get("id") != disks[0].get("id"):
    raise SystemExit("STOP: zvol device reference is ambiguous")

pool = call("pool.query", [["name", "=", pool_name]], {
    "select": ["status", "healthy", "warning"]
})
if len(pool) != 1 or pool[0].get("status") != "ONLINE" or pool[0].get("healthy") is not True or pool[0].get("warning") is not False:
    raise SystemExit("STOP: pool health check failed")
zvol_id = f"{pool_name}/linux-vms/{vm_name}/disk0"
zvol = call("pool.dataset.query", [["id", "=", zvol_id]], {
    "select": ["id", "type", "volsize", "refreservation"]
})
if (
    len(zvol) != 1
    or zvol[0].get("type") != "VOLUME"
    or zvol[0].get("volsize", {}).get("parsed") != 16 * 1024**3
    or zvol[0].get("refreservation", {}).get("parsed") != 16 * 1024**3
):
    raise SystemExit("STOP: target is not the expected thick 16 GiB zvol")
available = call("vm.get_available_memory", False)
if not isinstance(available, int) or available < 20 * 1024**3:
    raise SystemExit("STOP: available memory is below the 4-GiB VM plus 16-GiB host reserve")

start_result = call("vm.start", vm_id, {"overcommit": False})
if start_result is not None:
    raise SystemExit("STOP: unexpected start response; inspect live state before retrying")

vm_after = call("vm.query", [["id", "=", vm_id]], {
    "select": ["id", "name", "status"]
})
if len(vm_after) != 1:
    raise SystemExit("STOP: VM read-back unexpected; do not retry")
status = vm_after[0].get("status", {})
print(json.dumps({
    "start_call_returned": "success",
    "vm_state": status.get("state"),
    "domain_state": status.get("domain_state"),
    "nic_attached": False,
}, separators=(",", ":")))
PY
```

A successful start request is not proof that Ubuntu booted. Observe the VM console for boot progress. No NIC is attached, so this test says nothing about network behavior; seed provisioning, login, and installer automation also remain untested.
