# Ubuntu Desktop staged media: design decision

**Status:** offline design record (2026-11-23). No live access path is implemented or validated.

## Problem

The Desktop installer needs two read-only media files attached to the installer VM:

- the pinned, signature-verified Ubuntu Desktop ISO (`ubuntu-installer.iso`), exposed as a `CDROM` device;
- the NoCloud seed image (`cidata.img`, a VFAT/ISO9660 CIDATA image), exposed as a `RAW` device.

The reviewed write contracts (`planning/desktop_write_contracts.py`) already place both under the installer-owned VM dataset, so the media is created, owned and removed together with the zvol.

## Decision

Stage both files inside the installer-owned VM dataset, at the planned path:

```
/mnt/<pool>/linux-vms/<vm-name>/ubuntu-installer.iso
/mnt/<pool>/linux-vms/<vm-name>/cidata.img
```

Rationale:

1. **Ownership stays exact.** The media lives under the same dataset as the disk. The ownership manifest already tracks that dataset, so no separate host path needs to be claimed.
2. **No shared media store.** A shared ISO directory would make one VM's detach or cleanup affect other VMs and violate the "never touch existing resources" rule.
3. **Read-only after staging.** The installer ISO is attached as CDROM and the seed as RAW, and the post-install step (`planning/desktop_finalize.py`) detaches both only after the VM reports `STOPPED`.

## Constraints this decision does not resolve

- **How bytes reach the dataset is unresolved.** Copying the verified ISO and seed into `/mnt/...` needs host-side file access. The plan forbids host writes outside an authorized disposable target, so this is a live-gate question, not an offline one.
- **Re-verification before attach.** `planning/desktop_integrity.py::verify_staged_iso` re-hashes a staged ISO copy against the pinned size and SHA-256, and `rehearse()` now calls it immediately before the CDROM attach (offline tests cover match, wrong size, wrong SHA-256, and missing file). The NoCloud seed is pinned at build time: `seed_pin_from_file` records its exact size and SHA-256, the ownership manifest stores that pin before the first write, and `verify_staged_seed` re-hashes the staged seed before `attach_seed`. The pin is produced per seed build and is never reused across rebuilds (the password hash salt makes each build's bytes differ). The create-only writer applies the same re-verification before its seed attach; the live gate that keeps it disabled is described in `desktop-create-only-adapter.md`.
- **Check position (resolved in Phase 2).** The writer now stages the ISO and seed into the per-VM dataset, re-verifies both copies (size, SHA-256, VM-process readability; `verify_staged_iso` and `verify_staged_seed`) and only then creates the zvol and calls `vm.create`. A failed check stops with `reconcile_required` and leaves only the shared parent (if it was created by the run) and the per-VM staging dataset with its staged files for manual cleanup; no zvol or VM exists. The offline fakes (`rehearse`, `rehearse_persistent`) verify media before disk/zvol and VM creation. Nothing is rolled back or deleted automatically.
- **Permissions.** `libvirt-qemu` must be able to read the staged files. The 2026-10-22 staging probe showed traversal and listing work but a mode-0600 file is unreadable, so staged files need an explicit read mode. Not validated on a live target.

## Offline coverage

- `planning/test_desktop_finalize.py` checks that planned media paths equal those in the reviewed write intents.
- The fake backend records the attached CDROM and RAW paths and refuses detach unless read-back matches the plan.
