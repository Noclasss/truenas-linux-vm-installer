#!/bin/sh
# Read-only staging-permission preflight for the Ubuntu Desktop create-only writer.
# Reads state only: no writes, no middleware calls, no VM start.
# Usage: sh scripts/staging-preflight.sh <pool> [account]
POOL="${1:?usage: sh staging-preflight.sh <pool> [account]}"
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
