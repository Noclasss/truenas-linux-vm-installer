# Implementation Roadmap

**Current state (2026-10-22):** an experimental offline planner, tested reusable safety helpers, Ubuntu Server artifact/NoCloud preparation, and mock-only orchestration. PR #10 and PR #11 are merged; the latest functional merge commit is `9ef93d5d986545e78f67862a4ba1edc9229ffd0a`. The merged changes include the Beta.2 boolean parser fix, conservative non-sparse zvol policy, simulated seed-ISO ownership reconciliation, and pure dataset-mountpoint path planning. No live writer was added and no Linux VM has been created by this repository.

**GitHub status verified 2026-10-22:** PR #10 was merged by merge commit `61515c2ecbbf530e7cfe860a8e3f1c902c75c462`; PR #11 was squash-merged as `9ef93d5d986545e78f67862a4ba1edc9229ffd0a`. Planner checks passed on both PR heads and on both resulting `main` commits, including final run `39795854209`. These are CI/offline results only and do not establish live TrueNAS behavior.

## GitHub tracking milestones

- **#1 Offline Mock Review and CI** — complete and closed after PR #6 merged and hosted checks passed; no due date.
- **#2 Disposable Validation and Profile Support** — remains open and assigned to issue #1; no due date. Requires separate authorization and an isolated disposable target; no live operations are currently authorized.

## Guardrails

- PR #3, PR #2, PR #5, and PR #6 are merged. Continue to treat the project as experimental; merging code is not live-install or guest-validation evidence. Any future live operation still requires separate authorization and an isolated disposable target.
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
|| TrueNAS compatibility | Sanitized request/return contracts for 20 selected methods on `TrueNAS-26.0.0-BETA.2`; bounded fail-closed validator and read-only comparison probe. Merged PR #10 narrowly handles exact `True`/`False` output for `vm.supports_virtualization` on this build. | Operator-reported contract/preflight results do not prove write acceptance, runtime resource behavior, or guest boot. |
|| Read-only preflight | `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` verify caller-selected pool, bridge, VM name, resource limits, and exact-build compatibility using allowlisted reads; reserves are explicit and no resource is selected automatically. | Operator-provided output from pinned code `9e89be1b932750d3010f40cd68a916b8afd43a70` reports a pass on Beta.2: all 20 contracts and listed pool, bridge, conflict, virtualization/vCPU, and memory checks passed. Only read methods ran; no resources were created. This is not independently reproduced or write authorization. |
|| CI | PR #10 head `0454441ece4ddb0894745b82d12d93ee060756d5` passed run [39775195621](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39775195621), and merge commit `61515c2ecbbf530e7cfe860a8e3f1c902c75c462` passed push run [39794106338](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39794106338). PR #11 head `9a4bf1dee6564487634d5cdbe74729b48f71ce40` passed run [39791943299](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39791943299), and final `main` commit `9ef93d5d986545e78f67862a4ba1edc9229ffd0a` passed push run [39795854209](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39795854209). | Hosted checks and offline tests do not establish live TrueNAS compatibility or guest behavior. |

## Next work, in order

### 1. Offline image staging and conversion — implemented

`planning/ubuntu_server.py` exposes `stage_ubuntu_server_raw_image(cache_dir, output_path)`. The helper explicitly downloads through the shared exact-size/SHA-256 verifier, inspects the source with bounded `qemu-img info`, rejects unexpected QCOW2 format, virtual size, backing file, or dirty flag, and converts to raw only after those checks. It validates converted format/virtual size, requires a lowercase `.raw` suffix for the Beta.2 converter, refuses pre-existing outputs (including races during publication), uses no shell, and removes its temporary output on failure. It is local artifact preparation only: no TrueNAS import/zvol path exists. It requires `qemu-img`; it does not install tools. All profiles remain experimental.

**Done when:** offline tests cover success, failed verification, unexpected source/output metadata, subprocess failure/timeout, existing output and cleanup; planner docs describe the helper and its limits.

### 2. Extend the read-only schema evidence — complete for Beta.2

The fixture contains 20 exact-build request/return contracts, including `vm.device.convert`. The operator-reported probe from `TrueNAS-26.0.0-BETA.2` matched all 20 using only `system.version` and `core.get_methods`; no write methods ran. Keep raw host captures outside Git. This result is specific to the exact build and does not prove write acceptance or runtime behavior.

**Done when:** the exact-build comparison has an attributable operator result for all 20 fixture contracts. Completed for Beta.2 on 2026-10-22; repeat with a separately reviewed fixture if TrueNAS changes build.

### 3. Review the install boundary before any live writer — offline review complete

