# Ubuntu Desktop TrueNAS Quick-Deploy Roadmap

**Goal:** Deliver a reviewed, paste-once TrueNAS-shell command that guides the user through creating a new Ubuntu Desktop VM, while keeping preview, consent, resource ownership, and recovery explicit.

**Architecture:** Keep this guest workflow in `truenas-linux-vm-installer`, separate from the HAOS installer. Reuse the tested offline planner and safety-core patterns only after reviewing each dependency. Keep the repository's `install.sh` local-only. The requested one-line shell entry point must be a separate bootstrapper: stage an immutable reviewed revision into a private temporary location, verify its independently pinned digest before execution, and invoke the local wrapper; never pipe unverified remote bytes into a shell. The installer must start in read-only preflight, show a complete plan, and require an explicit `CREATE` confirmation. Autoinstall must start the installer VM only after that confirmation and request poweroff after completion; the resulting VM stays stopped and is not booted into the installed desktop automatically.

- **Tech Stack:** POSIX shell launcher, Python 3 standard library, TrueNAS `midclt`/versioned API contracts, Ubuntu Desktop Subiquity autoinstall via cloud-init NoCloud, pinned Ubuntu Desktop ISO, existing repository tests/CI.

---

## Scope and non-goals

- First supported guest target: **Ubuntu Desktop, amd64** only. Keep Ubuntu Server and Debian profiles unchanged.
- The user-facing end state is a one-line command run from the TrueNAS host shell, with interactive pool/network/resource selection and a reviewed create plan.
- No production VM or storage mutation during development. Any live validation requires separate explicit authorization and an isolated disposable target.
- GitHub scope: the user authorizes feature branches, commits, pushes, and pull-request creation/updates for tested offline-development changes. Merge only reviewed offline-only changes with passing checks; do not merge live-enabled functionality or publish a production installer without further approval.
- Do not weaken TLS, install packages on TrueNAS, expose credentials in chat/logs, modify host networking, or delete existing resources.

## Evidence at start

- Repository remote: `https://github.com/Noclasss/truenas-linux-vm-installer.git`.
- Current `main` is `e0c71487faedd7e329f64740b869ecb2ec3e0f13`; PR #30 was squash-merged as `b4b873292ca2300c85a2ebcb7092fe61b4050f6d`. PRs #31–#33 subsequently updated Desktop preview defaults, name/hostname handling, and documentation.
- Baseline: `python3 -m unittest discover -s planning -p 'test_*.py' -q` passed 198 tests on the exact current `main` tree during this development cycle.
- Existing state: offline planner and Ubuntu Server artifact/NoCloud helpers exist; Desktop candidate metadata, autoinstall/seed builders, Desktop read-only preflight, fake-only deployment rehearsal, and a separate isolated create-only writer module are present. The writer is not exposed through the public launcher and `LIVE_PROFILE_ALLOWLIST` is empty.
- Existing operator evidence covers only a manually configured Ubuntu Server console login. It does not establish desktop installation, graphical login/display, guest provisioning, or the installer flow.
- Canonical's current Subiquity docs say Ubuntu Desktop autoinstall is supported from 23.04 onward and that NoCloud cloud-config can deliver the autoinstall. On 2026-11-19, genuine GnuPG 2.4.7 verified the signature on the cached 26.04.1 Desktop checksum manifest using the pinned Ubuntu fingerprint, and the checked-in verifier confirmed the exact ISO size and SHA-256. Debian's signed APT metadata provided the private GnuPG and 7-Zip helper binaries; no package was installed on TrueNAS. The repository's 7-Zip fallback extracted `casper/install-sources.yaml` from the same verified ISO and confirmed that `ubuntu-desktop-minimal` is the default source, with `ubuntu-desktop` also listed. The minimal entry describes a minimal but usable desktop, but no autoinstall, offline installation, VM boot, or graphical login has been tested. The route remains experimental and live-disabled.
- TrueNAS official documentation describes browser SPICE display access. The existing sanitized `TrueNAS-26.0.0-BETA.2` fixture also contains a DISPLAY/SPICE request variant, but that is exact-build schema evidence only, not write acceptance or compatibility with another release.
- **Reusable TrueNAS checks already existed; this branch extends them rather than duplicating them.** At HAOS installer commit `6cdd8daf1de36967593ef69221c0416030a78a68`, `build_plan` checks the supported version and runtime method set, validates representative VM/dataset/NIC/start payloads against `core.get_methods`, checks healthy pools, eligible NIC choices, VM/storage name collisions, and a pool-free-space reserve. It also sets `ensure_display_device=True`. The Linux repo's `planning/readonly_preflight.py` already had exact-build compatibility, pool health/capacity, selected bridge, name/path collisions, virtualization support, maximum vCPU, and available RAM plus reserve. This branch now accepts the Ubuntu Desktop candidate, selects a separate ISO-plus-seed staging budget, and validates `ensure_display_device` plus the CD-ROM request shape against the reviewed fixture before host calls. The existing Server budget/flow remains covered. This is local code plus fake/fixture tests, not a live preflight; neither helper proves GUI boot, actual display creation, NoCloud media access, or desktop credentials. Live results must be fresh for the target build and VM plan.
- Current issue #1 remains open and explicitly says no live resource writes/VM creation are authorized by that issue. Its status text is stale (2026-10-24) relative to the current fetched `main`; do not use its historical test count as current evidence.
- The Jarvis Project Manager task tools are not registered in this session; this local roadmap is not claimed as a PM-record update.

