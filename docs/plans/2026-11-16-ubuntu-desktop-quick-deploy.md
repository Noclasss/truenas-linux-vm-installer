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
- Existing state: offline planner and Ubuntu Server artifact/NoCloud helpers exist; Desktop candidate metadata, autoinstall/seed builders, Desktop read-only preflight, and a fake-only deployment rehearsal are present. No live writer exists; `LIVE_PROFILE_ALLOWLIST` is empty.
- Existing operator evidence covers only a manually configured Ubuntu Server console login. It does not establish desktop installation, graphical login/display, guest provisioning, or the installer flow.
- Canonical's current Subiquity docs say Ubuntu Desktop autoinstall is supported from 23.04 onward and that NoCloud cloud-config can deliver the autoinstall. On 2026-11-17, the cached 26.04.1 Desktop ISO passed the checked-in signature/size/SHA-256 verifier using the pinned Ubuntu fingerprint. `xorriso` then extracted `casper/install-sources.yaml` from those verified bytes; it lists `ubuntu-desktop-minimal` as default and also lists `ubuntu-desktop`. This confirms the catalogue IDs only; NoCloud autoinstall behavior and disposable TrueNAS boot/display validation remain untested. The route remains experimental and live-disabled.
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
- [x] Verify the detached signature of the pinned 26.04.1 ISO checksum manifest and inspect that exact ISO's `casper/install-sources.yaml`. On 2026-11-17 the signature, pinned signer, exact ISO size/digest, and both catalogue IDs were confirmed from cached bytes. The checked-in verifier's `source_id_verified` remains false because catalogue inspection is separate; no guest behavior is inferred.
- [x] Extend the existing exact-build read-only preflight for the Desktop profile, including its ISO/seed reserve and VM display-enable/CD-ROM request shapes; retain Server behavior and add refusal tests for unsupported schemas.
- [ ] Confirm the live target build matches a reviewed fixture. The checked-in Beta.2 fixture covers the CD-ROM and DISPLAY variants, but no live preflight ran here; actual graphical-console boot remains a separate guest-validation gate.

**Gate:** no deployable/live desktop support claims until image, unattended provisioning, and graphical-access path are evidence-backed.

### Phase 1 — Offline Ubuntu Desktop profile

- [x] Add candidate-only static artifact/provisioning metadata without changing the Server profile's behavior. The source ID remains unset in preview; the pinned artifact signature/bytes and ISO catalogue were verified locally on 2026-11-17, while installer behavior remains untested.
- [x] Add a focused regression asserting candidate metadata, explicit unresolved gates, experimental status, stopped VM, and non-applicable preview.
- [x] Reuse the live preflight's pool/network/name/CPU/RAM checks for Desktop with an ISO-plus-VFAT-seed capacity reserve and read-only schema validation for VM display enablement/CD-ROM/RAW media requests.
- [x] Add offline autoinstall JSON construction, hidden-password hashing via OpenSSL stdin, deterministic NoCloud metadata, and a private mode-0600 VFAT CIDATA seed-media builder. The exact catalogue IDs are now confirmed; autoinstall behavior and guest behavior remain unverified.
- [ ] Produce explicit offline preview output, warnings, and desktop resource defaults derived from upstream requirements; plan remains non-applicable.
- [x] Add tests proving the desktop profile is selected correctly and preview never downloads, writes files, or invokes host/API operations.

**Gate:** pinned artifact bytes and catalogue IDs are verified; the desktop remains experimental until unattended provisioning and guest behavior are validated.

### Phase 2 — Reviewed one-line launcher and live-disabled installer core

- Design the paste command around an immutable revision and separately pinned launcher digest. Stage to a private directory, verify before execution, clean only the launcher's own temporary files, and pass through user arguments safely.
- Extend/reuse existing shared local `midclt`, exact-build schema validation, read-only preflight, manifest, and safe artifact-download helpers only after auditing all callers and adapting tests.
- Implement no-write preview and plan rendering first. Keep the live profile allowlist empty.

**Gate:** launcher and preview tests pass; no live write method is reachable from preview; missing/mismatched digest fails before Python execution.

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
- Next: run the local verifier on the actual pinned artifacts and inspect the
  ISO source catalogue. Design staged-media access and a separately reviewed
  live writer; do not replace the fake type gate with a live adapter. Validate
  exact runtime writes, durable ownership and graphical installation on a
  specifically selected disposable target before enabling deployment.
