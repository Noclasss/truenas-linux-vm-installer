# Verify a local Ubuntu Desktop ISO

The local checker verifies the detached SHA256SUMS signature, requires the pinned
Ubuntu signing fingerprint, matches the exact Desktop filename against the
checked-in digest, and streams the ISO to check its exact size and SHA-256.
It refuses missing tools, invalid signatures, other signers, duplicate entries,
mismatched pins, symlinks, non-regular files, and files that change during hashing.

Use a trusted Linux workstation with Python 3 and GnuPG (`gpgv` preferred; `gpg`
with an isolated keyring is supported). For ISO catalogue inspection, the operator
prefers `xorriso` and falls back to `7z` stdout extraction. Obtain the ISO,
SHA256SUMS, SHA256SUMS.gpg and a current Ubuntu public keyring through trusted
routes. This command neither downloads files nor installs packages.
The expected signing fingerprint is fixed in ubuntu_desktop.py; a keyring
containing an unrelated trusted key cannot substitute a different signer.

## Reproducible helper tooling

Run verification on a trusted Linux helper or disposable helper container, never
on TrueNAS. For a fresh Debian/Ubuntu helper, use the distribution's authenticated
package repository for `python3`, `gnupg`/`gpgv`, `coreutils`, and either
`xorriso` or `7zip`; do not use packages installed on the NAS as the verifier.
The code resolves tools through `os.defpath` rather than an ambient caller PATH.
For privately extracted packages, expose only the verified tool directory through
a controlled helper wrapper/container; do not weaken tool discovery to an
untrusted PATH.

A no-system-install Debian workflow is also supported: use APT with the Debian
archive keyring and signed `InRelease`/`Packages` indexes, download the selected
`.deb` packages with `apt-get download`, compare each package SHA-256 with its
signed package-index entry, and extract with `dpkg-deb -x` into a mode-0700
private directory. Do not use `dpkg -i`. Include the package dependencies in
the helper root or use a disposable Debian helper image. Preserve the repository,
package versions, package hashes, and verifier/extractor versions in the run
record. Never install these packages on TrueNAS.

Establish Ubuntu key provenance independently of the checksum: compare the
primary fingerprint in the downloaded official Ubuntu archive keyring with the
fingerprint published by Ubuntu's official image-verification documentation (or
an already trusted Ubuntu key). Then verify `SHA256SUMS.gpg` over the exact
`SHA256SUMS`, require the pinned fingerprint, match the exact ISO filename and
pinned digest, and hash the exact ISO bytes. A checksum-only match is not
acceptable evidence.

For the supported catalogue extraction, `desktop_catalogue.verify_desktop_source_id`
uses `xorriso` when present and otherwise `7z`'s bounded stdout mode. It extracts
only `casper/install-sources.yaml` and refuses failed or empty extraction. On
Linux, it opens the ISO without following a symlink, passes the same descriptor
to the extractor through `/proc/self/fd` and `pass_fds`, then checks both the
opened file and source path identities. This prevents pathname replacement from
redirecting the extractor to different bytes. Example inspection from the
repository root:

~~~sh
python3 -c 'from pathlib import Path; from planning.desktop_catalogue import verify_desktop_source_id; print(verify_desktop_source_id(Path("/path/to/ubuntu-26.04.1-desktop-amd64.iso"), "ubuntu-desktop-minimal"))'
~~~

The catalogue result proves only that the source ID appears in those ISO bytes;
it does not prove unattended installation or guest boot behavior.

From the reviewed repository checkout:

~~~sh
python3 -m planning.desktop_integrity \
  --iso /path/to/ubuntu-26.04.1-desktop-amd64.iso \
  --manifest /path/to/SHA256SUMS \
  --signature /path/to/SHA256SUMS.gpg \
  --keyring /path/to/ubuntu.gpg
~~~

Verification copies the small inputs into a private temporary directory, invokes
gpgv (or gpg with an explicit keyring and isolated home), then removes its own
temporary files. It does not change the source files, personal GnuPG configuration or any
TrueNAS resource. The ISO is read through one open descriptor; concurrent
modification is refused. Run against files in a trusted directory. The result
describes bytes read during this invocation and is not a reusable authorization
token; a future writer must verify its actual staged bytes again.

On success, stdout contains a JSON report with the image digest, size, signing
fingerprint and checksum-manifest digest. Failures exit nonzero without a success
report. The report always sets source_id_verified and live_install_allowed to
false. No profile is enabled and no VM is created or started.

## Fresh verification (2026-11-19)

The selected cached ISO and companion files were re-verified on the helper. The verifier packages were not installed
on TrueNAS. GnuPG 2.4.7 (`gpgv` preferred) came from Debian Trixie packages
validated through APT's signed Debian archive metadata. The GnuPG package set
used was:

