# TrueNAS Linux VM Installer

A standalone project for fast deployment of new Linux Server and Desktop virtual machines on TrueNAS SCALE.

## Status

**Experimental offline planner and reusable safety core only; no live installer is released.** The `planning/` module validates settings and produces a JSON preview, with an offline interactive menu for profile, pool, bridge, CPU, RAM, and disk-size choices. `planning/mock_install.py` rehearses a limited Ubuntu Server resource-creation sequence against a stateful in-memory fake and a private local test manifest; it cannot connect to TrueNAS, download or actually convert images, write host storage, or start a VM. Its conversion model accepts only a fixed synthetic source and a fresh in-memory zvol. `planning/installer_core/` contains tested infrastructure adapted from the HAOS TrueNAS installer—middleware/schema validation, safe local files, and verified HTTPS artifact downloads—but it is not wired to any live install flow. `planning/ubuntu_server.py` pins an official Ubuntu 26.04 LTS amd64 cloud image and provides an offline-testable NoCloud seed helper using `cloud-localds`; preview does not download the image or generate seed media. The image's SHA-256 and exact size were verified, and `qemu-img info` identified it as QCOW2 with a 3,758,096,384-byte virtual size and no backing file. Separate operator-run tests have since demonstrated narrow synthetic conversion and a manually configured Ubuntu Server VM reaching its console login prompt. Neither exercised repository code, installer automation, guest login, NoCloud first-boot provisioning, or guest networking; see the live-test evidence below. The image pin and evidence limits are documented in the [planner notes](planning/README.md) and [roadmap](docs/implementation-roadmap.md).

This project is separate from the Home Assistant OS installer so Linux distributions and installation workflows can be designed without affecting HAOS.

**Current publication state (2026-11-23):** The default branch includes merged PR #40 (exact-commit offline smoke and recovery-completeness checks) and PR #41 (pinned Desktop ISO signature/catalogue verification and updated preview metadata). PR #45, which adds a guarded Ubuntu Desktop operator workflow, remains open, draft, and unmerged. The default branch remains preview-only; `LIVE_PROFILE_ALLOWLIST` is empty and no live installer is released.

## Initial target

The planned profiles are:

- Ubuntu Server
- Ubuntu Desktop
- Debian Server
- Debian Desktop

All profiles are experimental until each has passed automated tests and disposable VM boot and first-boot checks on supported TrueNAS versions. Each image and edition needs its own verified source, provisioning method, boot configuration, resource defaults, and tests; a profile will not be labelled ready just because another distro works.

## Ubuntu Desktop quick-deploy (work in progress)

**No production one-line VM deployment command is available.** The Ubuntu Desktop
candidate uses the pinned Ubuntu 26.04.1 Desktop ISO and Subiquity autoinstall via
NoCloud. The current `main` branch provides offline JSON autoinstall rendering,
password hashing through OpenSSL stdin, and private CIDATA VFAT seed-media creation.
The desktop preflight checks reviewed fixture-backed CD-ROM, RAW seed, and display
request shapes; these are **not proof of live TrueNAS write compatibility**.

Offline deployment development now includes a **fake-backend-only** sequencing
rehearsal with an exact `CREATE` gate, collision checks, basic read-back checks,
and explicit partial-failure refusal. The fake preflight checks pool, bridge,
conflicts, virtualization, CPU and memory; filesystem parents are modeled before
the zvol. Proposed writes are checked against the reviewed Beta.2 schema.
A private atomic ownership manifest and read-only recovery report now model
interrupted operations and lost responses. Recovery cannot continue or delete
resources. These components cannot connect to TrueNAS or create a real VM.
The offline preview also reserves the pinned Desktop ISO and seed-media size in
addition to the requested virtual disk, and desktop autoinstall now accepts the
same VM-name formats as the planner while deriving valid guest hostnames.

Recent merges:

