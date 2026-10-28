#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Verify the shared workflow payload reached its destination byte-for-byte.

Scope: single-session-opencode (source) to a shared-payload destination
(orchestrator-opencode by default). Checks the 18 shared files and the three
anchors that prove the generated single-session skill was actually transformed.
Exit 0 pass, 1 drift, 2 destination missing (never a silent pass).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SHARED_FILES = (
    "tools/assemble-task.sh",
    "tools/inject-research.sh",
    "tools/plan_scopes.py",
    "tools/plan-scopes.sh",
    "tools/plan-scopes.bat",
    "tools/memory.py",
    "tools/memory.sh",
    "tools/memory.bat",
    "tools/web_research.py",
    "tools/web_search.sh",
    "tools/web_search.bat",
    "tools/browser_fetch.py",
    "tools/browser_fetch.sh",
    "tools/browser_fetch.bat",
    "plugin/fix-prompt.js",
    "plugin/handoff-compaction.js",
    "templates/severity-guide.txt",
    "agents/prepare-agent.md",
)

SKILL_PATH = "skills/single-session-workflow/SKILL.md"
SKILL_ANCHORS = (
    "name: single-session-workflow",
    "# Single-Session Workflow",
    "**MODE SWITCH — MANDATORY:**",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default="../orchestrator-opencode",
        help="destination repo root (default: ../orchestrator-opencode)",
    )
    args = parser.parse_args()

    source = Path(__file__).resolve().parents[2]
    dest = Path(args.dest).expanduser()
    if not dest.is_absolute():
        dest = (source / dest).resolve()

    if not dest.is_dir():
        print(f"WARN: destination not found: {dest} (not verified)")
        return 2

    failures: list[str] = []

    for rel in SHARED_FILES:
        src = source / ".opencode" / rel
        dst = dest / ".opencode" / rel
        if not src.is_file():
            failures.append(f"SOURCE MISSING {rel}")
        elif not dst.is_file():
            failures.append(f"DEST MISSING   {rel}")
        elif src.read_bytes() != dst.read_bytes():
            failures.append(f"DRIFT          {rel}")
        else:
            print(f"OK             {rel}")

    skill = dest / SKILL_PATH
    if not skill.is_file():
        failures.append(f"DEST MISSING   {SKILL_PATH}")
    else:
        text = skill.read_text(encoding="utf-8")
        for anchor in SKILL_ANCHORS:
            if anchor not in text:
                failures.append(f"ANCHOR MISSING {SKILL_PATH}: {anchor}")

    if failures:
        print("\n".join(failures))
        print(f"FAIL: {len(failures)} issue(s)")
        return 1

    print("PASS: shared payload and generated-skill anchors verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
