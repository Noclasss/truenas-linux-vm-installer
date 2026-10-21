# Ubuntu Server install boundary — offline review

**Status:** design and mock-only contract; no production writer exists. `LIVE_PROFILE_ALLOWLIST` remains empty. This document does not authorize TrueNAS writes or a VM boot.

**Schema scope:** sanitized request/return constraints captured for `TrueNAS-26.0.0-BETA.2` in `planning/fixtures/truenas-26.0.0-beta.2-method-contracts.json`. The operator's 2026-10-21 probe checked metadata using only `system.version` and `core.get_methods`; it did not test write acceptance.

## Proposed method boundary

The following is the complete candidate write set for a future Ubuntu Server create-only flow. It is exercised only by `MockMiddleware`; none of these methods is enabled for a live profile:

- `pool.dataset.create`
- `vm.create`
- `vm.device.create`

Explicitly outside this boundary: `vm.start`, `vm.stop`, `vm.device.delete`, dataset/VM deletion, host networking changes, and automatic rollback. A VM remains stopped and `autostart` remains false. Do not add a method to a live allowlist without a separately reviewed exact-build contract and authorization.

A future live adapter must revalidate the selected caller-supplied pool and bridge with read-only methods (`pool.query`, `interface.query` and/or `vm.device.nic_attach_choices`) without choosing a substitute. Before creation, it must read `pool.dataset.query` and `vm.query`; after creation, it must read back exact dataset, VM, and device targets using `pool.dataset.query`, `vm.query`, and `vm.device.query`. These are design candidates from the Beta.2 fixture, not calls made by the mock's inventory checks. Re-query exact collision targets immediately before their writes; a stale inventory is not sufficient.

## Exact mock write requests

`planning/test_mock_install.py` asserts the complete request sequence and payloads. Each request is also validated against the sanitized Beta.2 request schema by the fake middleware.

1. Create the shared filesystem parent only if absent:
   `pool.dataset.create([{"name": "<pool>/linux-vms", "type": "FILESYSTEM"}])`
2. Create the per-VM filesystem parent:
   `pool.dataset.create([{"name": "<pool>/linux-vms/<vm>", "type": "FILESYSTEM"}])`
3. Create the VM disk zvol:
   `pool.dataset.create([{"name": "<pool>/linux-vms/<vm>/disk0", "type": "VOLUME", "volsize": <disk_gib> * 1024**3, "sparse": true}])`
   Ancestors are explicit; never request `create_ancestors`.
4. Create the VM:
   `vm.create([{ "name": <vm>, "memory": <memory_mib>, "vcpus": <vcpus>, "bootloader": "UEFI", "enable_secure_boot": false, "autostart": false, "ensure_display_device": true, "description": "EXPERIMENTAL MOCK ONLY - not a live installer VM" }])`
5. Attach devices, one request at a time with a manifest update after each acknowledged response:
   - Disk: `vm.device.create([{ "vm": <vm_id>, "attributes": {"dtype": "DISK", "path": "/dev/zvol/<pool>/linux-vms/<vm>/disk0", "type": "VIRTIO"} }])`
   - NoCloud seed: `vm.device.create([{ "vm": <vm_id>, "attributes": {"dtype": "CDROM", "path": "mock://seed/cidata.iso"} }])`
   - Network: `vm.device.create([{ "vm": <vm_id>, "attributes": {"dtype": "NIC", "nic_attach": <caller-selected bridge>, "type": "VIRTIO"} }])`

The fake models the middleware-created DISPLAY device requested by `ensure_display_device`; it is recorded and included in read-back. Whether the selected TrueNAS build creates and reports that device exactly this way is unverified. The mock CDROM URI is deliberately nonfunctional and must never be used live. A real seed path, storage visibility, sparse-zvol policy, and all write acceptance remain unresolved.

## Preflight and conflicts

Before any write, a future live implementation must fail closed unless all of these hold:

- The user explicitly selected the experimental Ubuntu Server profile, valid VM name, pool, bridge, CPU/RAM/disk settings, and verified seed artifact. No automatic pool/bridge selection or host discovery.
- A fresh read confirms the selected pool is healthy and has sufficient capacity for the chosen policy, and the selected bridge is still an eligible VM attachment target.
- No VM with the requested name exists (case-insensitive comparison), and no exact, ancestor, descendant, or wrong-type dataset collision exists at the intended paths.
- The private operation manifest is exclusively created before the first write. It records the entire plan and ownership intent; an existing manifest is never overwritten.
- Shared parent ownership is explicit: reuse an existing filesystem parent without claiming it; if absent, create and record it as an individually owned resource before creating descendants.
- Recheck the exact resource immediately before each create to reduce races. Any conflict stops the sequence; never adopt, overwrite, or delete an unexpected object.

The current mock tests cover supplied inventory, pool health/capacity, duplicate VM and dataset conflicts, explicit parent ordering, exclusive mode-0600 manifest creation, and exact write payloads. They do not perform TrueNAS queries or prove host capacity.

## Acknowledgement, read-back, and interruption policy

- Persist the planned operation before writes. After each acknowledged create, record its exact ID, type/attributes, and ownership in the manifest before proceeding.
- Read back all created datasets/zvol properties, VM configuration and stopped state, and every attached device; compare exact identifiers and requested attributes. A mismatch stops the run.
- If a write may have applied but its response or local manifest update is lost, do not retry, resume, or automatically clean up. Mark `reconcile_required` where possible, retain the manifest, and require manual review.
- The mock-only `inspect_mock_reconciliation()` performs read-only queries against the exact `MockMiddleware` type. It reports recorded-state mismatches and untracked partial resources; its disposition is always `manual_review_required`. It cannot be passed a production adapter and offers no resume/delete operation.
- A consistent partial manifest is not permission to continue. An untracked resource is not permission to delete it. Human reconciliation is required for every ambiguous live write.
- Do not start the VM in this flow. Disposable-VM boot and NoCloud first-boot tests are a separate milestone and require separate approval.

## Evidence and remaining gates

- Offline tests assert the full mock write method sequence, exact request payloads, schema validation, acknowledged read-back, no `vm.start`/delete, and reconciliation of lost dataset, VM, and device responses.
- Reconciliation tests verify that it makes only `pool.dataset.query`, `vm.query`, and `vm.device.query` calls and does not add writes; they cover both a newly created shared parent and a pre-existing shared parent that must not be claimed.
- `LIVE_PROFILE_ALLOWLIST` remains empty. All four distro profiles remain experimental.
- Before any live writer: separately review a current exact-build capture, replace mock-only paths/values with verified live artifact paths, review fresh query semantics and capacity policy, test the real read-only adapter, obtain explicit authorization, and validate only in an isolated disposable environment. No live write was part of this review.