## Acceptance criteria

1. A documented one-line command is pinned to an immutable reviewed revision and verifies the launcher independently before execution; no unverified `curl | sh` path.
2. Installer offers `--preview`, caller-selected pool/bridge/name/resources, fresh version/schema/resource preflight, complete plan review, explicit `CREATE`, private ownership manifest, exact read-back, no automatic cleanup, and a stopped post-install VM. The installer VM's temporary start is disclosed and occurs only after `CREATE`.
3. Ubuntu Desktop image and first-boot provisioning are pinned and independently integrity-checked; the design explicitly documents whether packages are installed from an official cloud image or a desktop installer image.
4. In an authorized disposable VM, first boot produces an actually usable graphical session through the documented TrueNAS console/display route; record exact TrueNAS build, Ubuntu release, image digest, user actions, and limits. A text login prompt is not desktop evidence.
5. Offline tests cover launcher verification/rejection, preview no-write behavior, plans, image pins, cloud-init/credentials, schema validation, collision/capacity refusal, manifest/reconciliation behavior, and all changed callers/docs/fixtures.
6. Full repository suite, Python compile, tracked shell syntax checks, secret scan, and `git diff --check` pass before any publication request.

## Roadmap

### Phase 0 — Pin the guest and UX contract (candidate route selected; validation remains open)

- [x] Refresh `main`, branch, worktree state, existing open issue/PR state, and full baseline suite.
- [x] Confirm the requested operator interface: paste once in the TrueNAS shell; prompts, review plan, explicit confirmation.
- [x] Select the candidate route: official Ubuntu Desktop installer ISO + Subiquity autoinstall over cloud-init NoCloud. Canonical documents Desktop autoinstall support from 23.04 onward and NoCloud cloud-config delivery.
- [x] Verify the detached signature of the pinned 26.04.1 ISO checksum manifest and inspect that exact ISO's `casper/install-sources.yaml`. On 2026-11-19, genuine GnuPG verified the pinned signer and exact ISO size/digest; the checked-in 7-Zip fallback extracted the catalogue from the same bytes and confirmed both IDs, with `ubuntu-desktop-minimal` marked default. The checked-in artifact report still sets `source_id_verified: false` because catalogue inspection is separate; no guest behavior is inferred. See [artifact verification evidence](../desktop-artifact-verification.md).
- [x] Extend the existing exact-build read-only preflight for the Desktop profile, including its ISO/seed reserve and VM display-enable/CD-ROM request shapes; retain Server behavior and add refusal tests for unsupported schemas.
- [ ] Confirm the live target build matches a reviewed fixture. The checked-in Beta.2 fixture covers the CD-ROM and DISPLAY variants, but no live preflight ran here; actual graphical-console boot remains a separate guest-validation gate.

