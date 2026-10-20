# Implementation Roadmap (Recovery Baseline)

**Status: planning and research only.** The remote feature branch currently contains documentation only. This roadmap is a fresh recovery baseline; it is not the missing prepared implementation and does not prove any installer behavior.

## Confirmed project constraints

- Work only on `feat/multi-distro-installer`; keep `main` unchanged.
- Keep Ubuntu Server, Ubuntu Desktop, Debian Server, and Debian Desktop marked experimental until each has its own successful disposable-VM boot and first-boot validation.
- No live VM creation, host networking changes, or changes to existing TrueNAS resources during offline development.
- No PR until implementation, tests, and CI workflow are present and verified. No merge or release without separate approval.

## Confirmed technical facts

- TrueNAS 25.04 and later use a versioned JSON-RPC 2.0 WebSocket API; TrueNAS documentation says the REST API was removed in TrueNAS 26. The TrueNAS API client includes `midclt`. The documented API version must be selected for the exact target build; method names alone are not sufficient evidence of accepted payloads.
- The versioned TrueNAS API reference lists `vm.create` and `vm.device.create`, but a method index is not a request contract. The published 25.04.2 `vm.create` schema marks `name` and `memory` required, with `autostart: true` and `ensure_display_device: true` as defaults. Do not assume those defaults or accepted fields are identical on 25.10/26; capture `system.version` and exact `core.get_methods` metadata for the target build before enabling writes. Any future installer must explicitly set `autostart: false` and read back the final device inventory.
- Ubuntu documents cloud-init provisioning and describes autoinstall as available for Ubuntu Desktop and Server. That establishes a configuration mechanism, not a TrueNAS-specific boot route or validated image workflow. Test each edition independently instead of assuming an image is bootable or unattended.
- Debian's Cloud Team publishes images for selected environments. Its docs describe a KVM generic image and recommend generic over genericcloud for maximum compatibility, but that is not validation on TrueNAS/KVM. Confirm the exact release, artifact, architecture, boot mode, integrity metadata, cloud-init support, and first-boot flow before implementing a Debian profile.

## Staged work

1. **Repository recovery baseline (this commit):** record the verified scope, constraints, and evidence gaps in version control.
2. **Research the first profile:** select a pinned Ubuntu Server artifact and a provisioning route; document checksum/signature verification, boot requirements, seed delivery, and first-boot success criteria.
3. **Read-only compatibility layer:** use local `midclt` for host-side operations; detect build/version, enumerate supported methods, pools, capacity, VM state, and NIC choices. Fail closed when required data or schemas are unknown.
4. **Plan and preview:** validate inputs and show a complete resource plan without downloading or writing anything. Add tests asserting preview makes zero write calls.
5. **Mocked install orchestration:** implement Ubuntu Server only behind an empty-by-default live-profile allowlist, explicit user confirmation, exclusive manifest creation, and exact ownership checks. Test failures, timeouts, and ambiguous writes without TrueNAS.
6. **CI and publication:** add unit tests, Python compile, shell syntax, secret scan, and diff checks to a workflow; verify workflow-file authorization before publishing it. Push only the feature branch and verify its SHA before considering a draft PR.
7. **Disposable live validation:** only after separate approval and safe isolation/capacity checks, validate Ubuntu Server boot, console, first-boot provisioning, networking, and access. Do not use production credentials.
8. **Expand profiles one at a time:** add Ubuntu Desktop, Debian Server, then Debian Desktop only after each has a distinct verified artifact/provisioning path and independent tests. Keep all experimental until disposable boot and first-boot evidence exists.

## Evidence gaps / blockers

- The previous local implementation and its commit objects are not present in this environment. Only the README can be recovered from GitHub; rebuilding must not be represented as restoring the lost work.
- The actual TrueNAS versioned VM method contracts have not been captured for the target build. General API references are research leads, not live schema validation.
- No profile has been boot-tested in a disposable VM from this repository.
- GitHub authorization previously reported no `workflow` scope. Do not create a replacement credential. Reauthorize the existing integration with workflow-file permission before publishing `.github/workflows/ci.yml`.

## Official references

- TrueNAS API overview: https://www.truenas.com/docs/scale/26/api/
- TrueNAS 25.04.2 `vm.create` schema: https://api.truenas.com/v25.04.2/api_methods_vm.create.html
- TrueNAS 25.10 VM method index (names only): https://api.truenas.com/v25.10.0/api_methods_vm.html
- Ubuntu cloud-init overview: https://ubuntu.com/server/docs/explanation/intro-to/cloud-init/
- Ubuntu automatic Server installation: https://ubuntu.com/server/docs/how-to/installation/
- Ubuntu Desktop autoinstall: https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/
- Official Ubuntu cloud images: https://cloud-images.ubuntu.com/
- Debian cloud overview: https://wiki.debian.org/Cloud
- Debian cloud image comparison: https://wiki.debian.org/Cloud/SystemsComparison
- Debian cloud image lifecycle: https://wiki.debian.org/Cloud/ImageLifecycle
