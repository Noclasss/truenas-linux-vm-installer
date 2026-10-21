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

These helpers are infrastructure only. They are not called by an install flow;
no live installer entry point or guest provisioning path exists. The HAOS release
metadata, HAOS disk/firmware assumptions, defaults, and installer flow were not
copied. Linux artifact URLs, checksums, image formats, boot settings, and
provisioning must be defined and validated per distro profile before reuse.

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
return schemas and descriptive metadata are omitted. The raw capture is not in
the repository. The bounded validator rejects unsupported schema assertions
instead of silently ignoring them.

On a host running that exact build, this optional probe re-reads only
`system.version` and `core.get_methods`, then compares accepted-request schemas:

```sh
python3 planning/verify_compatibility.py
```

A build mismatch stops before querying method schemas. A pass establishes only
that the selected request-schema metadata matches this fixture; it does not
invoke those methods, prove TrueNAS will accept a write, or validate VM boot.
Do not use the Beta fixture as evidence for another TrueNAS build.

The examples are fictional. Copy and edit them locally rather than committing
your host's inventory. The preview command reads only the files you explicitly
supply and prints the plan to stdout. Invalid input produces an error on stderr
and exit status 2; a successful preview returns 0. Success is not permission to install.

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
