# TrueNAS Linux VM Installer

A standalone project for fast deployment of new Linux Server and Desktop virtual machines on TrueNAS SCALE.

## Status

**Experimental offline planner only; no live installer is released.** The `planning/` module validates settings and produces a JSON preview. It cannot create or start a VM. A separate helper can capture selected middleware method metadata read-only when explicitly run by an operator in a TrueNAS host shell; it does not install anything. See [planner and schema-capture documentation](planning/README.md).

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

No live Linux VM has been created or validated by this repository. Mocked tests, TrueNAS schema checks, preview checks, and live-install verification must be reported separately rather than conflated.

## Try the offline preview

```sh
python3 planning/preview.py --config planning/examples/vm.json
```

See [planner documentation](planning/README.md) for inventory checks, examples,
tests and limitations. Preview success does not establish live compatibility.

## Development

Implementation work belongs on a feature branch and should be reviewed in an unmerged pull request. Do not treat the project as usable until installer code, tests, and supported-version documentation are published and reviewed.
