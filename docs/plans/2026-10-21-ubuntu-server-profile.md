# Ubuntu Server Profile Implementation Plan

> **Historical plan:** written before PRs #3 and #2 were explicitly authorized and merged on 2026-10-21. Its merge restriction records the original task scope; current project status is in the README and implementation roadmap. No live TrueNAS or VM validation has occurred.

> **For Hermes:** Implement this plan with offline tests and spec/quality review. Do not test a live VM.

**Goal:** Pin one official Ubuntu Server LTS cloud image and add a testable NoCloud/cloud-init seed-generation path while the project remains non-installing and experimental.

**Architecture:** Add an Ubuntu Server profile module that pins a date-specific Canonical release URL, signed-manifest SHA-256, expected size, and source metadata; delegate verified downloads to the existing generic artifact helper. Generate cloud-config and NoCloud metadata without passwords or private keys, and build a local seed image via `cloud-localds` behind an injectable, bounded subprocess runner. Expose only descriptive profile metadata in offline preview. Do not add TrueNAS write orchestration or image import/conversion until actual image format and boot behavior are verified.

**Tech Stack:** Python standard library, existing `planning/installer_core/artifacts.py`, `unittest`, mocked subprocess/download helpers. No new runtime Python dependency.

---

## Research facts and hard limits

- On 2026-10-21, Canonical's official cloud-image index listed `release-20261016` as the latest dated Ubuntu 26.04 LTS amd64 Server release; its manifest and image were last modified on 2026-10-20.
- Pinned file: `https://cloud-images.ubuntu.com/releases/server/26.04/release-20261016/ubuntu-26.04-server-cloudimg-amd64.img`.
- Official `SHA256SUMS` entry: `5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5 *ubuntu-26.04-server-cloudimg-amd64.img`.
- Direct HTTPS metadata returned `Content-Length: 859090944`. `SHA256SUMS.gpg` was checked against the official Ubuntu cloud-image signing-key fingerprint `D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81`; the detached signature verified. The available Python verifier does not check key revocation/self-signature status, so document this evidence precisely and do not claim a full GnuPG keyring/revocation audit.
- Ubuntu's cloud-init documentation describes NoCloud seed files (`user-data`, `meta-data`) on a `CIDATA` labeled ISO/VFAT filesystem and the `cloud-localds` utility. The captured TrueNAS 26.0.0-BETA.2 accepted-request schema includes `vm.device.create` `CDROM` attributes with a `path` field; this is schema evidence only, not tested write acceptance.
- **Verified locally:** the pinned full image passed the existing exact-size/SHA-256 download verifier; private-scratch `qemu-img info --output=json` reported QCOW2, 3,758,096,384-byte virtual size, no backing file, and clean dirty flag; `qemu-img check` reported no errors. A scratch-only conversion to raw succeeded and preserved virtual size (819,237,376 bytes actual allocation). This still does not validate TrueNAS import behavior or guest boot.
- **Verified locally:** the real `cloud-localds` helper produced a 374,784-byte ISO with volume ID `cidata` and both NoCloud seed files. It used a synthetic, nonfunctional test key. Debian tools were downloaded and extracted into private scratch; no system packages were installed.
- **Unverified:** TrueNAS import/write acceptance, disposable VM creation, boot/console, and first-boot provisioning.
- **Historical safety scope:** no VM, TrueNAS write, host-network, existing-resource, or deployment changes were made during this plan. PRs #3 and #2 were later merged by explicit user authorization; all four distro profiles remain experimental.

## Task 1: Add an Ubuntu Server profile and NoCloud seed builder

**Objective:** Provide immutable artifact metadata, SSH-key-only cloud-config, deterministic NoCloud metadata, and a private local seed-image builder whose subprocess is mockable.

**Files:**
- Create: `planning/ubuntu_server.py`
- Create: `planning/test_ubuntu_server.py`
- Reuse: `planning/installer_core/artifacts.py:download_and_verify`
- Reuse: `planning/installer_core/safe_filesystem.py:ensure_trusted_directory_chain` and `write_private_new_file`

**Steps:**
1. Write unit tests first for the fixed URL/digest/size, profile status and unverified image format; validate username/public OpenSSH key inputs; parse the emitted `#cloud-config` body as JSON/YAML-compatible JSON; assert no password/password-hash/private-key fields; verify NoCloud `instance-id` and safe hostname.
2. Run focused tests and confirm they fail before implementation.
3. Implement the immutable artifact record and a thin wrapper around `download_and_verify` using the exact pinned URL, digest, exact size, and a byte ceiling equal to the pinned size. Do not invoke the network in tests.
4. Implement deterministic `user-data` and `meta-data` generation with `dsmode: local`; disable password SSH and root login, lock the created user password, and accept only validated public authorized-key lines.
5. Implement seed-image generation using `cloud-localds --disk-format raw --filesystem iso`, argument-list invocation (no shell), bounded timeout, private temporary directory/files, refuse-overwrite behavior, regular nonempty output validation, atomic placement, and generic errors that do not echo seed contents.
6. Mock `cloud-localds` in unit tests; assert exact arguments, private file modes, output behavior, timeout/failure cleanup, and refusal to overwrite. Separately, after explicit user approval, run one real seed build inside private scratch with a synthetic nonfunctional key and inspect its ISO label/files; do not use production keys or attach it to a VM.
7. Run the focused tests and the existing planning tests.

## Task 2: Surface the profile in offline preview and documentation

**Objective:** Make the selected Ubuntu Server artifact and bootstrap plan reviewable without downloading it or applying TrueNAS changes.

**Files:**
- Modify: `planning/preview.py`
- Modify: `planning/test_preview.py`
- Modify: `README.md`
- Modify: `planning/README.md`
- Modify: `docs/implementation-roadmap.md`

**Steps:**
1. Add tests that Ubuntu Server preview includes pinned artifact URL/digest/size and NoCloud seed route, records QCOW2 plus the observed virtual size, remains `experimental`, has `apply_supported: false`, and does not invoke downloads/subprocess/middleware. Assert other profiles remain experimental without Ubuntu metadata.
2. Run the new preview tests and verify expected failures.
3. Integrate only static metadata in `build_plan`; do not call the download or seed functions from preview.
4. Document signature-verification limits, the locally verified QCOW2 image metadata, the real scratch seed-build result, CDROM schema-only evidence, and remaining disposable-VM gates. Clearly separate local artifact/seed validation from TrueNAS write acceptance and guest boot.
5. Run all Python unit tests, compile checks, preview examples, secret scan, and `git diff --check`.

## Task 3: Delivery and evidence

**Objective:** Publish only reviewed, tested changes to the existing feature branch and update the existing unmerged draft PR/issue.

**Steps:**
1. Verify `origin`, current branch, clean/expected diff, remote feature SHA, current `main` SHA, PR #2 state and issue #1 state.
2. Review the full diff for scope, safety, licenses, and secrets. Confirm no workflow file, raw capture, production credential, artifact image, or host-specific inventory was added.
3. Commit and push only `feat/multi-distro-installer`; never push or merge `main`.
4. Read back exact remote feature SHA, confirm `main` is unchanged, verify PR #2 remains draft/open and issue #1 remains open, and inspect hosted Actions runs for the exact commit SHA.
5. Report local tests, exact artifact/hash/format and seed evidence separately from hosted checks; explicitly state that no VM/TrueNAS write was tested and all profiles remain experimental.
