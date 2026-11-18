# Ubuntu Desktop disposable validation runbook

**State: NOT AUTHORIZED.** This is a planning worksheet, not an executable procedure or permission to mutate TrueNAS. The current preview launcher is offline-only; the create-only writer is not exposed through it and the live-profile allowlist remains empty. Do not substitute manual middleware writes. No live preflight or resource operation has been performed for this runbook.

## 1. Complete the approved target record

Fill every field and have the operator explicitly approve this exact plan before any live operation:

- TrueNAS system/endpoint: `[operator fills in]`
- Exact TrueNAS build: `[operator fills in; must match reviewed runtime contract]`
- Non-production pool: `[operator selects; never choose automatically]`
- Unique VM name: `[operator fills in]`
- New per-run filesystem dataset path and zvol path, plus shared-parent presence: `[operator fills in]`
- vCPU / RAM / host reserve / virtual disk / pool reserve: `[operator fills in]`
- Cache location and its measured available space: `[operator fills in]`
- Ubuntu Desktop ISO version, size, SHA-256, signer fingerprint: `[verify from staged bytes]`
- Explicit installer source ID: `[operator selects after inspecting that ISO]`
- Private NoCloud seed path and SHA-256: `[operator fills in; never record seed contents]`
- Network: `none` for the first attempt; any network requires a separate isolation plan and approval
- Private durable operation-manifest path: `[operator fills in]`
- Exact cleanup scope: `[list only resources permitted for this run]`

Any blank, changed value, or mismatch invalidates the approval; stop and obtain approval for the revised plan.

## 2. Prerequisites — all read-only

1. Confirm authenticated, encrypted access to the exact TrueNAS host, exact build, and the reviewed request *and return* contracts. Do not use HTTP or accept credentials in chat.
2. Confirm the selected non-boot pool is online/healthy and has enough free space for the non-sparse zvol, staged ISO and seed, any same-pool cache copy, and the explicit reserve. Confirm physical and currently available RAM, virtualization support, and vCPU limits with the required host reserve.
3. Query exact VM names, datasets/zvols, and device disk paths. Confirm the requested name/path is absent, no device references the target zvol, and any shared parent already present is recorded as pre-existing and unowned by this run. Confirm the chosen staging location is on the selected pool and safely accessible to the intended VM process.
4. Verify the exact staged ISO against the pinned signature, signer fingerprint, exact size and SHA-256; inspect its source catalogue. Verify seed size, mode `0600`, ownership, and digest. Do not expose the seed or password hash in logs.
5. Create the operation manifest exclusively, with private permissions, before the first TrueNAS write. If any prerequisite is unavailable, stale, unknown, or contradictory, stop. A successful preflight is not authorization.

## 3. Exact permitted scope after approval

The approved plan may allow only these new resources and actions:

- `pool.dataset.create` for the named shared parent only if absent, the exact new per-run filesystem dataset, and its exact new zvol;
- private staging of the named ISO and seed files inside that dataset;
- `vm.create` for the exact unique VM name, with autostart disabled, no NIC, and a stopped final state;
- `vm.device.create` for exactly one loopback-bound DISPLAY device, one disk referencing the exact new zvol, one CD-ROM referencing the verified ISO, and one RAW device referencing the seed.

No other API writes are allowed. In particular: no VM start/stop, delete, retry, resume, rollback, NIC/network/host change, package install, image download, production credential, or change to an existing VM/dataset/device. If the exact reviewed adapter cannot enforce this list, do not run it.

## 4. Ownership, read-back, and stop rules

- Persist the exact pre-write inventory and whether the shared parent existed. Record a pending intent before each write; immediately persist every positive returned VM/device ID (including its VM ID) before response validation or read-back; confirm ownership only after exact identity/property read-back.
- After each create, read back the exact returned ID, name/path, type, size, VM association, device attributes and power state. Keep intended, confirmed and ambiguous resources distinct in the manifest. Never claim a pre-existing parent as owned.
- Stop all writes on timeout, malformed response, failed/missing/mismatched read-back, manifest write failure, unexpected resource/device, changed build/schema, capacity shortfall, or any unrecognized state. Mark the exact pending operation ambiguous when possible. Do not retry, resume, roll back, or delete to “fix” ambiguity; preserve the manifest and use read-only reconciliation with human review.

## 5. Evidence to retain

Keep a private run record containing approval text/scope, operator and timestamp, exact build and contract revision, plan values, preflight outcomes, ISO size/SHA/signature result, seed SHA/size (not contents), manifest path, each write's method and returned ID, exact read-back result, and final VM state. Keep raw middleware responses, credentials, seed contents and password hashes out of chat, Git and public logs. Report separately what is offline-tested, read-only observed, written/read back, and guest-validated; do not infer installation or desktop usability from a created VM.

## 6. Cleanup — separately gated

The current create-only writer has no cleanup path. Do not improvise API deletes or use wildcard, prefix, force, or recursive deletion. Cleanup requires an explicitly approved cleanup scope and a separately reviewed cleanup procedure. Before cleanup, use the manifest plus fresh read-only inventory to match every exact VM/device/resource ID and verify no unrelated VM or device references the zvol. If anything is missing, changed, unowned, ambiguous, or the VM is not already stopped, stop and leave resources intact; this writer must not start or stop it. When a reviewed cleanup path is authorized: delete only the exact run-owned VM with non-forcing options while preserving its zvol; read back VM/device inventory, then remove only any remaining exact target-device IDs with force/raw-file/zvol deletion disabled; verify no device references the zvol; delete only the exact run-owned zvol non-recursively; then remove only empty datasets created by this run non-recursively. Preserve pre-existing parents. Read back absence after each deletion and verify unrelated inventory is unchanged. Record retained artifacts separately; do not delete manifests or source artifacts by default.
