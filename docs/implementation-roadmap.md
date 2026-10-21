# Implementation Roadmap (Recovery Baseline)

**Status: experimental offline planner, reusable safety components, and mock-only orchestration rehearsal.** The branch contains no live Linux VM installer. The in-memory rehearsal does not connect to TrueNAS and proves neither write compatibility nor guest behavior.

## Confirmed project constraints

- Work only on `feat/multi-distro-installer`; keep `main` unchanged.
- Keep Ubuntu Server, Ubuntu Desktop, Debian Server, and Debian Desktop marked experimental until each has its own successful disposable-VM boot and first-boot validation.
- No live VM creation, host networking changes, or changes to existing TrueNAS resources during offline development.
- PR #2 already exists as a draft; keep it unmerged and draft while acceptance criteria remain incomplete. No merge or release without separate approval.

## Confirmed technical facts

- TrueNAS 25.04 and later use a versioned JSON-RPC 2.0 WebSocket API; TrueNAS documentation says the REST API was removed in TrueNAS 26. The TrueNAS API client includes `midclt`. The documented API version must be selected for the exact target build; method names alone are not sufficient evidence of accepted payloads.
- The versioned TrueNAS API reference lists `vm.create` and `vm.device.create`, but a method index is not a request contract. The published 25.04.2 `vm.create` schema marks `name` and `memory` required, with `autostart: true` and `ensure_display_device: true` as defaults. Do not assume those defaults or accepted fields are identical on 25.10/26; capture `system.version` and exact `core.get_methods` metadata for the target build before enabling writes. Any future installer must explicitly set `autostart: false` and read back the final device inventory.
- Canonical's official cloud-image catalog listed `release-20261016` as the latest dated Ubuntu 26.04 LTS amd64 Server release observed on 2026-10-21; its manifest and image were last modified on 2026-10-20. The downloaded, pinned image passed exact-size/SHA-256 verification; `qemu-img info` reported QCOW2, virtual size 3,758,096,384 bytes, no backing file, and `dirty-flag: false`, while `qemu-img check` found no errors. A scratch-only QCOW2-to-raw conversion also succeeded (same virtual size, 819,237,376 bytes allocated). The detached manifest signature was checked against key fingerprint `D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81`; the Python verifier did not check key revocation/self-signatures. This does not prove TrueNAS import/write acceptance or guest boot.
- The Ubuntu Server profile has an offline NoCloud bootstrap helper. A real `cloud-localds` run built a local CIDATA ISO with both `user-data` and `meta-data`; the seed was inspected with `isoinfo` and used a synthetic, nonfunctional test public key. TrueNAS `vm.device.create`'s `CDROM` `path` is present in the 26.0.0-BETA.2 accepted-request fixture; write acceptance, attachment, boot, and first-boot behavior remain unverified. Ubuntu autoinstall is not used for this preinstalled cloud image.
- Debian's Cloud Team publishes images for selected environments. Its docs describe a KVM generic image and recommend generic over genericcloud for maximum compatibility, but that is not validation on TrueNAS/KVM. Confirm the exact release, artifact, architecture, boot mode, integrity metadata, cloud-init support, and first-boot flow before implementing a Debian profile.

## Staged work

