# TrueNAS Linux VM Installer

A standalone project for fast deployment of new Linux Server and Desktop virtual machines on TrueNAS SCALE.

## Status

**Experimental offline planner and reusable safety core only; no live installer is released.** The `planning/` module validates settings and produces a JSON preview, with an offline interactive menu for profile, pool, bridge, CPU, RAM, and disk-size choices. `planning/mock_install.py` rehearses a limited Ubuntu Server resource-creation sequence against a stateful in-memory fake and a private local test manifest; it cannot connect to TrueNAS, download or actually convert images, write host storage, or start a VM. Its conversion model accepts only a fixed synthetic source and a fresh in-memory zvol. `planning/installer_core/` contains tested infrastructure adapted from the HAOS TrueNAS installer—middleware/schema validation, safe local files, and verified HTTPS artifact downloads—but it is not wired to any live install flow. `planning/ubuntu_server.py` pins an official Ubuntu 26.04 LTS amd64 cloud image and provides an offline-testable NoCloud seed helper using `cloud-localds`; preview does not download the image or generate seed media. The image's SHA-256 and exact size were verified, and `qemu-img info` identified it as QCOW2 with a 3,758,096,384-byte virtual size and no backing file. TrueNAS write acceptance, import behavior, VM boot, and first-boot behavior remain unverified. The image pin and evidence limits are documented in the [planner notes](planning/README.md) and [roadmap](docs/implementation-roadmap.md).

This project is separate from the Home Assistant OS installer so Linux distributions and installation workflows can be designed without affecting HAOS.

## Initial target

The planned profiles are:

- Ubuntu Server
- Ubuntu Desktop
- Debian Server
- Debian Desktop

All profiles are experimental until each has passed automated tests and disposable VM boot and first-boot checks on supported TrueNAS versions. Each image and edition needs its own verified source, provisioning method, boot configuration, resource defaults, and tests; a profile will not be labelled ready just because another distro works.

## Safety goals

- Preview and inspect the plan before creating anything.
- Create a new, explicitly named VM and installer-owned storage only; never overwrite existing VMs or disks.
- Use official image sources and verify pinned integrity metadata before import.
- Use per-distro unattended provisioning where supported, without collecting private SSH keys or account passwords.
- Reuse existing TrueNAS network choices; do not reconfigure host networking.
- Leave new VMs powered off unless starting is explicitly requested.
- Keep install manifests so failed runs can be reconciled safely.

No live Linux VM has been created or validated by this repository. Operator-provided read-only compatibility output for commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9` records a match for all 20 selected request/return schema contracts on `TrueNAS-26.0.0-BETA.2`, using only `system.version` and `core.get_methods`; no write methods were called. This result applies only to that exact build and fixture. Schema matching does not establish write acceptance, actual runtime response data, guest boot, or first-boot behavior.

## Merge status (2026-10-21)

- PR #3 (`fix/readonly-preflight-memory-units`) was squash-merged into `feat/multi-distro-installer` as `4ddf0b79a9c62658fc6aeabe88b1adafe860d004`; PR #2 was then squash-merged into `main` as `f40ca4c8cd520a6cbae0d9732cb43e9d59a16dc4`.
- Documentation PR #5 was squash-merged as `5393a2fea8d32fe896bfcc54163183fd4834c074`; its push Actions run [39656161788](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39656161788) succeeded (hosted test count unknown). PR #6 was then squash-merged as `f72d9f43bad386a939bac8de0044298cbebf18d7`; documentation-only PR #7 was later merged as `76c598687e953c7b2fa53f63f2476a751b905691`, with successful post-merge run [39671265846](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39671265846). PR #4 remains open/draft and superseded by #5.
- PR #6 (`feat/mock-fresh-zvol-import`) was squash-merged into `main` on 2026-10-21. Its 142-test local suite passed; PR-head planner run [39669242388](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39669242388) and post-merge push run [39669932188](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39669932188) succeeded. The merged changes are mock/offline only.
- Milestone #1, **Offline Mock Review and CI**, is complete and closed with PR #6. Milestone #2, **Disposable Validation and Profile Support**, remains open and tracks issue #1; neither milestone has a due date, and milestone #2 remains authorization-gated.
- The earlier 19-method count describes the initial fixture; the operator's exact-build comparison matched all 20 contracts on `TrueNAS-26.0.0-BETA.2`, using only `system.version` and `core.get_methods` and no write methods.

The recorded merges and offline checks do not establish live TrueNAS preflight, write acceptance, VM creation, guest provisioning, or boot validation. The project remains experimental, `LIVE_PROFILE_ALLOWLIST` remains empty, and all four profiles remain experimental.

## Quick start (preview only)

From a reviewed local checkout, run the guided preview:

```sh
./install.sh --interactive
```

The launcher resolves the checked-out repository and runs the offline planner; it does not download code, contact TrueNAS, create a manifest, or install a VM. It prompts for the profile, VM name, pool, bridge, CPU, RAM, and disk size, then prints a JSON plan. All profiles remain experimental and the plan cannot be applied. A real guided installer still requires the live preflight, image-to-zvol import, resource read-back, and disposable boot gates below; this is not an install command yet.

For a config-file preview:

```sh
./install.sh --config planning/examples/vm.json
```

See [planner documentation](planning/README.md) for inventory checks, examples,
tests and limitations. Preview success does not establish live compatibility.

## Development

Implementation work belongs on a feature branch and should be reviewed in an unmerged pull request. Do not treat the project as usable until installer code, tests, and supported-version documentation are published and reviewed.
