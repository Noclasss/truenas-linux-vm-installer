"""Offline post-install step: detach installer media only after confirmed poweroff.

Like desktop_deploy_mock, this module has no TrueNAS adapter, no subprocesses and
no filesystem writes. It accepts only the built-in offline fake. It never starts
the VM, never deletes media or resources, and never retries an ambiguous detach.
"""
from typing import Any

if __package__:
    from .desktop_deploy_mock import DeploymentRefused, DesktopRequest, FakeBackend, resource_names
    from .desktop_write_contracts import INSTALLER_ISO_NAME, SEED_IMAGE_NAME, media_path
else:
    from desktop_deploy_mock import DeploymentRefused, DesktopRequest, FakeBackend, resource_names
    from desktop_write_contracts import INSTALLER_ISO_NAME, SEED_IMAGE_NAME, media_path

MEDIA_DTYPES = ("CDROM", "RAW")


def expected_media(request: DesktopRequest) -> dict[str, str]:
    """Planned host paths for the installer ISO and seed image of this request."""
    _, disk = resource_names(request)
    return {
        "CDROM": media_path(disk, INSTALLER_ISO_NAME),
        "RAW": media_path(disk, SEED_IMAGE_NAME),
    }


def verify_media_readback(devices: list[dict[str, Any]], disk: str,
                          expected: dict[str, str]) -> None:
    """Require exactly the planned media and the planned zvol, read back from the fake.

    Raises DeploymentRefused on any missing, duplicate, or unexpected device.
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        by_type.setdefault(device.get("dtype"), []).append(device)

    disk_devices = by_type.get("DISK", [])
    if len(disk_devices) != 1 or disk_devices[0].get("path") != f"/dev/zvol/{disk}":
        raise DeploymentRefused("read-back disk device does not match the planned zvol")
    for dtype in MEDIA_DTYPES:
        found = by_type.get(dtype, [])
        if len(found) != 1:
            raise DeploymentRefused(f"read-back expected exactly one {dtype} device")
        if found[0].get("path") != expected[dtype]:
            raise DeploymentRefused(f"read-back {dtype} path does not match the plan")
    unexpected = set(by_type) - {"DISK", *MEDIA_DTYPES}
    if unexpected:
        raise DeploymentRefused("read-back shows unplanned device types: " + ", ".join(sorted(unexpected)))


def finish_installation(backend: FakeBackend, request: DesktopRequest) -> tuple[str, ...]:
    """Detach CDROM and seed media after the VM reports STOPPED (autoinstall poweroff).

    Refuses while the VM is RUNNING, because the guest may still be installing.
    Ambiguous detach results are reported for manual reconciliation, not retried.
    """
    if type(backend) is not FakeBackend:
        raise DeploymentRefused("only the built-in offline fake is permitted")
    vm_name, disk = resource_names(request)
    if not backend.exists("vm", vm_name):
        raise DeploymentRefused("installer VM is missing")
    if backend.status(vm_name) != "STOPPED":
        raise DeploymentRefused("autoinstall poweroff not confirmed; installation media stays attached")

    expected = expected_media(request)
    verify_media_readback(backend.device_list(vm_name), disk, expected)

    events: list[str] = []
    try:
        for dtype, label in (("CDROM", "installer-media-detached"), ("RAW", "seed-media-detached")):
            backend.detach_media(vm_name, dtype)
            events.append(label)
    except Exception as exc:
        raise DeploymentRefused(
            "media detach failed or ambiguous; reconcile manually; "
            f"completed steps: {', '.join(events) or 'none'}"
        ) from exc

    verify_media_readback_after_detach(backend, vm_name, disk)
    events.append("media-detach-verified")
    if backend.status(vm_name) != "STOPPED":
        raise DeploymentRefused("VM left STOPPED state during media detach")
    return tuple(events)


def verify_media_readback_after_detach(backend: FakeBackend, vm_name: str, disk: str) -> None:
    devices = backend.device_list(vm_name)
    remaining = [d for d in devices if d.get("dtype") in MEDIA_DTYPES]
    if remaining:
        raise DeploymentRefused("installation media still attached after detach read-back")
    if [d.get("dtype") for d in devices] != ["DISK"]:
        raise DeploymentRefused("unexpected device set after detach read-back")
    if devices[0].get("path") != f"/dev/zvol/{disk}":
        raise DeploymentRefused("disk device changed during media detach")