**Gate:** no deployable/live desktop support claims until image, unattended provisioning, and graphical-access path are evidence-backed.

### Phase 1 — Offline Ubuntu Desktop profile

- [x] Add candidate-only static artifact/provisioning metadata without changing the Server profile's behavior. The source ID remains unset in preview; current signature, pinned bytes, and catalogue evidence are recorded in [artifact verification](../desktop-artifact-verification.md), while installer behavior remains untested.
- [x] Add a focused regression asserting candidate metadata, explicit unresolved gates, experimental status, stopped VM, and non-applicable preview.
- [x] Reuse the live preflight's pool/network/name/CPU/RAM checks for Desktop with an ISO-plus-VFAT-seed capacity reserve and read-only schema validation for VM display enablement/CD-ROM/RAW media requests.
- [x] Add offline autoinstall JSON construction, hidden-password hashing via OpenSSL stdin, deterministic NoCloud metadata, and a private mode-0600 VFAT CIDATA seed-media builder. The exact catalogue IDs are now confirmed; autoinstall behavior and guest behavior remain unverified.
- [ ] Produce explicit offline preview output, warnings, and desktop resource defaults derived from upstream requirements; plan remains non-applicable.
- [x] Add tests proving the desktop profile is selected correctly and preview never downloads, writes files, or invokes host/API operations.

**Gate:** pinned artifact bytes and catalogue IDs are verified; the desktop remains experimental until unattended provisioning and guest behavior are validated.

### Phase 2 — Reviewed one-line launcher and live-disabled installer core

- [x] Add a separate, no-argument Ubuntu Desktop preview bootstrapper. It stages only the reviewed source commit `912333b58bdaec01a82bb9365212cf5642cc748b` in a private temporary checkout, verifies the exact commit before running the local `install.sh`, and cleans only that temporary checkout.
- [x] Pin the bootstrapper's SHA-256 in the README copy/paste command; a regression test requires the documented pin to match the exact script bytes.
- [x] Fake-Git tests cover exact-commit success, mismatched-commit and fetch-failure refusal before launch, argument rejection, mode-0700 staging, and temporary-tree cleanup. This exercises the bootstrapper's control flow; it does not test GitHub transport or TrueNAS.
- [ ] Replace the reviewed source pin only after a later code revision has been reviewed. Keep this bootstrapper preview-only; `install.sh` remains local-only and no live writer is reachable.

**Gate:** launcher, preview, and bootstrapper tests must pass; the documented digest must match the script bytes, and hash/checkout mismatch must stop before Python execution. Preview must have no reachable live-write method. Passing this gate still proves no live support.

### Phase 3 — Create-only live flow (code-reviewed, still disabled)

- Add the minimum exact-build write set for per-VM storage, image import, VM configuration, and devices; validate each payload against the exact build's positional schema immediately before writing.
- Require a fresh full read-only preflight, no collision, capacity/memory margin, root host access where `/dev/zvol` requires it, and a private exclusive manifest before first write.
- Re-read each exact resource after a write; treat timeout/ambiguous results as manual-reconciliation-only; never blind-retry, resume, or auto-delete.
- Require `CREATE` after displaying the complete plan. Starting the installer VM is required for unattended installation; disclose it as part of the plan, set autostart disabled, require read-back to `RUNNING`, and require the autoinstall `poweroff` outcome/read-back before detaching installation media. Do not start the installed desktop by default.

