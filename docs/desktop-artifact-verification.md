# Verify a local Ubuntu Desktop ISO

The local checker verifies the detached SHA256SUMS signature, requires the pinned
Ubuntu signing fingerprint, matches the exact Desktop filename against the
checked-in digest, and streams the ISO to check its exact size and SHA-256.
It refuses missing tools, invalid signatures, other signers, duplicate entries,
mismatched pins, symlinks, non-regular files, and files that change during hashing.

Use a trusted Linux workstation with Python 3 and gpgv already available. Obtain
the ISO, SHA256SUMS, SHA256SUMS.gpg and a current Ubuntu public keyring through
trusted routes. This command neither downloads files nor installs packages.
The expected signing fingerprint is fixed in ubuntu_desktop.py; a keyring
containing an unrelated trusted key cannot substitute a different signer.

From the reviewed repository checkout:

~~~sh
python3 -m planning.desktop_integrity \
  --iso /path/to/ubuntu-26.04.1-desktop-amd64.iso \
  --manifest /path/to/SHA256SUMS \
  --signature /path/to/SHA256SUMS.gpg \
  --keyring /path/to/ubuntu.gpg
~~~

Verification copies the small inputs into a private temporary directory, invokes
gpgv with an explicit keyring and isolated home, then removes its own temporary
files. It does not change the source files, personal GnuPG configuration or any
TrueNAS resource. The ISO is read through one open descriptor; concurrent
modification is refused. Run against files in a trusted directory. The result
describes bytes read during this invocation and is not a reusable authorization
token; a future writer must verify its actual staged bytes again.

On success, stdout contains a JSON report with the image digest, size, signing
fingerprint and checksum-manifest digest. Failures exit nonzero without a success
report. The report always sets source_id_verified and live_install_allowed to
false. No profile is enabled and no VM is created or started.

## Evidence still required

Adding this checker and passing synthetic tests does **not** verify the actual
Canonical manifest or 6.48 GB ISO. Run it against the actual pinned files before
recording that evidence. The exact ISO's casper/install-sources.yaml still needs
inspection, followed by reviewed live-writer integration and separately scoped
disposable installation and graphical-login validation.

The offline suite includes refusal tests and a real cryptographic round trip
using a generated disposable test key if GnuPG tools are already present. That
test performs no network requests and does not use the Ubuntu signing key.
A skipped crypto test must not be reported as real-signature verification.

## References

- [Ubuntu image verification and key provenance](https://documentation.ubuntu.com/security/software-integrity/image-verification/)
- [GnuPG verifier options](https://gnupg.org/documentation/manuals/gnupg/gpgv.html)
- [GnuPG machine-readable status format](https://github.com/gpg/gnupg/blob/master/doc/DETAILS)