1. **Recovery baseline:** preserve the offline planner and document verified scope, constraints, and evidence gaps; do not represent this work as recovery of the missing installer.
2. **Ubuntu Server profile research:** the signed-manifest-pinned image is downloaded and inspected as QCOW2, and a real local NoCloud seed ISO has been built; TrueNAS import/write acceptance, VM boot, and first-boot remain unverified.
3. **Reusable installer core:** generic `midclt`, positional-schema validation, private-file, and verified-download components have been adapted from the separate HAOS installer with mocked tests. A sanitized fixture and offline compatibility tests now cover request and return schemas for 19 selected methods captured for `TrueNAS-26.0.0-BETA.2`; an optional read-only probe compares that exact build and both schema classes. The core is not wired to a live install flow. Keep image/provisioning behavior per distro profile.
4. **Plan and preview:** show resource choices without downloading or writing anything. The offline CLI now offers a numbered profile menu plus VM name, storage-pool, existing-bridge, vCPU, RAM, and virtual-disk-size prompts; `--profile` remains an explicit override. Inventory menus use only caller-supplied snapshots. The preview stays static, experimental, stopped, autostart-disabled, and unappliable.
5. **Mocked install orchestration (implemented, mock-only):** `planning/mock_install.py` rehearses Ubuntu Server dataset/zvol, VM, and device sequencing against a stateful in-memory `MockMiddleware`; validates write payloads against the sanitized Beta.2 request schemas; creates an exclusive private local manifest; records middleware-created display devices; and verifies exact fake read-back while leaving the VM stopped. Failure tests retain the manifest, do not retry ambiguous writes, and perform no cleanup. `LIVE_PROFILE_ALLOWLIST` remains empty. The mock does not download/import/populate images, call `midclt`, create a TrueNAS resource, or validate a real VM.
6. **CI and publication:** the existing planner workflow discovers all `planning/test_*.py` files, so the new mock orchestration tests run with the planner, schema-capture, compatibility-fixture, and reusable-core tests. This change does not edit the workflow. Push only the feature branch and verify its SHA before updating the draft PR.
7. **Disposable live validation:** only after separate approval and safe isolation/capacity checks, validate Ubuntu Server boot, console, first-boot provisioning, networking, and access. Do not use production credentials.
8. **Expand profiles one at a time:** add Ubuntu Desktop, Debian Server, then Debian Desktop only after each has a distinct verified artifact/provisioning path and independent tests. Keep all experimental until disposable boot and first-boot evidence exists.

## Evidence gaps / blockers

- The prior live-installer implementation and its local-only commits could not be recovered. The current remote offline planner is preserved and augmented with a read-only schema-capture helper and an unconnected, attributed safety core; none of this is a restoration of the missing installer.
- An operator-provided read-only capture records `TrueNAS-26.0.0-BETA.2`; 19/19 selected method definitions were present. The fixture now retains sanitized request and return schema constraints. The operator's earlier probe report covered request schemas only; the updated return-schema comparison has not yet been run on the host. The current probe still calls only `system.version` and `core.get_methods`; neither comparison establishes write acceptance, runtime response data, or guest behavior.
- No profile has been boot-tested in a disposable VM from this repository.
- The Ubuntu Server artifact's exact bytes and QCOW2 format are verified, and the real local seed-building command has succeeded. The repository still has no live TrueNAS resource-write path, QCOW2 import/population implementation, boot/console workflow, or first-boot evidence; no VM was created. The mock request payloads passing fixture checks is limited schema evidence, not write acceptance.
- `.github/workflows/planner-tests.yml` discovers the full `planning/test_*.py` suite, including the mock-only orchestration tests. This change leaves the workflow unchanged. The previously inspected credential lacked `workflow` scope; reauthorize the existing integration before any future workflow-file change if needed, and do not create a replacement credential.

## Official references

- TrueNAS API overview: https://www.truenas.com/docs/scale/26/api/
- TrueNAS 25.04.2 `vm.create` schema: https://api.truenas.com/v25.04.2/api_methods_vm.create.html
- TrueNAS 25.10 VM method index (names only): https://api.truenas.com/v25.10.0/api_methods_vm.html
- Official Ubuntu cloud images, pinned dated release: https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/
- Ubuntu image checksum/signature verification: https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/
- cloud-init NoCloud datasource: https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html
- Canonical cloud-utils `cloud-localds` source: https://github.com/canonical/cloud-utils/blob/main/bin/cloud-localds
- Ubuntu cloud-init overview: https://ubuntu.com/server/docs/explanation/intro-to/cloud-init/
- Ubuntu automatic Server installation: https://ubuntu.com/server/docs/how-to/installation/
- Ubuntu Desktop autoinstall: https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/
- Debian cloud overview: https://wiki.debian.org/Cloud
- Debian cloud image comparison: https://wiki.debian.org/Cloud/SystemsComparison
- Debian cloud image lifecycle: https://wiki.debian.org/Cloud/ImageLifecycle