**Gate:** mocks and code review pass; this phase alone does not authorize enabling a live profile.

### Phase 4 — Authorized disposable validation

- First collect exact target TrueNAS build/schema and safety/capacity evidence read-only.
- Ask for separate authorization naming the disposable VM/resource scope before any TrueNAS mutation.
- Validate image conversion, VM/device read-back, provisioning completion, graphical console, usable desktop login, and cleanup/reconciliation separately. Never touch existing production VMs/disks or production credentials.

**Gate:** profile can be enabled only for builds/routes that passed all listed checks; other builds remain refused.

### Disposable test sequence — proposal only, not authorization

This plan is for a single, disposable Ubuntu Desktop guest on `TrueNAS-26.0.0-BETA.2`. The current repository has no live writer and `LIVE_PROFILE_ALLOWLIST` is empty, so no live test is executable from this checkout. Do not substitute ad-hoc `midclt` writes. A later test requires a separately reviewed live-writer revision, fresh read-only preflight, and separate explicit approval before the first TrueNAS mutation.

1. **Select and recheck the disposable boundary.** Use only a pool explicitly selected by the user; do not switch pools to work around a failed check. Re-read the exact TrueNAS build and method contracts, pool health/capacity, RAM headroom, VM and device inventory, candidate storage paths, and bridge/interface membership immediately before any approved test. Previously reported VM, pool, bridge, resource, and reserve values are proposal inputs, not selections, reservations, or authorization. Choose a new unique test-only dataset path outside any production workload namespace; prove the exact leaf path and zvol absent. Never claim an existing parent as owned.
2. **Stage and re-verify the exact artifact.** Stage the already verified 26.04.1 ISO into a private, run-specific location scoped to the disposable test. On the staged copy, re-verify the detached signature against the pinned Ubuntu key fingerprint and the checksum manifest, then verify exact ISO size and SHA-256. Re-inspect the source catalogue from those same bytes. Preserve the existing pins; do not treat earlier cache verification as proof that a transferred/staged copy is intact. Stage seed media privately as well; keep its hash-bearing contents out of logs and Git.
3. **Prove ownership and recoverability before the guest run.** A reviewed writer must use an exclusive private manifest created before the first write, record the exact pre-write absence/parent ownership state, create resources individually, and immediately read back exact dataset/zvol identity, type and size, VM identity, disk association, and every device. Offline fault-injection tests must cover failure-before-apply and lost-response-after-apply for each creation. On an ambiguous result, stop; query exact targets read-only, mark manual reconciliation, and never retry, resume, or auto-delete. Live cleanup must be limited to positively owned test resources.
4. **Run one unattended install with no guest NIC.** Keep `desktop_source_id` unset in all preview output. For the actual test-only seed, choose one catalogue ID explicitly after approval; `ubuntu-desktop-minimal` is the ISO's labelled default, but its installer behavior is untested and it is not preselected here. Use a hidden local credential prompt and a synthetic disposable account—never share a password or hash in chat or logs. Attach the verified ISO and private NoCloud seed, leave autostart off, and start only the new installer VM after the complete plan is shown and the separately approved create gate is satisfied. First attempt with no NIC to avoid LAN exposure; if the ISO cannot complete unattended installation offline, stop and obtain a verified isolated-network plan rather than attaching a LAN bridge by default.
5. **Verify poweroff, then test the installed desktop.** Read back the installer VM and devices, confirm Subiquity completed and the VM is stopped after its requested `poweroff`, then detach only the exact installer media devices with read-back. Start the installed guest only as a separately stated step in the authorized test plan. Confirm hypervisor state and use the TrueNAS graphical console/SPICE to verify a usable desktop session and local test-account login; a text prompt or `RUNNING` state alone is insufficient. Keep networking disconnected unless its need and isolation have been proven.
6. **Clean up only the disposable run.** Read the manifest and exact live inventory again. Stop only the named test VM, remove only its positively identified devices and VM, preserve production resources and unrelated storage, then remove the exact test zvol and only empty datasets created by this run, non-recursively. Read back absence after each deletion and verify pre-existing VM/dataset inventories are unchanged. Any mismatch or ambiguous write halts cleanup for manual reconciliation; no wildcard, prefix, force, or recursive deletion.

