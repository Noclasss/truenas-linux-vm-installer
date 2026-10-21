# Exact-Build Middleware Response Contracts Plan

> **Historical plan:** the no-merge instruction below applied to the original task only. PRs #3 and #2 were later explicitly authorized and merged on 2026-10-21. Current status is recorded in the README and implementation roadmap; no live install or boot validation is implied.

> **For Hermes:** Implement this plan with offline tests only. Never invoke TrueNAS methods beyond the already-authorized read-only probe, and do not add a live install path.

**Goal:** Extend the sanitized TrueNAS 26.0.0-BETA.2 compatibility fixture and checker to compare return schemas as well as accepted request schemas, so later preflight/installer code is based on reviewed response contracts.

**Architecture:** Reuse the operator-provided local method-definition capture, which contains method metadata but no runtime inventory values. Normalize only schema constraints and selected annotations, retain the full raw capture outside Git, and add offline tests proving changed/missing return schemas fail compatibility checks. Keep all middleware calls in the probe limited to `system.version` and `core.get_methods`; this work must not invoke any schema-described query or write method.

**Tech Stack:** Python standard library, existing bounded schema normalizer, sanitized JSON fixture, `unittest`.

---

## Safety boundaries

- The original PR #2 work targeted `feat/multi-distro-installer`; its later preflight/schema follow-up is stacked separately on `fix/readonly-preflight-memory-units` as PR #3. Never push to `main`, merge, or deploy.
- Do not connect to TrueNAS or run any middleware method during implementation or tests.
- Use only schema definitions from the local operator-provided capture; never copy runtime inventory, credentials, private paths, or the raw capture into Git.
- Keep `LIVE_PROFILE_ALLOWLIST` empty and all distro profiles experimental.
- Schema comparison is not write acceptance, runtime-response validation, or guest-boot evidence.

## Task 1: Add response-schema comparison tests

**Files:** `planning/test_compatibility.py`, `planning/test_installer_core.py`.

1. Add fixtures with a simple `returns` schema and assert normalized method contracts retain it.
2. Add compatibility tests where a return type, required property, or enum differs; each must fail closed.
3. Test malformed/unsupported response-schema keywords and missing runtime `returns` when the fixture requires them.
4. Run focused tests and confirm the new assertions fail before implementation.

## Task 2: Extend schema normalization and compatibility

**Files:** `planning/installer_core/schema.py`, `planning/installer_core/compatibility.py`.

1. Keep request validation fail-closed for unsupported constraints.
2. Add bounded response-contract normalization that can preserve known response annotations such as `format` for comparison without treating them as validated runtime values.
3. Normalize `returns` alongside `accepts`, preserving method-local definitions and schema composition constraints.
4. Verify compatibility compares both contracts while the runtime probe still calls only `system.version` then `core.get_methods`.
5. Run focused tests and the compatibility test suite.

## Task 3: Sanitize the exact-build fixture

**Files:** `planning/fixtures/truenas-26.0.0-beta.2-method-contracts.json`, `planning/test_compatibility.py`.

1. Extract return-schema definitions for the exact 19 already-selected methods from the operator's private, uncommitted capture; do not copy that capture into the repository.
2. Retain only schema constraints needed for stable comparison; remove descriptions, examples, and unrelated descriptive metadata. Preserve request schemas unchanged.
3. Do not copy host values or commit the raw capture.
4. Add fixture tests for method count, format, and required return contracts.
5. Run offline comparison against the local capture and ensure all 19 request and return contracts normalize identically.

## Task 4: Documentation, review, and delivery

**Files:** `README.md`, `planning/README.md`, `docs/implementation-roadmap.md`.

1. State that the read-only probe compares request and return schemas but invokes only `system.version` and `core.get_methods`.
2. State that this improves contract review only; live method behavior, write acceptance, and VM boot remain unverified.
3. Run the complete unit suite, compilation, static preview, limited secret/private-IP scan, and `git diff --check`; inspect the full intended diff.
4. For the original PR #2 delivery, commit and push only the feature branch if checks pass. Read back its SHA and exact-head Actions runs; confirm `main` is unchanged and PR #2 remains draft/open/unmerged. The subsequent PR #3 follow-up remains on its separately named fix branch.
5. Do not close issue #1 or mark any profile supported.

## Completion status (2026-10-21)

The initial plan covered 19 selected methods. The sanitized fixture was later extended with `vm.device.convert` as method 20, and the operator reran the exact-build read-only comparison from commit `1b10b439f9bf2d10ff1afa0da228b30416043bc9`. All 20 request/return contracts matched for `TrueNAS-26.0.0-BETA.2`; only `system.version` and `core.get_methods` were called. The original 19-method steps above describe the plan's starting scope, not the final fixture or evidence. PR #3 carries the follow-up on `fix/readonly-preflight-memory-units`, stacked on PR #2's `feat/multi-distro-installer` branch. This remains schema evidence only; no write acceptance, live preflight, VM creation, or boot is claimed.
