"""Run offline smoke checks at a clean, exact Git commit; never contact TrueNAS.

Run as: python3 -B -m planning.smoke_tests --expected-commit FULL_SHA --output NEW_DIR
Evidence is local and private. Success never enables live deployment.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .desktop_autoinstall import build_desktop_seed_image
from .desktop_deploy_mock import DesktopRequest
from .desktop_ownership import (
    OwnershipError, PersistentFakeBackend, inspect_recovery, rehearse_persistent,
)
from .installer_core.safe_filesystem import (
    ensure_trusted_directory_chain, write_private_new_file,
)
from .mock_install import LIVE_PROFILE_ALLOWLIST
from .preview import PROFILES, build_plan
from .ubuntu_desktop import SEED_MEDIA_SIZE_BYTES


class SmokeError(RuntimeError):
    """A smoke gate failed; no installation is permitted."""


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def exercise_desktop(root: Path) -> dict:
    """Exercise real local seed creation and in-memory recovery with fake inputs."""
    require(not LIVE_PROFILE_ALLOWLIST, "live profiles must remain disabled")
    request = DesktopRequest("smoke-desktop", "testpool", "br0", 2, 6144, 32)
    for profile in PROFILES:
        plan = build_plan({"profile": profile, **asdict(request)})
        require(plan["apply_supported"] is False and plan["vm"]["start"] is False
                and plan["vm"]["autostart"] is False
                and plan["network"]["modify_host_network"] is False,
                "preview unexpectedly permits live operations")
    # This is deliberately a synthetic hash, never a usable account credential.
    seed = build_desktop_seed_image(
        root / "cidata.img", vm_name=request.name, realname="Synthetic Smoke",
        username="smoketest", password_hash="$6$smokesalt$" + "A" * 86,
        source_id="ubuntu-desktop-minimal",
    )
    require(seed.stat().st_size == SEED_MEDIA_SIZE_BYTES, "wrong seed size")
    require(seed.stat().st_mode & 0o777 == 0o600, "seed is not private")
    require(b"CIDATA     " in seed.read_bytes(), "seed label missing")
    backend = PersistentFakeBackend()
    manifest = root / "complete.json"
    rehearse_persistent(request, backend, manifest_path=manifest, confirmation="CREATE")
    report = inspect_recovery(manifest, backend)
    require(not report.attention and len(report.resources) == 9, "complete rehearsal did not reconcile")
    require(not report.can_continue and not report.can_delete, "recovery permits a live action")
    failed = PersistentFakeBackend(lose_response_after="vm:" + request.name)
    pending = root / "interrupted.json"
    try:
        rehearse_persistent(request, failed, manifest_path=pending, confirmation="CREATE")
    except OwnershipError:
        pass
    else:
        raise SmokeError("lost response did not stop rehearsal")
    before = failed.inventory()
    operations = list(failed.operations)
    recovery = inspect_recovery(pending, failed)
    require(bool(recovery.attention) and not recovery.can_continue and not recovery.can_delete,
            "interruption was not reported for manual review")
    require(failed.inventory() == before and failed.operations == operations,
            "recovery changed fake inventory")
    return {"profiles_previewed": list(PROFILES), "seed_bytes": seed.stat().st_size,
            "acknowledged_resources": len(report.resources), "lost_response_refused": True,
            "recovery_read_only": True, "live_install_allowed": False}


def _command(repo: Path, argv: list[str], timeout=180):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LC_ALL"] = "C"
    return subprocess.run(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout, check=False)


def _git(repo: Path, *args: str) -> str:
    result = _command(repo, ["git", *args], timeout=30)
    require(result.returncode == 0, "Git checkout inspection failed")
    return result.stdout.strip()


def check_checkout(repo: Path, expected_commit: str):
    require(bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit)),
            "expected commit must be a full lowercase 40-character SHA")
    require(_git(repo, "rev-parse", "HEAD") == expected_commit, "checkout is not at the expected commit")
    require(not _git(repo, "status", "--porcelain=v1", "--untracked-files=normal"),
            "checkout has tracked or untracked changes; use a clean detached worktree")


def suite_evidence(output: str) -> dict:
    count = re.search(r"^Ran ([0-9]+) tests? in ", output, re.MULTILINE)
    require(count is not None and int(count.group(1)) > 0, "unit-test count is missing")
    skipped = re.search(r"^OK \(skipped=([0-9]+)\)", output, re.MULTILINE)
    crypto = any(line.startswith("test_real_signature_tampering_and_wrong_identity ")
                 and line.endswith(" ... ok") for line in output.splitlines())
    return {"tests_run": int(count.group(1)), "skipped": int(skipped.group(1)) if skipped else 0,
            "real_crypto_test_passed": crypto}


def run_smoke(repo: Path, expected_commit: str, output: Path) -> dict:
    check_checkout(repo, expected_commit)
    output = Path(os.path.abspath(output))
    require(not output.is_relative_to(repo.resolve()), "evidence directory must be outside the checkout")
    ensure_trusted_directory_chain(output.parent, "smoke evidence")
    output.mkdir(mode=0o700)  # exclusive: existing evidence must never be overwritten
    report = {"commit": expected_commit, "scope": "offline smoke only",
              "status": "running", "checks": [], "live_install_allowed": False,
              "actual_iso_verified": False, "host_preflight_run": False}
    try:
        def step(name, argv, timeout=180):
            try:
                result = _command(repo, argv, timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                write_private_new_file(output / (name + ".log"), type(exc).__name__ + "\n")
                report["checks"].append({"name": name, "returncode": None,
                                         "error": type(exc).__name__})
                raise SmokeError(name + " could not complete") from exc
            log = result.stdout + result.stderr
            write_private_new_file(output / (name + ".log"), log)
            report["checks"].append({"name": name, "returncode": result.returncode})
            require(result.returncode == 0, name + " failed; inspect its private log")
            return log

        log = step("unit-tests", [sys.executable, "-B", "-m", "unittest", "discover",
                                  "-s", "planning", "-p", "test_*.py", "-v"])
        report["suite"] = suite_evidence(log)
        paths = _git(repo, "ls-files").splitlines()
        for relative in paths:
            path = repo / relative
            if relative.endswith(".py"):
                compile(path.read_bytes(), relative, "exec")
            elif relative.endswith(".sh"):
                step("shell-" + str(len(report["checks"])), ["sh", "-n", relative], 30)
        report["checks"].append({"name": "tracked-python-syntax", "returncode": 0})
        preview = step("launcher-preview", ["sh", "install.sh", "--config",
                                           "planning/examples/vm.json"], 30)
        plan = json.loads(preview)
        require(plan["apply_supported"] is False and plan["vm"]["start"] is False,
                "launcher preview permits an apply or start")
        step("whitespace", ["git", "diff", "--check", "HEAD^", "HEAD"], 30)
        with tempfile.TemporaryDirectory(prefix="desktop-smoke-") as scratch:
            report["desktop"] = exercise_desktop(Path(scratch))
        check_checkout(repo, expected_commit)
        report["status"] = ("passed" if report["suite"]["skipped"] == 0
                            and report["suite"]["real_crypto_test_passed"] else "incomplete")
    except Exception as exc:
        report["status"] = "failed"
        # No traceback or subprocess payload in the machine-readable summary.
        report["error"] = str(exc) if isinstance(exc, SmokeError) else type(exc).__name__
    write_private_new_file(output / "report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parent.parent
    try:
        report = run_smoke(repo, args.expected_commit, args.output)
    except Exception as exc:
        print(f"Smoke preflight refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