Keep `LIVE_PROFILE_ALLOWLIST` empty. The offline contract in `docs/ubuntu-server-install-boundary.md` records the candidate create-only method set and exact mock payloads, preflight/conflict requirements, ownership rules, read-back expectations, and interruption policy. `planning/mock_install.py` models `vm.device.convert` and the possible seed ISO owner transfer only in memory. It uses fixed synthetic identifiers, requires a fresh run-owned zvol, persists pending/acknowledged states, and reconciles ambiguous attachment without retry. These simulations do not invoke `qemu-img`, access host paths, connect to TrueNAS, or establish live behavior.

**Audit result (2026-10-21):** the mock rehearsal now records the shared-parent preflight state before the first write and includes a read-only reconciliation assessment that checks manifest state against fake dataset, VM, and device inventory. The assessment cannot use a production adapter, never retries/resumes/deletes, reports `manual_review_required` for every failed run, and identifies untracked resources after ambiguous dataset, VM, or device writes. Tests verify no new writes during reconciliation and distinguish pre-existing shared parents from resources created ambiguously by the run.

**Seed ownership and staging-path model (2026-10-22):** the fake CDROM attach models a root-to-libvirt owner change; its manifest tracks the pre-attach, pending, and acknowledged states, and reconciliation detects a transfer after a simulated lost response. `ubuntu_server_stage_paths_from_dataset_rows()` derives fixed `.raw` and NoCloud ISO paths only from one exact filesystem dataset query row with a canonical mountpoint beneath `/mnt` and below the pool-root dataset. This is path-planning logic only: it does not query TrueNAS or establish mount visibility, permissions, conversion, or CDROM access.

**Offline gate:** complete for the mock-only design and tests. `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` now provide an exact-build, read-only preflight prototype. It accepts explicit caller-selected pool/bridge/resource values, validates pool and VM/device/dataset state, and uses only the reviewed read methods. The exact Beta.2 `core.get_methods` capture describes available memory and pool free space in bytes and VM requested memory in MiB; the sanitized fixture pins these units to the build and the preflight fails closed if the evidence is absent or mismatched. On 2026-10-22, operator-provided output from the checkout pinned to `9e89be1b932750d3010f40cd68a916b8afd43a70` reports a successful read-only preflight on `TrueNAS-26.0.0-BETA.2`, matching 20 reviewed request/return contracts and passing the listed pool, bridge, collision, virtualization/vCPU, and memory checks. No write methods were called and no resources were created. This is operator-provided evidence, not independently reproduced; it does not establish write acceptance or guest boot. The current live profile allowlist remains empty; no live writer, TrueNAS resource creation, or VM start was added.

**Import-path investigation:** PR #11 records the exact Beta.2 `vm.device.convert` constraints, models a non-sparse zvol with full disk capacity reserved, enforces lowercase `.raw` staging, and documents the CDROM delegate's possible seed-ISO ownership transfer. The offline model now records that possible mutation and detects it in an ambiguous-response simulation. Neither conversion nor CDROM creation has been invoked on TrueNAS. Actual host path/permission behavior, ownership changes, authorization, write acceptance, successful population, and partial-failure behavior remain unverified; no live zvol has been created to verify allocation behavior.

**Remaining before any live writer:** integrate the exact child-dataset mountpoint read-back into an install plan and stage the verified image/seed only beneath that path; add real file-owner and libvirt traversal/read-access checks; validate conversion, device attachment, and read-back on an authorized disposable target. The new helpers and fake only define offline contracts. Source review and the passing read-only preflight do not replace these gates. Any TrueNAS write/boot test still requires separate authorization and an isolated disposable target. All four distro profiles remain experimental.

### 4. Validate Ubuntu Server in a disposable environment

Only after separate approval and isolation/capacity checks, validate image import, zvol population, VM definition, console, networking, and NoCloud first boot on a disposable target. Capture reproducible evidence without production credentials or changes to existing resources.

**Done when:** the exact TrueNAS build, resource settings, test result, and limitations are documented, and any failure leaves existing host resources untouched.

### 5. Expand one profile at a time

Research and implement Ubuntu Desktop, Debian Server, and Debian Desktop separately. For each, pin an official artifact and integrity data, verify its boot and provisioning model, add offline tests, then repeat disposable validation. Do not infer that one distro or edition works because another does.

## Evidence and scope notes

- TrueNAS 25.04 and later use a versioned JSON-RPC 2.0 WebSocket API; TrueNAS 26 removes the REST API. Select and verify contracts for the exact build before considering any write path. A method index or schema match alone is not write-acceptance evidence.
- The Beta.2 fixture contains 20 selected method contracts and omits the raw host capture. Operator-provided output from code commit `9e89be1b932750d3010f40cd68a916b8afd43a70` reports a passing read-only preflight on 2026-10-22. Do not use this fixture as evidence for another build.
- Image checksum/QCOW2 checks, scratch conversion, and a local NoCloud ISO build establish artifact and helper properties only. Beta.2 source review shows CDROM validation can chown the ISO to the libvirt service user, but does not establish that this path is readable or correctly attached on the target host. No import, device attachment, boot, networking, or guest provisioning has been validated live.
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
