# Ubuntu Desktop create-only adapter checkpoint

## DISPLAY behavior — source-checked for Beta.2

The inspected TrueNAS middleware tag is `TS-26.0.0-BETA.2`, commit `69e77a6f2e089d392db6e4a3df146bcbe8fba98e`:

- [`plugins/vm/vms.py` at the tag](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/plugins/vm/vms.py#L58) declares `ensure_display_device` as a stored VM field. `extend_vm()` queries devices and calculates `display_available` from the existing device rows (lines 112–119). `do_create()` validates, inserts the VM record, generates configuration, and returns the instance (lines 134–213); it does not call `vm.device.create` or create a DISPLAY device.
- [`plugins/vm/vm_devices.py` at the tag](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/plugins/vm/vm_devices.py#L368-L384) exposes device creation separately through `do_create()` and `_create_impl()`.

The adapter therefore expects `vm.create` to return an empty device list and `display_available: false`, verifies the new VM by ID and properties, then calls `vm.device.create` once with an explicit DISPLAY payload. It verifies the returned positive device ID, association with the intended VM, non-secret attributes, middleware-assigned distinct display ports, and exact inventory. Any unexpected device or inconsistent display state after `vm.create` is journaled and stops all later mutations. A failed/lost DISPLAY response or mismatched read-back stays pending/ambiguous for manual read-only reconciliation; there is no automatic retry, rollback, or deletion.

## Display password contract and handling

The Beta.2 source schema does **not** require a non-empty display password: [`VMDisplayDevice`](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/api/v26_0_0/vm_device.py#L35-L56) defines `password: Secret[str | None] = None`. The source validation path is [`DeviceMixin._validate_device`](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/utils/libvirt/mixin.py#L179-L193) plus [`DisplayDelegate.validate_middleware` / `validate_port_attrs`](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/utils/libvirt/display.py#L36-L89): it validates display uniqueness, bind address, and ports, but does not reject an empty/null password. This is distinct from the loaded development guidance's stronger security recommendation. The adapter deliberately applies that recommendation as a *local policy*: it obtains a non-empty printable value with a hidden `getpass` prompt, without a default credential, before the first resource write.

Beta.2 allows `port` and `web_port` to be null; [`DisplayDelegate.validate_port_attrs`](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/utils/libvirt/display.py#L61-L89) assigns available distinct ports when null. The adapter therefore sends null for automatic assignment and validates the returned ports without expecting a literal null read-back.

The password is sent only in the explicit `vm.device.create` request. The adapter excludes it from the operation manifest and request fingerprint, suppresses raw exception causes, omits it from read-back comparisons and returned evidence, and never prints device records. TrueNAS persists device attributes in the encrypted JSON `vm_device.attributes` column ([`VMDeviceModel`](https://github.com/truenas/middleware/blob/69e77a6f2e089d392db6e4a3df146bcbe8fba98e/src/middlewared/middlewared/plugins/vm/vm_devices.py#L43-L49)); therefore this design does **not** promise that the password is not persisted by TrueNAS. API read-backs may contain the value, which the adapter does not expose.

This source review establishes the expected Beta.2 code path and schema only, not behavior on a different build or successful live write acceptance. No TrueNAS operations were performed.

## Verification state

- Adapter implementation and fake coverage are in `planning/desktop_live_writer.py` and `planning/test_desktop_live_writer.py`.
- **Live gate (stated once here).** `LIVE_PROFILE_ALLOWLIST` (defined in `planning/mock_install.py`) is empty on `main`, so there are no live writes: `DesktopCreateOnlyWriter.create` and `desktop_operator.py create` refuse for any profile outside it before any middleware call or confirmation prompt. The seed attach re-hashes the staged seed against the source pin (`seed_pin_from_file` / `verify_staged_seed`) immediately before `vm.device.create` and aborts with no attach on pin mismatch, wrong size, or missing file.
- The public launcher remains preview-only.
- A failed seed or ISO check after `vm.create` leaves the VM, disk and CDROM for manual cleanup (`reconcile_required`); nothing is rolled back or deleted automatically.
- A passing fake or schema check does not establish target write acceptance, VM-process media access, unattended installation, poweroff, or graphical login.
- Independent review and separately authorized disposable live validation remain required before any release or merge.
