# Implementation Roadmap

**Current state (2026-10-21):** an experimental offline planner, tested reusable safety helpers, Ubuntu Server artifact/NoCloud preparation, and mock-only orchestration with an in-memory fresh-zvol image-import model. There is no live installer. No Linux VM has been created by this repository.

**GitHub status verified 2026-10-21:** PR #3 was squash-merged into `feat/multi-distro-installer` as `4ddf0b79a9c62658fc6aeabe88b1adafe860d004`; PR #2 was then squash-merged into `main` as `f40ca4c8cd520a6cbae0d9732cb43e9d59a16dc4`; documentation PR #5 was merged as `5393a2fea8d32fe896bfcc54163183fd4834c074`, the current `main` head. Latest main push run `39656161788` succeeded; hosted test count is unknown. PR #6 is open from `feat/mock-fresh-zvol-import` against `main`; its 142-test offline suite passes, but hosted PR checks have not reported a result yet. PR #4 remains open/draft and superseded by #5. None of this establishes live TrueNAS behavior.

## GitHub tracking milestones

- **#1 Offline Mock Review and CI** — assigned to PR #6; no due date. The open PR contains only mock/offline changes and awaits review/hosted checks.
- **#2 Disposable Validation and Profile Support** — assigned to issue #1; no due date. Requires separate authorization and an isolated disposable target; no live operations are currently authorized.

## Guardrails

- PR #3 and PR #2 are merged. Continue to treat the project as experimental; merging code is not live-install or guest-validation evidence. Any future live operation still requires separate authorization and an isolated disposable target.
- Keep Ubuntu Server, Ubuntu Desktop, Debian Server, and Debian Desktop experimental until each has its own supported-version disposable-VM boot and first-boot evidence.
- Offline work must not create TrueNAS resources, alter host networking, or change existing VMs, storage, or devices.
- Any live validation must be separately authorized, isolated, and use no production credentials.

## What exists

| Area | Current evidence | Limit |
|---|---|---|
| Offline planner | Config validation, optional caller-supplied inventory checks, interactive profile/resource prompts, JSON preview. | Does not query TrueNAS; inventory may be stale. |
| Reusable safety core | Mock-tested bounded `midclt` wrapper, positional schema checks, private/atomic files, HTTPS downloads with size and SHA-256 checks. Missing artifact caches are created mode 0700; existing caches must already be mode 0700 and are never chmodded. Read and write allowlists default to empty. | Not connected to an install flow. |
| Ubuntu Server preparation | Pinned official Ubuntu 26.04 LTS amd64 cloud image has recorded exact-size/SHA-256 and QCOW2 inspection evidence. Offline helper now reuses the pinned verifier, validates source metadata with bounded `qemu-img`, creates/verifies a new raw output, and cleans its temporary file. Offline NoCloud seed helper built and inspected a local CIDATA ISO using a synthetic test key; it publishes mode-0600 media without changing the selected output directory's permissions. | Helper tests are mocked; no TrueNAS import, zvol population, boot, or first-boot validation. |
| Mock orchestration | Stateful fake rehearses the candidate write sequence, including an in-memory `vm.device.convert` model only for a fresh run-owned zvol. It validates the captured request schema, enforces the synthetic source size floor, records pending/acknowledged manifest state, and reconciles ambiguous outcomes as manual-review-only without retry. | Fake-only; no `midclt`, downloads, actual conversion, host storage writes, or live resource creation. |
| TrueNAS compatibility | Sanitized request and return schema constraints for 20 selected methods from `TrueNAS-26.0.0-BETA.2`; bounded fail-closed validator and optional read-only comparison probe. Operator-provided output for commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9` says all 20 contracts matched using only `system.version` and `core.get_methods`, with no writes. | Schema matches do not prove write acceptance, runtime resource behavior, or guest boot. |
| Read-only preflight | `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` verify caller-selected pool, bridge, VM name, resource limits, and exact-build compatibility using only allowlisted reads; reserves are explicit and no resource is selected automatically. Fake-middleware tests cover success and fail-closed gates. | Exact Beta.2 `core.get_methods` metadata describes available memory and pool free space in bytes and VM requested memory in MiB; the fixture pins these units to the build and the preflight fails closed if any are absent or mismatched. Exercised only against fake middleware; no TrueNAS preflight has been run. A pass is not write authorization. |
| CI | Current `main` `5393a2fea8d32fe896bfcc54163183fd4834c074` passed latest push run [39656161788](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39656161788); hosted test count unknown. PR #6's branch passed 142 local tests, compile/CLI/preview checks, `git diff --check`, and targeted secret/private scan; hosted PR checks have not reported a result yet. | CI and offline tests do not establish live TrueNAS compatibility or guest behavior. |

## Next work, in order

### 1. Offline image staging and conversion — implemented

`planning/ubuntu_server.py` exposes `stage_ubuntu_server_raw_image(cache_dir, output_path)`. The helper explicitly downloads through the shared exact-size/SHA-256 verifier, inspects the source with bounded `qemu-img info`, rejects unexpected QCOW2 format, virtual size, backing file, or dirty flag, and converts to a raw image only after those checks. It validates converted format/virtual size, refuses pre-existing outputs (including races during publication), uses no shell, and removes its own temporary output on failure. It is local artifact preparation only: there is no TrueNAS import/zvol path, and mocked tests do not establish compatibility or guest boot. It requires `qemu-img`; it does not install tools. All profiles remain experimental.

**Done when:** offline tests cover success, failed verification, unexpected source/output metadata, subprocess failure/timeout, existing output and cleanup; planner docs describe the helper and its limits.

### 2. Extend the read-only schema evidence — complete for Beta.2

The earlier operator-provided report records a passing comparison for the 19 contracts selected at that time. After adding the exact-build `vm.device.convert` contract as the 20th fixture entry, the operator reran the probe from commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9`. It passed all 20 request/return contract comparisons on `TrueNAS-26.0.0-BETA.2`; only `system.version` and `core.get_methods` were called, with no write methods. Keep raw host captures outside Git. This result is specific to the exact build and does not prove write acceptance or runtime behavior.

