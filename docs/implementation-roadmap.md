# Implementation Roadmap

**Current state (2026-10-21):** an experimental offline planner, tested reusable safety helpers, Ubuntu Server artifact/NoCloud preparation, and a mock-only orchestration rehearsal. There is no live installer. No Linux VM has been created by this repository.

## Guardrails

- Develop on `feat/multi-distro-installer`; keep `main` unchanged.
- Keep PR #2 draft and unmerged until its acceptance gates are complete. Do not release or merge without separate approval.
- Keep Ubuntu Server, Ubuntu Desktop, Debian Server, and Debian Desktop experimental until each has its own supported-version disposable-VM boot and first-boot evidence.
- Offline work must not create TrueNAS resources, alter host networking, or change existing VMs, storage, or devices.
- Any live validation must be separately authorized, isolated, and use no production credentials.

## What exists

| Area | Current evidence | Limit |
|---|---|---|
| Offline planner | Config validation, optional caller-supplied inventory checks, interactive profile/resource prompts, JSON preview. | Does not query TrueNAS; inventory may be stale. |
| Reusable safety core | Mock-tested bounded `midclt` wrapper, positional schema checks, private/atomic files, HTTPS downloads with size and SHA-256 checks. Missing artifact caches are created mode 0700; existing caches must already be mode 0700 and are never chmodded. Read and write allowlists default to empty. | Not connected to an install flow. |
| Ubuntu Server preparation | Pinned official Ubuntu 26.04 LTS amd64 cloud image has recorded exact-size/SHA-256 and QCOW2 inspection evidence. Offline helper now reuses the pinned verifier, validates source metadata with bounded `qemu-img`, creates/verifies a new raw output, and cleans its temporary file. Offline NoCloud seed helper built and inspected a local CIDATA ISO using a synthetic test key; it publishes mode-0600 media without changing the selected output directory's permissions. | Helper tests are mocked; no TrueNAS import, zvol population, boot, or first-boot validation. |
| Mock orchestration | Stateful fake rehearses the complete candidate write sequence, schema-validated exact payloads, explicit dataset/zvol ancestry, stopped VM/device creation, private manifest, read-back, and read-only reconciliation of partial state. Ambiguous writes are not retried or cleaned up; reconciliation is always manual-review only. | Fake-only; no `midclt`, downloads, storage writes, or live resource creation. |
| TrueNAS compatibility | Sanitized request and return schema constraints for 20 selected methods from `TrueNAS-26.0.0-BETA.2`; bounded fail-closed validator and optional read-only comparison probe. Operator-provided output for commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9` says all 20 contracts matched using only `system.version` and `core.get_methods`, with no writes. | Schema matches do not prove write acceptance, runtime resource behavior, or guest boot. |
| Read-only preflight | `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` verify caller-selected pool, bridge, VM name, resource limits, and exact-build compatibility using only allowlisted reads; reserves are explicit and no resource is selected automatically. Fake-middleware tests cover success and fail-closed gates. | Exact Beta.2 `core.get_methods` metadata describes available memory and pool free space in bytes and VM requested memory in MiB; the fixture pins these units to the build and the preflight fails closed if any are absent or mismatched. Exercised only against fake middleware; no TrueNAS preflight has been run. A pass is not write authorization. |
| CI | Prior hosted runs [39622905793](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39622905793) and [39622916219](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39622916219) passed on a previous published commit. The current worktree passes 138 local tests, compilation, CLI help, and whitespace checks; hosted status for the pending fixture update has not yet been recorded. | Hosted checks do not establish live TrueNAS compatibility or guest behavior. |

## Next work, in order

### 1. Offline image staging and conversion — implemented

`planning/ubuntu_server.py` exposes `stage_ubuntu_server_raw_image(cache_dir, output_path)`. The helper explicitly downloads through the shared exact-size/SHA-256 verifier, inspects the source with bounded `qemu-img info`, rejects unexpected QCOW2 format, virtual size, backing file, or dirty flag, and converts to a raw image only after those checks. It validates converted format/virtual size, refuses pre-existing outputs (including races during publication), uses no shell, and removes its own temporary output on failure. It is local artifact preparation only: there is no TrueNAS import/zvol path, and mocked tests do not establish compatibility or guest boot. It requires `qemu-img`; it does not install tools. All profiles remain experimental.

**Done when:** offline tests cover success, failed verification, unexpected source/output metadata, subprocess failure/timeout, existing output and cleanup; planner docs describe the helper and its limits.

### 2. Extend the read-only schema evidence — complete for Beta.2

The earlier operator-provided report records a passing comparison for the 19 contracts selected at that time. After adding the exact-build `vm.device.convert` contract as the 20th fixture entry, the operator reran the probe from commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9`. It passed all 20 request/return contract comparisons on `TrueNAS-26.0.0-BETA.2`; only `system.version` and `core.get_methods` were called, with no write methods. Keep raw host captures outside Git. This result is specific to the exact build and does not prove write acceptance or runtime behavior.

