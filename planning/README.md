# Offline VM planner

This independent module provides validation and a reviewable JSON plan for the
future installer. The `preview.py` planner is offline-only: it reads only files
you explicitly pass, makes no middleware calls, and does not write files, create
storage, or start VMs. There is no apply flag. Run it on any development machine
with Python 3.9 or newer; no third-party packages are required.

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
2. Discover and validate live TrueNAS version/API schemas using read-only calls.
3. Select official images with trusted integrity metadata and distro-specific provisioning.
4. Revalidate live names, storage, networking and host capacity immediately before creation.
5. Implement explicit confirmation, durable ownership manifests and conservative recovery.
6. Validate each profile's boot and first boot in a disposable VM with separate authorization.

Mocked/offline tests do not establish live TrueNAS compatibility.
