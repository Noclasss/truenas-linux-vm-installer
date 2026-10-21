# Offline VM planner

This independent module provides validation and a reviewable JSON plan for the
future installer. The `preview.py` planner is offline-only: it reads only files
you explicitly pass, makes no middleware calls, and does not write files, create
storage, or start VMs. There is no apply flag. Run it on any development machine
with Python 3.10 or newer; no third-party packages are required.

A separate `capture_schema.py` helper is provided for an operator to run from an
authenticated TrueNAS host shell. It makes only the read-only `system.version`
and `core.get_methods` middleware calls and writes selected method metadata to a
new private file under `/tmp`. It does not install a VM or validate any write.

## Try it

From the repository root:

```sh
python3 planning/preview.py --config planning/examples/vm.json
python3 planning/preview.py --config planning/examples/vm.json --inventory planning/examples/inventory.json
python3 planning/preview.py --interactive --inventory planning/examples/inventory.json
python3 -m unittest discover -s planning -p 'test_*.py' -v
```

## Reusable installer core (not a live installer)

`installer_core/` adapts generic safety components from the separate
[HAOS TrueNAS installer](https://github.com/Noclasss/haos-truenas-installer):
local `midclt` calls with timeouts, middleware positional-schema validation,
private and atomic file handling, and HTTPS-only digest/size-verified downloads.
The port includes mocked tests and keeps the upstream MIT notice in this repo's
`LICENSE` file. See `docs/installer-core-provenance.md` for boundaries and source
revision.

These helpers are infrastructure only and are not wired into a live install flow. The Ubuntu Server-specific module at `ubuntu_server.py` pins one official amd64 image, delegates explicit downloads to the generic HTTPS/size/SHA-256 verifier, builds key-only cloud-config and NoCloud metadata, and can generate seed media through `cloud-localds`. The preview displays only static metadata: it does not download an image, create seed media, make middleware calls, or write files. No image import/convert or VM-creation path exists.

## Ubuntu Server profile (experimental)

The first profile is pinned to Canonical's latest dated Ubuntu 26.04 LTS release directory observed on 2026-10-21, `release-20261016` (the manifest and image were last modified on 2026-10-20):

- Artifact: `ubuntu-26.04-server-cloudimg-amd64.img`
- SHA-256 from the official signed `SHA256SUMS`: `5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5`
- Expected size: `859090944` bytes
- The detached signature was checked against Ubuntu's documented cloud-image signing key fingerprint `D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81`. The verifier used for this check did not assess revocation or self-signature status; do not describe it as a complete GnuPG keyring audit. Runtime downloads use the fixed URL, digest, and exact-size check; the raw checksum/signature files are not included.
- The exact downloaded artifact passed the existing pinned size/SHA-256 verifier. On that file, `qemu-img info --output=json` reported `format: qcow2`, `virtual-size: 3758096384`, no backing file, and `dirty-flag: false`; `qemu-img check -f qcow2` reported no errors. A scratch-only `qemu-img convert` to raw also completed with the same virtual size (819,237,376 bytes actual allocation). This supports a future QCOW2-to-raw workflow, but no conversion helper or TrueNAS import path is implemented; it does not validate TrueNAS behavior or guest boot.

Provisioning uses cloud-init's NoCloud datasource: `user-data` plus `meta-data` on CIDATA-labeled ISO media created with `cloud-localds`; the seed metadata sets `dsmode: local` so the datasource does not depend on guest networking. The seed helper accepts validated OpenSSH public keys only, locks the created account's password, disables SSH password authentication and root login, and never accepts private keys or passwords. It invokes `cloud-localds` without a shell, stages seed files in a mode-0700 directory, uses mode-0600 files, bounds execution time, and refuses output overwrite. A real local seed build succeeded using Debian tools extracted only into private scratch (no system packages installed); `isoinfo` reported volume ID `cidata` and both NoCloud files. The key used was a synthetic, nonfunctional test value, not a login credential. No TrueNAS device was created and no guest consumed the ISO. Future host preflight must verify required utilities are present; it must not install packages automatically.

The sanitized `TrueNAS-26.0.0-BETA.2` request-schema fixture includes a `CDROM` device variant with a `path` property. That is accepted-request-schema evidence only; it does not establish write acceptance, boot order, successful ISO attachment, or guest behavior. All four distro profiles remain experimental; no live installer, TrueNAS writes, VM boot, console, network, or first-boot validation has occurred.

Official references: [Ubuntu cloud images](https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/), [Ubuntu cloud-image signature verification](https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/), [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html), and [Canonical cloud-utils](https://github.com/canonical/cloud-utils/blob/main/bin/cloud-localds).


For the separately authorized local schema snapshot, run this from the TrueNAS
host shell (not from an ordinary development machine):

```sh
expected_build=$(midclt call system.version)
python3 planning/capture_schema.py --expected-version "$expected_build"
```

The helper aborts before querying schemas if `system.version` is not an exact
match. It writes a new mode-0600 JSON file under `/tmp`, refuses to overwrite,
and lists any requested methods missing from the running middleware. Copy the
capture out through an existing trusted channel for review; **do not commit it**.
Running the helper is read-only, but the resulting schema is not proof that any
VM request will be accepted or that a guest can boot.

An operator-provided capture for `TrueNAS-26.0.0-BETA.2` was reduced to a
sanitized fixture at `fixtures/truenas-26.0.0-beta.2-request-schemas.json`.
It contains only accepted-argument constraints for the 19 selected methods;
return schemas, defaults, and descriptive metadata are omitted. The raw capture is not in
the repository. The bounded validator rejects unsupported schema assertions
instead of silently ignoring them.

On a host running that exact build, this optional probe re-reads only
`system.version` and `core.get_methods`, then compares accepted-request schemas:

```sh
python3 planning/verify_compatibility.py
```

A build mismatch stops before querying method schemas. The operator reports this
probe passed on the exact Beta.2 host, matching all 19 selected schemas. A pass
establishes only that the selected request-schema metadata matches this fixture;
it does not invoke those methods, prove TrueNAS will accept a write, or validate
VM boot. Do not use the Beta fixture as evidence for another TrueNAS build.

The examples are fictional. Copy and edit them locally rather than committing
your host's inventory. The preview command reads only the files you explicitly
supply and prints the plan to stdout. Invalid input produces an error on stderr
and exit status 2; a successful preview returns 0. Success is not permission to install.

## Interactive choices (offline preview only)

Run `python3 planning/preview.py --interactive` to collect choices without touching TrueNAS. If `--profile` is omitted, the numbered menu lists Ubuntu Server, Ubuntu Desktop, Debian Server, and Debian Desktop; all are labeled experimental. `--profile ubuntu-server` (or another listed ID) selects a profile without showing the profile menu. The same `--profile` option overrides the profile in `--config` mode.

The prompts collect a VM name, storage pool, existing network bridge, total vCPUs, RAM MiB, and virtual-disk size GiB. With `--inventory`, pools and bridges are shown as numbered choices from that supplied snapshot; the inventory can be stale. Without it, the pool and bridge are typed manually and the preview marks them unchecked.

Storage selection is by **ZFS pool**, not an individual physical drive: the proposed path is `<pool>/linux-vms/<name>/disk0`. The future installer must create a per-VM dataset/zvol only after a fresh live preflight confirms the selected pool is healthy, writable, and has sufficient capacity. Network selection is an **existing bridge**; this planner will not create or alter bridges, VLANs, routes, or host networking. It does not set a guest IP or validate guest networking. The plan keeps the VM stopped and autostart disabled. CPU, RAM, and disk bounds are planner input limits, not host-capacity or guest-support guarantees; this remains a JSON preview, not an installer.

## Config format

All seven fields in `examples/vm.json` are required; unknown fields are rejected.
There are no credential fields. Profile IDs are `ubuntu-server`, `ubuntu-desktop`,
`debian-server`, and `debian-desktop`; all are experimental labels, with no image
versions or guest provisioning support implied.

Names, pools and bridges use a conservative subset: 1–63 ASCII characters,
starting with a letter, followed by letters, digits, underscores or hyphens.
Some valid TrueNAS identifiers may therefore be rejected. CPU, RAM and disk
bounds are planner policy, not validated guest requirements or host capacity.
CPU count means total virtual CPUs, modeled as one socket with that many cores
and one thread per core. It is not yet a TrueNAS API payload.

## Optional inventory format

`examples/inventory.json` is a **normalized snapshot format**, not a raw TrueNAS
API response. All four fields are required:

- `vm_names`: all existing VM names.
- `datasets`: all existing dataset and zvol paths, including descendants.
- `bridges`: existing bridges available for selection.
- `pools`: pool names mapped to objects with integer `available_bytes`.

An empty or malformed inventory is rejected. An omitted inventory is allowed,
but explicitly marks conflict, bridge and free-space checks as unchecked.
Provided snapshots may be stale or incomplete. The planner checks proposed
disk size against the supplied available bytes, excluding installation-image,
snapshot, reservation and filesystem overhead. It does not check parent dataset
type, encryption, quotas, permissions, bridge usability, or available CPU/RAM.

The proposed layout is `<pool>/linux-vms/<name>/disk0`. Existing target datasets
or descendants are rejected. An existing `linux-vms` parent can be shown in the
plan, but a future installer must verify its suitability before creating children.

## Remaining installation gates

1. Integrate and review the unpublished installer source.
2. Run the read-only build/schema compatibility probe on the intended host; any build without a reviewed fixture remains unchecked.
3. Select official images with trusted integrity metadata and distro-specific provisioning.
4. Revalidate live names, storage, networking and host capacity immediately before creation.
5. Implement explicit confirmation, durable ownership manifests and conservative recovery.
6. Validate each profile's boot and first boot in a disposable VM with separate authorization.

Mocked/offline tests do not establish live TrueNAS compatibility.