**Done when:** the exact-build comparison has an attributable operator result for all 20 fixture contracts. Completed for Beta.2 on 2026-10-21; repeat with a separately reviewed fixture if TrueNAS changes build.

### 3. Review the install boundary before any live writer — offline review complete

Keep `LIVE_PROFILE_ALLOWLIST` empty. The offline contract in `docs/ubuntu-server-install-boundary.md` records the candidate create-only method set and exact mock payloads, preflight/conflict requirements, ownership rules, read-back expectations, and interruption policy. `planning/mock_install.py` exposes a mock-only write-method set; its fake validates every write against the sanitized Beta.2 request schemas. Tests assert the complete method sequence and payload values.

**Audit result (2026-10-21):** the mock rehearsal now records the shared-parent preflight state before the first write and includes a read-only reconciliation assessment that checks manifest state against fake dataset, VM, and device inventory. The assessment cannot use a production adapter, never retries/resumes/deletes, reports `manual_review_required` for every failed run, and identifies untracked resources after ambiguous dataset, VM, or device writes. Tests verify no new writes during reconciliation and distinguish pre-existing shared parents from resources created ambiguously by the run.

**Offline gate:** complete for the mock-only design and tests. `planning/readonly_preflight.py` and `planning/check_readonly_preflight.py` now provide an exact-build, read-only preflight prototype. It accepts explicit caller-selected pool/bridge/resource values, validates pool and VM/device/dataset state, and uses only the reviewed read methods. The exact Beta.2 `core.get_methods` capture describes available memory and pool free space in bytes and VM requested memory in MiB; the sanitized fixture pins these units to the build and the preflight fails closed if the evidence is absent or mismatched. It has not been run against TrueNAS. The current live profile allowlist remains empty; no live writer, TrueNAS resource creation, or VM start was added.

**Import-path investigation:** the later exact-build capture confirms `vm.device.convert` exists on `TrueNAS-26.0.0-BETA.2`. Its `core.get_methods` metadata says it is a job, requires the `VM_DEVICE_WRITE` role, accepts one object containing required non-empty string `source` and `destination` paths, and returns a boolean. The metadata lists qcow2, qed, raw, vdi, vhdx, and vmdk image formats and says direction is selected automatically from the file extension. Its sanitized schema is now in the fixture and covered by offline request-validation tests. The method was not invoked; exact path interpretation, permissions/visibility, zvol population, and write acceptance remain unverified.

**Remaining before any live writer:** determine exact source/destination direction and host path/permission behavior for conversion; run read-only preflight only on an explicitly selected and approved target; resolve seed-media accessibility and sparse-zvol policy; implement and mock-test image-to-zvol import and real seed/device path; separately review write acceptance and read-back semantics. Any live write/boot test requires separate authorization and an isolated disposable target. All four distro profiles remain experimental.

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
