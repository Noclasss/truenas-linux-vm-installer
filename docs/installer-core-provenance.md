# HAOS core reuse and boundaries

## Source and license

The generic helpers in `planning/installer_core/` are adapted from
[`Noclasss/haos-truenas-installer`](https://github.com/Noclasss/haos-truenas-installer),
read from its `main` branch at commit
`6cdd8daf1de36967593ef69221c0416030a78a68`. The source repository's root
`LICENSE` is MIT, copyright (c) 2026 Noclasss; its notice is preserved in this
repository's `LICENSE`.

Only the separate Linux installer repository was edited. The HAOS repository
was cloned read-only for review and was not modified.

## Adapted components

- `middleware.py`: local `midclt` command adapter, with a bounded timeout,
  structured JSON parsing, narrow plain-text `system.version` support, and
  errors that do not echo raw host output.
- `schema.py`: positional middleware request validation, including required
  arguments, JSON types, enums/constants, unions, local references, object and
  array bounds, and an explicit wrapper that validates a write payload before
  invoking the client. It is a documented subset, not a complete JSON Schema
  implementation.
- `safe_filesystem.py`: trusted-directory checks, mode-0600 exclusive creation,
  no-overwrite behavior, and atomic private file replacement.
- `artifacts.py`: generic HTTPS-only redirects, private digest-keyed caching,
  streamed byte limits, expected-size checks, and SHA-256 verification.

The source helpers were adapted and covered with synthetic/mocked tests; no
TrueNAS calls, external artifact downloads, or VM writes were made while
building or testing this port.

## Deliberately not ported

- HAOS release selection/API endpoints, asset names, and Home Assistant version
  behavior.
- HAOS-specific QCOW2/XZ assumptions, UEFI/OVMF settings, resource defaults,
  prompts, zvol naming/ownership markers, and install/resume/cleanup orchestration.
- The remote WebSocket/authentication adapter and the HAOS shell launcher.

The new core is not wired into an installation path. Linux distro artifact
metadata, integrity source, image format, provisioning, firmware/device settings,
and supported TrueNAS versions remain separate profile decisions. No Linux
profile is live-enabled; all four profiles remain experimental.