- [#30](https://github.com/Noclasss/truenas-linux-vm-installer/pull/30) —
  offline-only VM sequencing and refusal tests (CI passed).
- [#31](https://github.com/Noclasss/truenas-linux-vm-installer/pull/31) —
  include Desktop artifact/seed staging bytes in offline pool capacity (CI passed).
- [#32](https://github.com/Noclasss/truenas-linux-vm-installer/pull/32) —
  align Desktop autoinstall naming with planner names (CI passed).

- [#34](https://github.com/Noclasss/truenas-linux-vm-installer/pull/34) —
  enforce fake preflight and filesystem-parent ordering.
- [#35](https://github.com/Noclasss/truenas-linux-vm-installer/pull/35) —
  validate proposed Desktop writes against the exact reviewed schema.
- [#36](https://github.com/Noclasss/truenas-linux-vm-installer/pull/36) —
  persist mock ownership and report recovery state without continuing or deleting.

- [#38](https://github.com/Noclasss/truenas-linux-vm-installer/pull/38) —
  verify caller-supplied ISO bytes against a signed manifest and pinned signer.
- [#39](https://github.com/Noclasss/truenas-linux-vm-installer/pull/39) —
  require exact fake-resource read-back before acknowledging ownership.

Historical verification for PR #36: the post-merge [planner run 43961915844](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/43961915844)
passed unit tests, compilation, preview examples, and whitespace checks at
`89c2cd29d8c7d0de4d1e890cb41872b4388c1a3d` on 2026-11-16.
These checks establish offline behavior only.

**Release blockers remain:** independently verify the Canonical checksum manifest
signature; inspect the exact ISO's `casper/install-sources.yaml` to confirm the
Subiquity source ID; implement an exact-build, separately reviewed live
middleware writer with ownership manifest and deterministic recovery; verify
unattended installation, post-install poweroff, and graphical desktop login
on a **separately authorized disposable** TrueNAS VM. Until then, the live
profile allowlist remains empty and the tool must remain preview-only.

A [local signature and ISO checker](docs/desktop-artifact-verification.md) is
available for caller-supplied files on a trusted workstation. Its tests do not
establish verification of the actual release ISO or enable deployment.

See the [Ubuntu Desktop roadmap](docs/plans/2026-11-16-ubuntu-desktop-quick-deploy.md).

## Safety goals

- Preview and inspect the plan before creating anything.
- Create a new, explicitly named VM and installer-owned storage only; never overwrite existing VMs or disks.
- Use official image sources and verify pinned integrity metadata before import.
- Use per-distro unattended provisioning where supported; prompt locally for any required guest password and never send plaintext credentials in chat, source, or logs.
- Reuse existing TrueNAS network choices; do not reconfigure host networking.
- Leave new VMs powered off unless starting is explicitly requested.
- Keep install manifests so failed runs can be reconciled safely.

No live Linux VM has been created or validated by this repository. Operator-provided read-only compatibility output for commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9` records a match for all 20 selected request/return schema contracts on `TrueNAS-26.0.0-BETA.2`, using only `system.version` and `core.get_methods`; no write methods were called. On 2026-10-22, operator-provided output from the checkout pinned to `9e89be1b932750d3010f40cd68a916b8afd43a70` reports that the read-only preflight passed on that build, including pool, bridge, collision, virtualization/vCPU, and memory checks. A later independent read-only rerun matched all 20 contracts and passed the existing preflight logic against current middleware responses collected over strict key-only SSH. It invoked no write methods; the preflight logic ran locally against those responses through a temporary SSH adapter, not through the checked-in CLI or its local `midclt` adapter. At that time, noninteractive root elevation was unavailable. A later, separately authorized conversion-only probe is recorded below; it does not establish VM creation, guest boot, or first-boot behavior.

Separately, on 2026-10-22, an authorized operator-run staging-only probe created and removed a disposable filesystem dataset and a tiny file. Root could create a mode-0600 file; `libvirt-qemu` could traverse/list the directory but could not write there or read the file. The exact dataset was deleted and read back as absent. This did not run installer code or test image conversion, CDROM attachment, VM creation, or boot; see the [roadmap](docs/implementation-roadmap.md) for limits.

On 2026-10-23, a separately authorized operator-run conversion-only probe on `TrueNAS-26.0.0-BETA.2` converted a 1 MiB synthetic raw image to a fresh 16 MiB non-sparse zvol. Middleware history showed two successful conversion jobs; a byte comparison of the source and the first MiB of the zvol passed. The exact disposable zvol and parent dataset were deleted non-recursively without force, and independent read-only queries confirmed the dataset, zvol, and device reference were absent. This demonstrates narrow live conversion/write acceptance and data transfer for that synthetic raw input only. It did not invoke installer code, use the pinned Ubuntu image, create a VM/device, or test boot, seed media, provisioning, or cleanup automation. The duplicate successful jobs are recorded without inferring why the call ran twice.

A separate operator-run console-only test later converted the pinned Ubuntu Server image, started a manually configured VM with no NIC or seed/CDROM, and reached the Ubuntu 26.04.1 LTS `ubuntu login:` prompt. This establishes a narrow manual guest-boot result only; it does not test repository code, login, first-boot provisioning, or networking. See the [operator console boot evidence](docs/operator-console-boot-evidence.md) and [install boundary](docs/ubuntu-server-install-boundary.md).

## Merge history

- PR #3 (`fix/readonly-preflight-memory-units`) was squash-merged into `feat/multi-distro-installer` as `4ddf0b79a9c62658fc6aeabe88b1adafe860d004`; PR #2 was then squash-merged into `main` as `f40ca4c8cd520a6cbae0d9732cb43e9d59a16dc4`.
- Documentation PR #5 was squash-merged as `5393a2fea8d32fe896bfcc54163183fd4834c074`; its push Actions run [39656161788](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39656161788) succeeded (hosted test count unknown). PR #6 was then squash-merged as `f72d9f43bad386a939bac8de0044298cbebf18d7`; documentation-only PR #7 was later merged as `76c598687e953c7b2fa53f63f2476a751b905691`, with successful post-merge run [39671265846](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39671265846). PR #4 was closed without merging on 2026-10-22 after being superseded by #5.
- PR #6 (`feat/mock-fresh-zvol-import`) was squash-merged into `main` on 2026-10-21. Its 142-test local suite passed; PR-head planner run [39669242388](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39669242388) and post-merge push run [39669932188](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39669932188) succeeded. The merged changes are mock/offline only.
- Milestone #1, **Offline Mock Review and CI**, is complete and closed with PR #6. Milestone #2, **Disposable Validation and Profile Support**, remains open and tracks issue #1; neither milestone has a due date, and milestone #2 remains authorization-gated.
- The earlier 19-method count describes the initial fixture; the operator's exact-build comparison matched all 20 contracts on `TrueNAS-26.0.0-BETA.2`, using only `system.version` and `core.get_methods` and no write methods.
- On 2026-10-22, PR #10 was merged into `main` at `61515c2ecbbf530e7cfe860a8e3f1c902c75c462`; its planner check and post-merge push run [39794106338](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39794106338) passed. PR #11 was then squash-merged at `9ef93d5d986545e78f67862a4ba1edc9229ffd0a`; its planner check and final push run [39795854209](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39795854209) passed. At the time of that earlier status refresh, PR #11 was the functional `main` head.
- PR #12 refreshed status documentation at `b674695`; PR #13 added mock-only staging-path planning at `3d6522e`; PR #14 constrained staging paths to the selected pool at `d25ceda3d6e8627c7ab06145db152c25550bbef0`. The latest merge still does not add a live installer writer.
- PR #15 (`339e137e3c237371a4d36956e6b149ecaa58583d`) documented the cleaned-up staging permission probe. PR #16 (`b5c4ab9c141d960742c554c6183ef84255ecc0a0`) clarified artifact access boundaries. PR #17 (`ebc8081aa23d4ba43071c09420e0c36c141d95b1`) refreshed status and defined the conversion-only gate; its hosted planner run [39832859709](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39832859709) passed with 150 local tests reported. PR #18 (`506c3d201073db00266439cf56802b11abbc5551`) added mock seed ISO ownership/mode reconciliation; its hosted planner run [39855166968](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39855166968) passed with 152 local tests reported. PR #19 refreshed the status documentation and was squash-merged on 2026-10-22 as `d30981cf95f114ad0a2b4f35ce04c9858e998057`; it made no installer behavior changes. At that historical checkpoint, PR #18 was the latest functional change. PR #23 added the guarded console-only operator runbook and was squash-merged on 2026-10-23 as `4333501c41e9a96827bc99f5026e6981c6f884ee`; its `planner` check passed.

The recorded merges and offline checks do not establish installer behavior. The manual live tests establish only the narrowly described synthetic conversion and console boot; installer-flow write acceptance, seed/CDROM setup, guest provisioning/login, guest networking, and automated cleanup remain unverified. The project remains experimental, `LIVE_PROFILE_ALLOWLIST` remains empty, and all four profiles remain experimental.

## Quick start: previews only

The ordinary `install.sh` interactive route is an offline JSON planner; it does not contact TrueNAS. From a reviewed local checkout, run:

```sh
./install.sh --interactive --profile ubuntu-desktop
```

For the guided host-side read-only preflight, run this from the repository root on TrueNAS or another authenticated TrueNAS shell with `midclt` access:

```sh
python3 planning/desktop_readonly_preview.py --preview
```

It shows VM name, explicit pool, vCPU, RAM, virtual-disk size, autostart/start state, and the fixed first-create no-NIC choice. You can accept or change the values; it measures point-in-time RAM and pool headroom, then runs the existing preflight only after `RUN`. Exact `TrueNAS-26.0.0-BETA.2` build and method request/return schemas remain mandatory. It reports pass/fail and unchecked items. It does not create a VM, dataset, zvol, device, manifest, seed, or installation, and it does not start a VM. A pass is not write authorization or a reservation.

For a shell without a checkout, the command below downloads the reviewed bootstrap script from immutable commit `1b6952082de3cbaa1798457a515be487bf6dd65a`, checks its independently pinned SHA-256 before executing it, then the script fetches and verifies that same source commit and opens the interactive preflight. The shell needs `curl`, `sha256sum`, `git`, `python3`, and an interactive terminal. It writes only temporary download/checkout files under `/tmp` and removes its private directory on exit; no TrueNAS VM/storage resources are changed.

```sh
( set -eu; umask 077; REF='1b6952082de3cbaa1798457a515be487bf6dd65a'; BOOTSTRAP_SHA256='c9434dfc604fcf0bc7eff53dc942dfd38959d9813b40f993981e047ef45f620e'; d=$(mktemp -d "/tmp/linux-vm-desktop-preview.XXXXXXXX") || exit 1; trap 'rm -rf -- "$d"' 0; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM; curl -fsSL --proto '=https' --proto-redir '=https' "https://raw.githubusercontent.com/Noclasss/truenas-linux-vm-installer/$REF/ubuntu-desktop-preview-bootstrap.sh" -o "$d/bootstrap.sh"; printf '%s  %s\n' "$BOOTSTRAP_SHA256" "$d/bootstrap.sh" | sha256sum -c -; sh "$d/bootstrap.sh" --ref "$REF" --preview )
```

For the offline planner from a config file:

```sh
./install.sh --config planning/examples/vm.json
```

See [planner documentation](planning/README.md) for the preflight inputs, exact-build/schema gates, headroom calculations, unchecked items, and limitations. Preview success does not establish write acceptance, artifact readiness, VM-process access, installation, or guest boot.

### Verified bootstrapper (preview only, unreleased)

`launch.sh` is the offline bootstrapper for running the planner from one pinned
revision without a checkout. It requires a SHA-256 digest for the archive, checks
it before extraction, rejects links, special files and unsafe member paths, runs
`planning/preview.py` in isolated mode from a private temporary directory, and
removes that directory on exit. It has no default digest, never pipes remote bytes
into a shell, and makes no TrueNAS calls.

```sh
sh launch.sh --revision <40-hex-commit> --sha256 <64-hex-archive-digest> -- --config planning/examples/vm.json
sh launch.sh --archive ./reviewed.tar.gz --sha256 <64-hex-archive-digest> -- --interactive
```

No release revision or digest is published yet, so there is no pasteable
TrueNAS-shell command. The digest must come from an independent review of the
revision, not from the same download. This verifies the archive bytes only; it does
not establish live compatibility or guest behavior.

## Operator test runbooks

The [console-only test VM runbook](docs/operator-console-test-disk-attach.md) contains separate guarded TrueNAS Shell steps to attach the disposable zvol and request a no-overcommit VM start. The [NoCloud seed and console-login runbook](docs/operator-nocloud-seed-test.md) is documentation only: it requires a trusted pre-existing transfer route, leaves the VM without a NIC, and tests seed pickup on an already-booted guest—not pristine first boot. No seed was created, transferred, or attached. Neither runbook is installer functionality or evidence of guest provisioning. The console boot result is documented in [operator console boot evidence](docs/operator-console-boot-evidence.md).

## Smoke testing and Jarvis handoff

Use the [Jarvis smoke-test handoff](docs/jarvis-smoke-tests.md) for an exact-commit
local run with private logs and a JSON result. The runner tests offline previews,
seed media and fake ownership/recovery; it never creates TrueNAS resources.
Actual ISO verification and read-only host preflight are separate evidence stages.

## Development

Implementation work belongs on a feature branch and should be reviewed in an unmerged pull request. Do not treat the project as usable until installer code, tests, and supported-version documentation are published and reviewed.
