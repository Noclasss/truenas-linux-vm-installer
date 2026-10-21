# TrueNAS Linux VM Installer

A standalone project for fast deployment of new Linux Server and Desktop virtual machines on TrueNAS SCALE.

## Status

**Experimental offline planner and reusable safety core only; no live installer is released.** The `planning/` module validates settings and produces a JSON preview, with an offline interactive menu for profile, pool, bridge, CPU, RAM, and disk-size choices. `planning/mock_install.py` rehearses a limited Ubuntu Server resource-creation sequence against a stateful in-memory fake and a private local test manifest; it cannot connect to TrueNAS, download or convert images, write host storage, or start a VM. `planning/installer_core/` contains tested infrastructure adapted from the HAOS TrueNAS installer—middleware/schema validation, safe local files, and verified HTTPS artifact downloads—but it is not wired to any live install flow. `planning/ubuntu_server.py` pins an official Ubuntu 26.04 LTS amd64 cloud image and provides an offline-testable NoCloud seed helper using `cloud-localds`; preview does not download the image or generate seed media. The image's SHA-256 and exact size were verified, and `qemu-img info` identified it as QCOW2 with a 3,758,096,384-byte virtual size and no backing file. TrueNAS write acceptance, import behavior, VM boot, and first-boot behavior remain unverified. The image pin and evidence limits are documented in the [planner notes](planning/README.md) and [roadmap](docs/implementation-roadmap.md).

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

## Review status (2026-10-21)

- PR #2 is the open, draft, unmerged `feat/multi-distro-installer` branch targeting `main`; its current head is `62d786c939b97fb3b425a662632ce29e422604ce`. The latest hosted Actions runs on that head, [39648144793](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39648144793) and [39648137214](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39648137214), succeeded. The latest code-bearing run, [39622916219](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39622916219), reported 116 tests passed; the current head has the same Git tree as that tested code commit.
- PR #3 is a separate stacked, open, draft, unmerged `fix/readonly-preflight-memory-units` branch targeting `feat/multi-distro-installer`; its last verified published head before this documentation update was `76289eb3f5d6829127cb7e793b312c9b20a298ce`. Its latest verified hosted Actions run, [39644757734](https://github.com/Noclasss/truenas-linux-vm-installer/actions/runs/39644757734), succeeded. The local suite on this branch passed 138 tests; compilation and CLI help checks passed. No hosted log-derived test count was available for that run.
- `main` remains at `88ef8195da992904dc24f7cfe49a717502b3e95f`. The Beta.2 fixture has 19 contracts on PR #2 and 20 on stacked PR #3; the operator reran the read-only comparison after the 20th contract was added and reported all 20 matched.

These statuses are evidence about code, schemas, and CI only. No live TrueNAS preflight, write acceptance, VM creation, or boot validation is claimed; all four profiles remain experimental.

## Try the offline preview

```sh
python3 planning/preview.py --config planning/examples/vm.json
```

See [planner documentation](planning/README.md) for inventory checks, examples,
tests and limitations. Preview success does not establish live compatibility.

## Development

Implementation work belongs on a feature branch and should be reviewed in an unmerged pull request. Do not treat the project as usable until installer code, tests, and supported-version documentation are published and reviewed.
