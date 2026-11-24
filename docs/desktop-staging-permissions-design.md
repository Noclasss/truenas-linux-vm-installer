# Ubuntu Desktop staged media: bytes, owners, and permissions (Phase 3 design)

**Status:** design plus offline tests. The live-writer gate (`LIVE_PROFILE_ALLOWLIST`) stays empty. Nothing here has been run on TrueNAS. Live read access by the VM process is **not** proven.

## 1. Who writes the bytes

Only `DesktopCreateOnlyWriter` (run by the authenticated root operator process) writes the staged ISO and seed. It creates each file with `O_CREAT|O_EXCL|O_NOFOLLOW`, inside the per-VM filesystem dataset mountpoint that TrueNAS returned from `pool.dataset.create`. Nothing else writes there, and no path outside that mountpoint is used.

Destination: `/mnt/<pool>/linux-vms/<vm>/` (mountpoint of the dataset `<pool>/linux-vms/<vm>`):

| File | Source | Size rule | Mode | Owner/group |
|---|---|---|---|---|
| `ubuntu-installer.iso` | verified source ISO | exactly `ARTIFACT_SIZE_BYTES` | 0600 | VM-process account |
| `cidata.img` | build-time seed (pinned, see #47/#51) | exactly `SEED_MEDIA_SIZE_BYTES` | 0600 | VM-process account |

## 2. Owner and mode that let the VM process read

- **Owner and group are the VM-process account**, not root. That account is the one whose uid owns the active QEMU process (`_active_qemu_process_owner`, matched against the selected account; see section 6).
- **Mode 0600.** The owner can read; group and other have no bits. Root can still read for re-hash because DAC does not apply to root.
- **Why not 0644 or root-owned 0600?** The 2026-10-22 probe showed a mode-0600 root-owned file is unreadable by the VM account. A group/world-readable seed would expose the password hash. The writer therefore never widens the mode; the owner change is what grants access.
- Staged files are never left group- or world-writable.

## 3. Directory chain

- The per-VM mountpoint and every ancestor must be a real directory (no symlinks), owned by root or the operator uid, and not group- or world-writable (`ensure_trusted_directory_chain`). A group-writable mountpoint refuses before any zvol or VM write (test `test_group_writable_staging_directory_refuses_before_zvol_and_vm`).
- The VM account must be able to traverse (`x`) every ancestor. This is proven only by the runuser read probe for each staged file (`_safe_read_probe`), which opens the exact file as the account.

## 4. CDROM and seed "chown" handling

- There is **no chown after attach**. Ownership is set on the descriptor the writer created, with `fchown` and `fchmod`, **before any byte is copied**.
- After the copy the writer `lstat`s the file and requires `st_uid`, `st_gid`, `st_mode & 0o7777 == 0600`, size, and SHA-256. Any mismatch stops with `reconcile_required`.
- `vm.device.create` receives only the path. TrueNAS does not change ownership.
- Ordering (Phase 2): both files are staged and re-verified before the zvol and `vm.create`.

## 5. Checks already enforced (offline tests)

- Owner uid/gid and mode 0600 on both staged files, recorded in the manifest (`test_staged_media_owner_group_and_mode_match_service_account`).
- Staged-media probe refusal before a write, for ISO and seed, before and after file creation (existing staging-failure tests).
- Pre-create re-verification of both files (Phase 2).
- Group-writable staging directory refuses before zvol and VM.

## 6. Open blocker: where the account comes from

The writer currently requires the selected account to equal the owner of an **active** QEMU process. On a host with **no running VM**, there is no such process, so the writer refuses before any write. That is a fail-closed design and is correct for safety, but it means Phase 4 needs an answer.

Options (decision needed after preflight):

1. Keep the active-process rule and require a separately running, unrelated VM on the host before the disposable test. Simplest, but it puts an unrelated VM at risk of being the reference.
2. Read the configured libvirt QEMU user (`user =` / `group =` in libvirt's `qemu.conf` on this TrueNAS build) and require it to match the account and the `runuser` probe. Needs the path and value confirmed by the preflight. Not implemented here.

No change is made to the gate in Phase 3.

## 7. Not proven

- Live read by the VM process (`live_vm_process_readable_verified` stays `False` until a running-VM check in Phase 4).
- Whether TrueNAS's VM sandbox can see `/mnt/<pool>/...` at the same path.
- Behaviour of the installer reading a 0600 CDROM image owned by the VM account.
- Anything on a non-Beta.2 build.

## 8. Read-only preflight

Run as root on the TrueNAS host, replacing `POOL`. It makes no writes, no middleware calls, and starts no VM. Save as `preflight.sh`, then `bash preflight.sh <pool>`:

```sh
#!/bin/sh
# Read-only staging-permission preflight. Writes nothing, starts nothing.
POOL="${1:?usage: preflight.sh <pool>}"
ACCT="${2:-libvirt-qemu}"
echo "== account"
getent passwd "$ACCT" || echo "MISSING account $ACCT"
echo "== runuser"
command -v runuser || echo "MISSING runuser"
echo "== active qemu-system process owners (euid comm)"
ps -eo euid=,comm= | awk '$2 ~ /qemu-system/ {print}'
echo "== libvirt qemu user/group config"
grep -sE '^[[:space:]]*(user|group)[[:space:]]*=' /etc/libvirt/qemu.conf || echo "no user/group in /etc/libvirt/qemu.conf"
echo "== linux-vms dataset"
zfs list -H -o name,mountpoint,type -r "$POOL/linux-vms" 2>&1 || echo "no $POOL/linux-vms dataset"
echo "== ancestor owner/mode"
stat -c '%U:%G %a %n' "/mnt/$POOL" "/mnt/$POOL/linux-vms" 2>&1
echo "== account traversal (x) of ancestors"
for d in "/mnt/$POOL" "/mnt/$POOL/linux-vms"; do
  if runuser -u "$ACCT" -- sh -c 'test -x "$1"' sh "$d" 2>/dev/null; then
    echo "x OK $d"
  else
    echo "x DENIED $d"
  fi
done
echo "== done"
```

### Expected output (what to paste back)

Go-ready on this host means every line below is true:

```
== account
libvirt-qemu:x:<uid>:<gid>:...        # uid > 0, not root
== runuser
/usr/sbin/runuser
== active qemu-system process owners (euid comm)
<uid> qemu-system-x86                 # present only if a VM is running; uid must equal the account uid
== libvirt qemu user/group config
user = "libvirt-qemu"                 # or "no user/group ..." (then section 6 option 2 is unavailable)
group = "libvirt-qemu"
== linux-vms dataset
<pool>/linux-vms   /mnt/<pool>/linux-vms   filesystem
== ancestor owner/mode
root:root 755 /mnt/<pool>             # not group/other-writable; owner root or operator
root:root 755 /mnt/<pool>/linux-vms
== account traversal (x) of ancestors
x OK /mnt/<pool>
x OK /mnt/<pool>/linux-vms
== done
```

Blockers, by line: `MISSING account` or `DENIED` lines stop Phase 4. An empty active-process section is the section 6 blocker (not a failure on its own). A `qemu.conf` user that differs from the account stops Phase 4.

The script only reads state. `zfs list` and `stat` are read-only. `runuser ... test -x` executes a no-op check as the account.

## 9. Next

Phase 4 needs: the preflight output, a decision on section 6, and an explicitly named disposable pool and VM.
