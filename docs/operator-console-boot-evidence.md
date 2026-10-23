# Operator-provided console-only Ubuntu Server boot evidence

- **Test date:** 2026-10-23
- **TrueNAS build:** `TrueNAS-26.0.0-BETA.2`
- **Evidence source:** operator-reported middleware read-back and an operator-provided console screenshot.

A separately authorized manual test used a pinned Ubuntu Server 26.04.1 LTS cloud image, converted to a fresh 16 GiB non-sparse zvol. The VM was configured with 2 vCPUs, 4096 MiB RAM, UEFI, Secure Boot disabled, and autostart disabled. It had one VirtIO disk and a SPICE display bound to loopback; **no NIC or seed/CDROM device was attached**.

The operator started the VM with memory overcommit disabled. Middleware read-back reported the VM as `RUNNING`. The console screenshot subsequently showed Ubuntu 26.04.1 LTS at the `ubuntu login:` prompt on `tty1`. This confirms the manually configured guest reached its console login prompt; it does not establish a successful guest login, cloud-init/NoCloud provisioning, or first-boot configuration.

This was an operator-run test, not an installer run. It does not validate installer automation, seed-media creation/attachment or permissions, guest credentials, network behavior, or automated cleanup. No guest password was configured or entered for this boot test. The VM was left as a disposable test resource; cleanup was not part of this test.

All distro profiles remain experimental, and `LIVE_PROFILE_ALLOWLIST` remains empty. Do not infer Desktop, Debian, networking, first-boot, or installer support from this result.
