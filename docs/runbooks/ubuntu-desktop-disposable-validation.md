# Ubuntu Desktop disposable validation runbook

**State: NOT AUTHORIZED.** This is a planning worksheet, not an executable procedure or permission to mutate TrueNAS. The current preview launcher is offline-only; the create-only writer is not exposed through it and the live-profile allowlist remains empty. Do not substitute manual middleware writes. No live preflight or resource operation has been performed for this runbook.

## Current offline evidence — 2026-11-19

The selected Ubuntu 26.04.1 Desktop ISO was verified locally with genuine GnuPG 2.4.7 against the official Ubuntu checksum signature and the pinned signing-key fingerprint. The exact size and SHA-256 matched the repository pin. The repository's bounded 7-Zip fallback extracted `casper/install-sources.yaml` from those same bytes; `ubuntu-desktop-minimal` is listed as the default “minimal but usable” Desktop source. The ISO digest was identical before and after extraction. Reproducible tool provenance, exact hashes, and catalogue evidence are in [artifact verification](../desktop-artifact-verification.md). This does not prove installation, offline completion, boot, display, or login.

**Offline code verification (2026-11-19):** clean checkout of code commit `24cef09dc56f2851c5793a212a1d787a42225ef2` passed the full offline suite (301 tests, 0 skipped, 0 failures) and smoke checks. The real disposable-signature test ran and passed; its skip guard requires `gpg` and `gpgconf` on the suite process's `PATH`, which were supplied from the isolated helper directory. Artifact verification separately used the isolated GnuPG verifier and 7-Zip 25.01, confirmed the pinned signature and ISO bytes, and found `ubuntu-desktop-minimal` as the default catalogue entry. The smoke report's `actual_iso_verified: false` means smoke does not perform the separate ISO check. No host preflight, seed staging, middleware write, or VM operation occurred. This remains offline evidence only.

## Current host gate — not passed

Available read-only diagnostics are partial: they do not establish the exact host build and method contracts, current CPU/memory headroom, bridge state, VM/device/zvol collision inventory, or service-account access to staged media. The helper's SSH attempt was denied, and no approved administrative operator route was available in-session. Do not reuse historical capacity/path snapshots as fresh preflight evidence. The exact target record and private NoCloud seed remain unprepared until the target identity and a private credential-handling path are established. Do not stage ISO/seed files, invoke `create`, or make resource changes as part of this runbook.

## Offline code snapshot — not live evidence

The returned-ID reconciliation code and regression coverage are present in PR #44 at reviewed source commit `352718c7cb8256387188c524db22936ba2c41c23`. On that exact head, the local offline smoke ran 269 tests with one GnuPG-dependent skip; its status was `incomplete` and exit code 1 because `gpg`, `gpgv`, and `gpgconf` were unavailable. All other smoke checks passed. GitHub Actions run [44421941111](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/44421941111) passed on that exact SHA. The unit tests use mocks; neither this evidence nor this worksheet demonstrates live write acceptance, ISO verification/staging, host preflight, VM creation, or guest behavior. Re-check the exact PR head and its tests before relying on this code; newer code changes require fresh evidence.

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