| Debian package | Version | SHA-256 (matched signed Packages index) |
|---|---|---|
| `gpg` | `2.4.7-21+deb13u1+b5` | `3aeedf3d223e5e6aff08f03227af6ce308f4317472609f383350fd2ec20d33ae` |
| `gpgv` | `2.4.7-21+deb13u1+b5` | `104e4c57b98f0b883aa94c68fbe7ec7b48bcdec7c0f9dedf2a51a5054aaeb5ae` |
| `gpgconf` | `2.4.7-21+deb13u1+b5` | `14668f3f4847d2544bdff03c2ca8dbdd2fe0ad1bc7ded00941db291954c60f1b` |
| `gnupg-utils` | `2.4.7-21+deb13u1+b5` | `787d8ec1f2184544a18da2bfaa8ba4100da1aa52b33693e80e095f59c63b7e52` |
| `libassuan9` | `3.0.2-2` | `9d78ef8f016e7f24b5e20d1ce91939fe0f1f897e5f9059793201e930ded7eb8e` |
| `libgcrypt20` | `1.11.0-7+deb13u1` | `f0eb41f8cb40896e0822e9ab65611e8383c12b721c76c54764f744abb4a8c247` |
| `libgpg-error0` | `1.51-4` | `22b95570fd41c113ef6f5651563b4d748292844baab1278a46eb940c1dec2322` |
| `libksba8` | `1.6.7-2+b1` | `45bc1bfaf41a5e1136b1c1f58c495a950888da33a617de24202f3568e45b9572` |
| `libnpth0t64` | `1.8-3` | `37d489976ef91ca0165a7789a7a7ac5923ee3cfadf316ac3e87582e15d0523d7` |

7-Zip 25.01 came from the `7zip` package `25.01+dfsg-1~deb13u2`, whose
SHA-256 `b2cb210bee3be6a10230b38b8645d71846d19364b157f44fd08e1a54cae1652f`
matched the signed APT package index. The packages were extracted into private
helper roots; no system package installation was performed. APT repository and
package-index provenance records are retained outside Git in the local evidence
workspace.

Ubuntu's official image-integrity documentation identifies the Ubuntu CD Image
Automatic Signing Key (2012) fingerprint as
`843938DF228D22F7B3742BC0D94AA3F0EFE21092`. The downloaded official Ubuntu
archive keyring contained that exact primary fingerprint. The signature and
artifact verification command exited 0 using genuine GnuPG 2.4.7 through the
isolated `gpgv` executable. The real suite test's skip guard checks `gpg` and
`gpgconf` on the test process's `PATH`; both were exposed from the isolated helper
directory. `RealSignatureTests.test_real_signature_tampering_and_wrong_identity`
ran and passed, using genuine `gpg` with a disposable test key and rejecting both
a modified manifest and the wrong signer. The exact files reported:

- ISO: `ubuntu-26.04.1-desktop-amd64.iso`, 6,482,409,472 bytes,
  SHA-256 `601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f`.
- SHA256SUMS SHA-256:
  `4adde9f7e7760a624db5821ad75679a76b9cde1e0d3e9cb2117cfe7d8043fa43`.
- SHA256SUMS.gpg SHA-256:
  `055292b53dffbc5387c1691e8c2357df0b4db88e7d7fd4c1b36fadf068479c5d`.
- Ubuntu archive keyring SHA-256:
  `80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31`.
- Actual repository catalogue verification used the privately extracted 7-Zip
  tool and exited 0. It extracted `casper/install-sources.yaml` (1,229 bytes,
  SHA-256 `700af50d66da6d97725111de4773f796c67e1b2aeb622379d8409a08af52f528`)
  from the same ISO and found both `ubuntu-desktop-minimal` and `ubuntu-desktop`.
  An independent read-only libarchive ISO9660 extraction returned the same
  catalogue digest, and the ISO remained unchanged during both inspections.
- In the catalogue, `ubuntu-desktop-minimal` is `default: true` and described as
  “A minimal but usable Ubuntu Desktop”; the declared `minimal.squashfs` layer is
  3,436,847,104 bytes. This makes it the best-supported initial source choice,
  but it does not prove the unattended install, offline operation, VM boot, or
  graphical login. Those remain separate guest-validation gates.

The isolated verification tools were invoked through the private helper wrapper
because the source code deliberately searches `os.defpath`, not ambient PATH.
No TrueNAS middleware call or write was made during artifact verification.

## Historical evidence (2026-11-17)

The checked-in verifier at checkout `fd620d0e04ad9685a05d99a2b0e7a716414dfda8`
was run against the cached pinned files using `gpgv` 2.4.7 staged in private
scratch from signed Debian snapshot metadata; no global package installation or
TrueNAS operation occurred. It verified:

- Detached `SHA256SUMS` signature: valid, pinned signer fingerprint
  `843938DF228D22F7B3742BC0D94AA3F0EFE21092`.
- Manifest SHA-256: `4adde9f7e7760a624db5821ad75679a76b9cde1e0d3e9cb2117cfe7d8043fa43`.
- ISO size: `6482409472` bytes; SHA-256:
  `601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f`.
- `xorriso` 1.5.6 extracted `casper/install-sources.yaml` from that ISO. It
  lists `ubuntu-desktop-minimal` as the default source and `ubuntu-desktop` as
  the other source. A post-extraction ISO hash still matched the pin.

The verifier continues to report `source_id_verified: false` because it checks
artifact integrity, not the catalogue; the catalogue evidence is recorded
separately. Its `live_install_allowed` result remains false. These checks do
not validate autoinstall behavior, VM creation, guest boot, or graphical login.
Any future writer must verify the exact staged bytes again.

## Evidence still required

Reviewed live-writer integration and separately authorized disposable
installation, poweroff, and graphical-login validation remain outstanding.

The offline suite includes refusal tests and a real cryptographic round trip
using a generated disposable test key if GnuPG tools are already present. That
test performs no network requests and does not use the Ubuntu signing key.
A skipped crypto test must not be reported as real-signature verification.

## References

- [Ubuntu image verification and key provenance](https://documentation.ubuntu.com/security/software-integrity/image-verification/)
- [GnuPG verifier options](https://gnupg.org/documentation/manuals/gnupg/gpgv.html)
- [GnuPG machine-readable status format](https://github.com/gpg/gnupg/blob/master/doc/DETAILS)
