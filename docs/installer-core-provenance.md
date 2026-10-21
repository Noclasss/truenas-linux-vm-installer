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
  array bounds, finite schema nesting/reference handling, and a wrapper that
  blocks calls unless their methods are explicitly allowlisted. It validates
  write payloads before invoking the client. Schema validation is not
  authorization, and code holding the underlying client can bypass the wrapper;
  callers must keep it trusted and choose allowlists deliberately. It is a
  documented subset, not a complete JSON Schema implementation.
- `safe_filesystem.py`: trusted-directory checks, mode-0600 exclusive creation,
  no-overwrite behavior, and atomic private file replacement.
- `artifacts.py`: generic HTTPS-only redirects, private digest-keyed caching,
  streamed byte limits, expected-size checks, and SHA-256 verification.

The source helpers were adapted and covered with synthetic/mocked tests; no
TrueNAS calls, external artifact downloads, or VM writes were made while
building or testing this port.

## Orchestration audit outcome

The HAOS installer is a monolithic, guest-specific flow rather than a reusable
orchestration package. Its `install()` flow and static test suite were reviewed
at the pinned source commit above. The flow's resource ownership, manifest, and
failure-handling patterns are useful, but the image release/decompression,
HAOS boot configuration, zvol naming, and resume assumptions are not safe to
copy as a Linux implementation.

One directly portable safety pattern is now applied in the Linux mock rehearsal:
create the shared parent dataset explicitly if it is absent, then create the
per-VM filesystem dataset and zvol as separate operations. Never ask middleware
to create ancestors implicitly with `create_ancestors`; that could create
resources the manifest does not record. The rehearsal records each successful
creation separately, reuses an existing filesystem shared parent without
claiming ownership, verifies all three dataset levels on read-back, and marks
an ambiguous parent-create response for reconciliation without retrying. This
remains an in-memory mock; it does not authorize or implement a live write path.

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
