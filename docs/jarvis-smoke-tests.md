# Jarvis smoke-test handoff

## Objective and scope

Run smoke tests for the reviewed merged commit, preserve attributable evidence,
and report actual results. The offline driver creates only private local test
files and an evidence directory. It cannot create a TrueNAS VM or dataset,
download an ISO, enable a profile, resume a deployment or delete host resources.

Use an existing trusted Linux execution environment. Do not install packages on
TrueNAS to make tests pass. Do not change agent credentials, production profiles,
networking, storage or existing VMs. This handoff is not a live installer release.

## 1. Isolate and pin the checkout

Set REVIEWED_SHA to the full merge SHA supplied with this handoff. Do not run an
unreviewed moving branch, overwrite a dirty checkout or execute downloaded shell
code. A new clone avoids interfering with Jarvis's existing worktrees.

~~~sh
: "${REVIEWED_SHA:?Set the reviewed full merge SHA first}"
test "${#REVIEWED_SHA}" -eq 40 || exit 1
case "$REVIEWED_SHA" in *[!0-9a-f]*) exit 1 ;; esac
smoke_root=$(mktemp -d "${TMPDIR:-/tmp}/linux-vm-smoke.XXXXXXXX") || exit 1
git clone --no-checkout -- https://github.com/Noclasss/truenas-linux-vm-installer.git "$smoke_root/repo" || exit 1
git -C "$smoke_root/repo" checkout --detach "$REVIEWED_SHA" || exit 1
(
  cd "$smoke_root/repo" || exit 1
  python3 -B -m planning.smoke_tests \
    --expected-commit "$REVIEWED_SHA" \
    --output "$smoke_root/evidence"
)
smoke_exit=$?
printf 'Smoke exit: %s\nEvidence: %s\n' "$smoke_exit" "$smoke_root/evidence"
~~~

Retain the exit code. Do not treat the final printf succeeding as smoke success.
Copy the evidence into your existing durable project records before temporary
storage is cleaned up. Do not delete another run's evidence.

The output directory must be new, outside the checkout, with a pre-existing
trusted parent. It is created mode 0700; logs and report.json are mode 0600.
The driver refuses a wrong SHA or tracked/untracked checkout changes. Use -B to
avoid generating bytecode before that check. Existing ignored Python cache files
do not count as source changes.

## 2. Interpret the offline result

The driver runs the full suite, compiles tracked Python in memory, syntax-checks
tracked shell scripts, invokes the local preview launcher and checks commit
whitespace. It then exercises all four non-applicable profile previews, private
2 MiB CIDATA generation with synthetic credentials, persistent Desktop fake
creation/read-back, and recovery from a lost VM-create response.

Expected outcomes:

- exit 0 and report.status = passed;
- the observed test count is recorded, not copied from an older handoff;
- zero skipped tests and real_crypto_test_passed = true;
- all four previews prohibit apply/start/autostart and host-network modification;
- fake success acknowledges nine resources;
- interrupted creation is reported for manual review without retry or deletion;
- checkout still clean and at the exact reviewed SHA;
- actual_iso_verified, host_preflight_run and live_install_allowed remain false.

A missing GnuPG tool may cause a crypto test to skip. The driver reports
incomplete and exits nonzero; do not describe that as full smoke verification.
failed also exits nonzero and keeps report.json and any completed logs. A
preflight refusal happens before evidence-directory creation. Check the terminal
error in that case. The suite has a 180-second bound; if a slow environment hits
it, report the timeout rather than silently skipping tests or weakening guards.

CI runs this same driver against its checkout SHA in addition to the ordinary
unit/compile/preview checks. CI success is offline evidence, not host evidence.

## 3. Verify the actual Desktop artifact when available

This is a separate optional stage. The offline suite uses synthetic artifacts.
If trusted local files already exist, use the
[artifact verification command](desktop-artifact-verification.md) against the
actual pinned ISO, checksum manifest, detached signature and Ubuntu public
keyring. Preserve its JSON report and process exit code separately.

If files are missing, report their absence. A full ISO download is approximately
6.48 GB; use an explicitly selected local cache with sufficient space and the
official URLs pinned in planning/ubuntu_desktop.py if download is authorized.
Do not use a TrueNAS data pool as an implicit scratch/download target. Never
modify the pins merely to make a mismatch pass.

After signature and byte verification, inspect casper/install-sources.yaml
from that same ISO on a workstation using existing trusted ISO tooling.
Record the actual source IDs and catalogue content/hash as separate evidence.
The verifier does not inspect this catalogue and its report correctly leaves
source_id_verified false. Do not infer the ID from web documentation or from
the mock's candidate string. Do not attach media or boot a VM for this stage.

## 4. Optional existing read-only TrueNAS route

Only if Jarvis already has a verified read-only route and explicit intended
test selections, run the checked-in preflight from the pinned checkout in the
TrueNAS shell. Do not invent pool/bridge names or silently reuse a production VM.
The VM name below is a proposed new name; this command does not create it.

~~~sh
: "${SMOKE_VM_NAME:?Set the proposed disposable name}"
: "${SMOKE_POOL:?Set the explicitly selected pool}"
: "${SMOKE_BRIDGE:?Set the selected existing bridge}"
python3 -B planning/check_readonly_preflight.py \
  --profile ubuntu-desktop \
  --vm-name "$SMOKE_VM_NAME" \
  --pool "$SMOKE_POOL" \
  --bridge "$SMOKE_BRIDGE" \
  --vcpus 2 --memory-mib 6144 --disk-gib 32 \
  --host-reserve-mib 1024 --pool-reserve-mib 1024
~~~

The resource sizes here are smoke-plan inputs, not installation recommendations
or reservations. Capture exit status and sanitized output privately. The current
fixture is exact to TrueNAS-26.0.0-BETA.2. A different build/schema must refuse;
record it as a compatibility blocker, not a reason to bypass validation.
If there is no verified route or the selections are missing, mark this stage
not run and continue the independent local work.

## 5. Stop before live writes

There is still no reviewed production live writer. Do not replace the fake
backend type checks, populate LIVE_PROFILE_ALLOWLIST or use ad-hoc midclt writes
to simulate an installer success. Offline smoke and preflight do not establish
unattended installation, completion/poweroff, seed access or graphical login.

Before a future live test, prepare a concrete disposable VM/storage plan and
its reviewed writer revision, fresh read-only checks, actual artifact evidence,
ownership/read-back strategy and recovery procedure. Obtain any missing target
selection/authorization then; do not ask again for already authorized local
testing, code fixes or Git publication.

## Return this result to Darryl

- Exact checkout SHA and clean-state result.
- Offline exit/status, observed test count, skips and real crypto result.
- Evidence directory and private report/log locations.
- Actual ISO verification: passed / failed / not run, with reason.
- Exact ISO source catalogue: inspected / not inspected, with evidence.
- TrueNAS read-only preflight: build, pass/fail/not run, and reason.
- Host writes: none for this handoff.
- Any regression: reproduce, fix on a separate branch, run appropriate checks
  and provide its commit/PR and exact-SHA CI. Never report a mock as a VM install.
