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
| Reusable safety core | Mock-tested bounded `midclt` wrapper, positional schema checks, private/atomic files, HTTPS downloads with size and SHA-256 checks. Read and write allowlists default to empty. | Not connected to an install flow. |
| Ubuntu Server preparation | Pinned official Ubuntu 26.04 LTS amd64 cloud image has recorded exact-size/SHA-256 and QCOW2 inspection evidence. Offline helper now reuses the pinned verifier, validates source metadata with bounded `qemu-img`, creates/verifies a new raw output, and cleans its temporary file. Offline NoCloud seed helper built and inspected a local CIDATA ISO using a synthetic test key. | Helper tests are mocked; no TrueNAS import, zvol population, boot, or first-boot validation. |
| Mock orchestration | Stateful fake rehearses dataset/zvol, stopped VM, device sequencing, manifest and read-back, using sanitized fixture constraints. Failure cases avoid retry and automatic cleanup. | Fake-only; no `midclt`, downloads, storage writes, or live resource creation. |
| TrueNAS compatibility | Sanitized request and return schema constraints for 19 selected methods from `TrueNAS-26.0.0-BETA.2`; bounded fail-closed validator and optional read-only comparison probe. | Operator report covers the earlier request-only probe. The newer request-plus-return comparison still needs an operator run on that exact build. Schema matches do not prove write acceptance. |
| CI | Hosted run [39598469135](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39598469135) passed all 104 unit tests, compilation, preview, and whitespace checks on code head `083f66793a047caed4158d9ff249c8f77dd527ca`. | A documentation-only follow-up does not change code coverage or establish live compatibility. |

## Next work, in order

### 1. Offline image staging and conversion — implemented

`planning/ubuntu_server.py` exposes `stage_ubuntu_server_raw_image(cache_dir, output_path)`. The helper explicitly downloads through the shared exact-size/SHA-256 verifier, inspects the source with bounded `qemu-img info`, rejects unexpected QCOW2 format, virtual size, backing file, or dirty flag, and converts to a raw image only after those checks. It validates converted format/virtual size, refuses pre-existing outputs (including races during publication), uses no shell, and removes its own temporary output on failure. It is local artifact preparation only: there is no TrueNAS import/zvol path, and mocked tests do not establish compatibility or guest boot. It requires `qemu-img`; it does not install tools. All profiles remain experimental.

**Done when:** offline tests cover success, failed verification, unexpected source/output metadata, subprocess failure/timeout, existing output and cleanup; planner docs describe the helper and its limits.

### 2. Close the read-only schema evidence gap

If the exact Beta.2 host is still available, run `planning/verify_compatibility.py` there. Record the exact build, date, pass/fail, and that the calls were limited to `system.version` and `core.get_methods`. Do not copy a raw host capture or credentials into Git. If the host has moved to another build, capture and review a separate fixture for that build; never generalize the Beta.2 fixture.

**Done when:** the request-and-return schema comparison has an attributable operator result, or the roadmap clearly records that the check remains unavailable.

### 3. Review the install boundary before any live writer

Keep `LIVE_PROFILE_ALLOWLIST` empty. Before implementing a real writer, review exact-build schemas for every method and request, define fresh preflight and duplicate/conflict checks, make each write's acknowledgement/read-back behavior explicit, and document interruption/reconciliation behavior. Any live path must default to stopped VMs and autostart disabled, never delete resources automatically, and operate only on installer-owned paths.

**Done when:** code review confirms explicit method/payload allowlists and tests cover ambiguous failures, partial completion, and safe reconciliation. This gate does not itself authorize a live run.

### 4. Validate Ubuntu Server in a disposable environment

Only after separate approval and isolation/capacity checks, validate image import, zvol population, VM definition, console, networking, and NoCloud first boot on a disposable target. Capture reproducible evidence without production credentials or changes to existing resources.

**Done when:** the exact TrueNAS build, resource settings, test result, and limitations are documented, and any failure leaves existing host resources untouched.

### 5. Expand one profile at a time

Research and implement Ubuntu Desktop, Debian Server, and Debian Desktop separately. For each, pin an official artifact and integrity data, verify its boot and provisioning model, add offline tests, then repeat disposable validation. Do not infer that one distro or edition works because another does.

## Evidence and scope notes

- TrueNAS 25.04 and later use a versioned JSON-RPC 2.0 WebSocket API; TrueNAS 26 removes the REST API. Select and verify contracts for the exact build before considering any write path. A method index or schema match alone is not write-acceptance evidence.
- The Beta.2 fixture omits descriptive metadata and raw host captures. It covers only 19 selected methods and must not be used for another build.
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