**Done when:** the exact-build comparison has an attributable operator result for all 20 fixture contracts. Completed for Beta.2 on 2026-10-21; repeat with a separately reviewed fixture if TrueNAS changes build.

### 3. Review the install boundary before any live writer — offline review complete

Keep `LIVE_PROFILE_ALLOWLIST` empty. The offline contract in `docs/ubuntu-server-install-boundary.md` records the candidate create-only method set and exact mock payloads, preflight/conflict requirements, ownership rules, read-back expectations, and interruption policy. `planning/mock_install.py` now models `vm.device.convert` only in memory, after creating a fresh run-owned zvol and before VM creation. It uses a fixed synthetic source/destination, verifies a minimum zvol size, persists pending/acknowledged state, and treats lost responses as manual-review-only. No `qemu-img`, host path, TrueNAS transport, or storage write is involved.

**Audit result (2026-10-21):** the mock rehearsal now records the shared-parent preflight state before the first write and includes a read-only reconciliation assessment that checks manifest state against fake dataset, VM, and device inventory. The assessment cannot use a production adapter, never retries/resumes/deletes, reports `manual_review_required` for every failed run, and identifies untracked resources after ambiguous dataset, VM, or device writes. Tests verify no new writes during reconciliation and distinguish pre-existing shared parents from resources created ambiguously by the run.

**Offline gate:** complete for the mock-only design and tests. `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` now provide an exact-build, read-only preflight prototype. It accepts explicit caller-selected pool/bridge/resource values, validates pool and VM/device/dataset state, and uses only the reviewed read methods. The exact Beta.2 `core.get_methods` capture describes available memory and pool free space in bytes and VM requested memory in MiB; the sanitized fixture pins these units to the build and the preflight fails closed if the evidence is absent or mismatched. It has not been run against TrueNAS. The current live profile allowlist remains empty; no live writer, TrueNAS resource creation, or VM start was added.

**Import-path investigation:** the exact-build fixture and operator's 20-contract probe cover the `vm.device.convert` schema only; the TrueNAS method was not invoked. Separately, the official middleware source at tag `TS-26.0.0-BETA.2`, commit `69e77a6f2e089d392db6e4a3df146bcbe8fba98e`, was inspected offline. The mock now rehearses sequence/ownership/error bookkeeping, but not image conversion. Host path visibility/permissions, seed-media accessibility, sparse-zvol policy, authorization, write acceptance, successful population, and partial-failure behavior remain unverified.

**Remaining before any live writer:** resolve seed-media accessibility and sparse-zvol policy; verify host path visibility/permissions and real write/read-back semantics without assuming mock modeling or source review proves runtime behavior. No live preflight has been run. Any live write/boot test requires separate authorization and an isolated disposable target. All four distro profiles remain experimental.

### 4. Validate Ubuntu Server in a disposable environment

Only after separate approval and isolation/capacity checks, validate image import, zvol population, VM definition, console, networking, and NoCloud first boot on a disposable target. Capture reproducible evidence without production credentials or changes to existing resources.

**Done when:** the exact TrueNAS build, resource settings, test result, and limitations are documented, and any failure leaves existing host resources untouched.

### 5. Expand one profile at a time

Research and implement Ubuntu Desktop, Debian Server, and Debian Desktop separately. For each, pin an official artifact and integrity data, verify its boot and provisioning model, add offline tests, then repeat disposable validation. Do not infer that one distro or edition works because another does.

## Evidence and scope notes

- TrueNAS 25.04 and later use a versioned JSON-RPC 2.0 WebSocket API; TrueNAS 26 removes the REST API. Select and verify contracts for the exact build before considering any write path. A method index or schema match alone is not write-acceptance evidence.
- The Beta.2 fixture contains 20 selected method contracts and omits the raw host capture. The operator-provided output for commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9` reports a passing comparison of all 20 contracts. Do not use this fixture as evidence for another build.
- Image checksum/QCOW2 checks, scratch conversion, and a local NoCloud ISO build establish artifact and helper properties only. They do not establish TrueNAS import behavior, device attachment, boot, networking, or guest provisioning.
- Planner bounds and mocked tests are not host-capacity checks or permission to install.
- Preserve the upstream MIT license and attribution for the adapted safety components. Keep the adaptation boundary documented in `docs/installer-core-provenance.md`.

## References

- [TrueNAS 26 API reference](https://www.truenas.com/docs/scale/26/api/)
- [TrueNAS 25.04.2 `vm.create` schema](https://api.truenas.com/v25.04.2/api_methods_vm.create.html)
- [TrueNAS 25.10 VM method index](https://api.truenas.com/v25.10.0/api_methods_vm.html)
- [Ubuntu pinned cloud-image release](https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/)
- [Ubuntu cloud-image checksum verification](https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/)
- [cloud-init NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html)
- [Canonical `cloud-localds` source](https://github.com/canonical/cloud-utils/blob/main/bin/cloud-localds)
- [Debian cloud overview](https://wiki.debian.org/Cloud)
- [Debian image comparison](https://wiki.debian.org/Cloud/SystemsComparison)