**Network boundary:** A bridge connected to a primary LAN may give the guest access to reachable local services; actual reachability depends on current firewall/isolation policy, which the offline preview does not establish. A bridge with no subnet configured does not prove isolation: bridge members, VLAN tagging, DHCP, routes, and switch policies must be checked. No bridge-backed isolated test network is confirmed by this plan. The safest first attempt is no NIC; if networking is required, stop until a current read-only topology/policy check identifies an existing isolated option and the user approves it. Creating or changing a VLAN, bridge, firewall, or network is outside this plan.

**Gate:** no test starts until the exact VM name, test-only storage path, resource limits/reserves, source ID for the test seed, network choice (prefer no NIC), installer start/poweroff/guest-boot steps, and exact cleanup scope are confirmed and separately authorized. This document and any successful read-only preflight are not authorization.

### Phase 5 — Release and operator documentation

- Update README and compatibility table with the actual pinned paste command and its behavior (preview vs install, prompts, `CREATE`, no-start default, recovery manifest).
- Run all acceptance checks, secret/private-network scan, and diff review. Keep release BETA until evidence supports stronger claims.
- Request explicit publication authorization; then publish only the reviewed feature branch/PR and verify exact remote SHA and hosted checks.

## Current work state

- The [Jarvis smoke handoff](../jarvis-smoke-tests.md) now provides an exact-commit
  command, private logs and machine-readable results. CI runs the command end to
  end. Skipped tests or missing real crypto coverage cannot produce a full pass.
- Recovery now requires complete, ordered acknowledgements for a completed
  manifest, a pending operation matching the next planned resource, and VM/device
  summaries consistent with those acknowledgements. Missing records cannot be
  treated as successful recovery.

- Persistent mock ownership/recovery is merged in PR #36. PR #38 adds a local
  signed-manifest and ISO-byte verifier with 235 hosted tests passing, including
  a real signature round trip using a disposable test key. It does not establish
  verification of the actual Canonical ISO; see
  [artifact verification](../desktop-artifact-verification.md).
- Persistent rehearsal now reads the fake inventory after each creation before
  acknowledging ownership. It verifies planned dataset identity/type, returned
  numeric IDs, VM disk association, device parent VM, expected attributes and
  synthetic ownership. A successful method return alone is not verification.
  Failed read-back retains the pending operation and stops without retry/deletion.
- Recovery applies the same exact-resource checks and refuses to claim unplanned
  paths, boolean IDs, wrong VM disks, devices attached to another VM or duplicate
  devices. Tests inject success-without-creation and altered inventory.
- The current deployment and ownership flows still accept only the built-in
  offline fake. No live writer, host changes, ISO download or graphical boot has
  been performed by this work.
- The new `ubuntu-desktop-preview-bootstrap.sh` stages and executes only the
  reviewed preview commit `912333b58bdaec01a82bb9365212cf5642cc748b`; its own
  SHA-256 is pinned in the README. It has not been run on TrueNAS and is not an
  installer.
- Next: complete the full offline suite on the final feature candidate, then commit/publish and verify exact-SHA Actions. Artifact and catalogue checks are now current, but the fresh host gate remains blocked: the available read-only diagnostics do not establish exact TrueNAS build/API methods, memory/CPU headroom, bridge state, VM/device/storage collisions, or staged-file service-account access; the helper's SSH attempt was denied and no approved administrative route is available in-session. The exact target record and private seed therefore remain pending. Do not stage files or invoke `create`; continue only after an authorized operator route and a separately reviewed target/credential plan exist.
