# Ubuntu Desktop staged media: bytes, owners, and permissions

**Status:** design plus offline tests. The live-writer gate (`LIVE_PROFILE_ALLOWLIST`) stays empty. Nothing here has been run on TrueNAS. Live read access by the VM process is **not** proven.

## 1. Who writes the bytes

Only `DesktopCreateOnlyWriter` (run by the authenticated root operator process) writes the staged ISO and seed. It creates each file with `O_CREAT|O_EXCL|O_NOFOLLOW`, inside the per-VM filesystem dataset mountpoint that TrueNAS returned from `pool.dataset.create`. Nothing else writes there, and no path outside that mountpoint is used.

Destination: `/mnt/<pool>/linux-vms/<vm>/` (mountpoint of the dataset `<pool>/linux-vms/<vm>`):

| File | Source | Size rule | Mode | Owner/group |
|---|---|---|---|---|
| `ubuntu-installer.iso` | verified source ISO | exactly `ARTIFACT_SIZE_BYTES` | 0600 | VM-process account |
| `cidata.img` | build-time seed (pinned, see #47/#51) | exactly `SEED_MEDIA_SIZE_BYTES` | 0600 | VM-process account |

## 2. Owner and mode that let the VM process read

- **Owner is the VM-process account**, not root. The account is the libvirt QEMU user named in `/etc/libvirt/qemu.conf` when a user line exists (rule 1), or is confirmed by the euid of every running `qemu-system` process when none exists (rule 2). Either way it must equal the account in the target record (section 6).
- **Group** is the account's primary group. A `group =` line in `qemu.conf` is recorded but not enforced: the VM process reads these files as owner (mode 0600), so the group does not change access.
- **Mode 0600.** The owner can read; group and other have no bits. Root can still read for re-hash because DAC does not apply to root.
- **Why not 0644 or root-owned 0600?** The 2026-10-22 probe showed a mode-0600 root-owned file is unreadable by the VM account. A group/world-readable seed would expose the password hash. The writer never widens the mode; the owner change is what grants access.
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

## 6. Account source (hybrid, fail-closed)

Checks run in this order, before any write. `/etc/libvirt/qemu.conf` is read read-only (`O_NOFOLLOW`, regular file, at most 64 KiB). A missing, unreadable, oversized, non-regular, or non-UTF-8 file is refused. Then the account must exist, be non-root, and pass the `runuser` probe (unchanged). Then exactly one rule applies. The rule used is recorded in the manifest as `account_source.rule`.

| Rule | Condition in `qemu.conf` | Required | Recorded `rule` |
|---|---|---|---|
| 1 | exactly one active quoted `user = "<name>"` line | `<name>` equals the target account; every running `qemu-system*` process has the account's euid (zero processes is allowed) | `qemu_conf_user_line` |
| 2 | **no** active user line | at least one running `qemu-system*` process, and **all** of them have the account's euid | `process_agreement_without_user_line` |

Refused before any write, under either rule:

- file missing, unreadable, oversized, non-regular, or non-UTF-8;
- more than one active user line, or more than one active group line;
- rule 1: configured user is not the target account;
- rule 2: no running `qemu-system*` process;
- any running `qemu-system*` process whose euid differs from the account;
- account missing, root, or `runuser` probe fails.

The `group =` line stays optional and is recorded, not enforced (section 2).

**Why rule 2 needs a running VM.** With no user line, libvirt uses its built-in default. The only evidence of the account is then the euid of the VM processes that are running. Requiring at least one process and requiring all of them to agree is the strongest evidence available without starting a VM. If no VM is running, the writer refuses. This is a deliberate limit: rule 2 cannot be satisfied on a host with no running VM.

**Tests.** Unit tests in `QemuConfSourceTests`: rule 1 (matching, mismatched, root, missing account, disagreeing process, agreeing process, no process); rule 2 (no process refuses; two agreeing processes proceed and record `process_agreement_without_user_line`; one disagreeing process refuses); duplicate user lines refused; missing file refused. Writer-level tests assert zero client writes and no operation record for each refusal, and assert the recorded `rule` for each accepted run.

## 7. Not proven

- Live read by the VM process (`live_vm_process_readable_verified` stays `False` until a running-VM check in Phase 4).
- Under rule 2, the process list is the only evidence of the account, so a misconfigured libvirt default that the running processes do not reflect is not detectable without a live VM.
- Under rule 1, whether the configured user is the one the VM process actually runs as is confirmed only by the process list, if any VM is running.
- Whether TrueNAS's VM sandbox can see `/mnt/<pool>/...` at the same path.
- Behaviour of the installer reading a 0600 CDROM image owned by the VM account.
- Anything on a non-Beta.2 build.

## 8. Read-only preflight

The same script is checked in as `scripts/staging-preflight.sh` and can be fetched at a pinned commit; see the README "Read-only staging preflight" section.

Run as root on the TrueNAS host. It reads state only: no writes, no middleware calls, no VM start, and the `runuser` check runs a no-op `test -x` as the account. Save the script as `preflight.sh` and run `sh preflight.sh <pool>` (the shebang is `/bin/sh`; `bash preflight.sh <pool>` also works):

```sh
#!/bin/sh
# Read-only staging-permission preflight for the Ubuntu Desktop create-only writer.
# Reads state only: no writes, no middleware calls, no VM start.
# Usage: sh preflight.sh <pool> [account]
POOL="${1:?usage: sh preflight.sh <pool> [account]}"
ACCT="${2:-libvirt-qemu}"
VMS="/mnt/$POOL/linux-vms"
POOLMP="/mnt/$POOL"

echo "== account"
getent passwd "$ACCT" || echo "MISSING account $ACCT"

echo "== runuser"
command -v runuser || echo "MISSING runuser"

echo "== running qemu-system processes (euid comm)"
ps -eo euid=,comm= | awk '$2 ~ /qemu-system/ {print}'

echo "== libvirt qemu user/group config (/etc/libvirt/qemu.conf)"
grep -sE '^[[:space:]]*(user|group)[[:space:]]*=' /etc/libvirt/qemu.conf || echo "no active user/group line in /etc/libvirt/qemu.conf"

echo "== linux-vms dataset"
zfs list -H -o name,mountpoint,type -r "$POOL/linux-vms" 2>&1 || echo "no $POOL/linux-vms dataset (writer will create)"

echo "== pool mountpoint and linux-vms owner/mode"
if [ -e "$POOLMP" ]; then
  stat -c '%U:%G %a %n' "$POOLMP"
else
  echo "MISSING pool mountpoint $POOLMP"
fi
if [ -e "$VMS" ]; then
  stat -c '%U:%G %a %n' "$VMS"
else
  echo "absent (writer will create) $VMS"
fi

echo "== account traversal (x) of existing paths"
for d in "$POOLMP" "$VMS"; do
  if [ ! -e "$d" ]; then
    if [ "$d" = "$VMS" ]; then
      echo "absent (writer will create) $d"
    else
      echo "MISSING pool mountpoint $d"
    fi
    continue
  fi
  if runuser -u "$ACCT" -- sh -c 'test -x "$1"' sh "$d" 2>/dev/null; then
    echo "x OK $d"
  else
    echo "x DENIED $d"
  fi
done

echo "== done"
```

### Expected output (what to paste back)

Go-ready on this host means these lines hold. Values in angle brackets are placeholders; the example lines show the shape only:

```
== account
libvirt-qemu:x:<uid>:<gid>:...          # present, uid > 0
== runuser
/usr/sbin/runuser
== running qemu-system processes (euid comm)
<empty>                                  # no VM running is acceptable; if present, euid must equal the account uid
== libvirt qemu user/group config (/etc/libvirt/qemu.conf)
user = "libvirt-qemu"                    # exactly one active user line naming the account
group = "libvirt-qemu"                   # optional
== linux-vms dataset
<pool>/linux-vms   /mnt/<pool>/linux-vms   filesystem   # or "no ... (writer will create)"
== pool mountpoint and linux-vms owner/mode
root:root 755 /mnt/<pool>                # not group/other-writable
absent (writer will create) /mnt/<pool>/linux-vms   # or: root:root 755 /mnt/<pool>/linux-vms
== account traversal (x) of existing paths
x OK /mnt/<pool>
x OK /mnt/<pool>/linux-vms               # or: absent (writer will create) /mnt/<pool>/linux-vms
== done
```

### Blockers

Stop Phase 4 if any of these appear:

- `MISSING account` or `MISSING runuser`.
- `MISSING pool mountpoint /mnt/<pool>`, or `x DENIED /mnt/<pool>`.
- `x DENIED /mnt/<pool>/linux-vms`, **when that path exists**.
- Any `qemu-system` row whose euid differs from the account uid (blocks under either rule).
- Account source, section 6:
  - `qemu.conf` has a user line that is not exactly the account name, or two user lines (rule 1 fails).
  - `qemu.conf` has **no** user line and **no** `qemu-system` row is shown (rule 2 needs at least one running VM process).

**Not blockers:** `absent (writer will create) /mnt/<pool>/linux-vms`, `no ... dataset (writer will create)`, and `x DENIED` lines for paths that do not exist.

The script reads state only; `zfs list` and `stat` are read-only.

## 9. Next

Phase 4 needs: the preflight output (section 8), and an explicitly named disposable pool and VM. Phase 4 does not start until you say "go live".
