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

No live Linux VM has been created or validated by this repository. An earlier operator report said the read-only compatibility probe matched 19 request schemas on `TrueNAS-26.0.0-BETA.2`, using only `system.version` and `core.get_methods`. The checker now also compares return schemas, but that expanded check has not yet been run on the host. Neither schema comparison establishes write acceptance, actual runtime response data, guest boot, or first-boot behavior.

## Try the offline preview

```sh
python3 planning/preview.py --config planning/examples/vm.json
```

See [planner documentation](planning/README.md) for inventory checks, examples,
tests and limitations. Preview success does not establish live compatibility.

## Development

Implementation work belongs on a feature branch and should be reviewed in an unmerged pull request. Do not treat the project as usable until installer code, tests, and supported-version documentation are published and reviewed.
