# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest", "rank-bm25"]
# ///
"""Acceptance tests for `.opencode/tools/memory.py` provenance/dedup/staleness.

Every fixture repo is created at test time under `tmp_path`; the tool is copied
into a `.opencode/tools/` layout because it resolves `knowledge.md` from
`SCRIPT_DIR.parent.parent`. Assertions check exit codes and file content, not
just printed text.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "memory.py"


def make_repo(tmp_path: Path) -> Path:
    tools = tmp_path / ".opencode" / "tools"
    tools.mkdir(parents=True)
    shutil.copy(TOOL, tools / "memory.py")
    return tmp_path


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / ".opencode" / "tools" / "memory.py"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def write_archive(repo: Path, blocks: list[str]) -> None:
    (repo / "knowledge.md").write_text("# Knowledge Base\n\n" + "\n".join(blocks), encoding="utf-8")


def read_archive(repo: Path) -> str:
    return (repo / "knowledge.md").read_text(encoding="utf-8")


SAMPLE = (
    "Prefix keys in the cache must be byte-stable across turns; "
    "mutating an early message rewrites the cache from that point onward."
)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_add_proposed_renders_status_and_search_badge(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    added = run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    assert added.returncode == 0, added.stderr
    assert "Status: proposed" in read_archive(repo)

    found = run(repo, "search", "cache prefix")
    assert found.returncode == 0, found.stderr
    assert "[proposed]" in found.stdout


def test_observed_requires_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    rejected = run(repo, "add", "gotcha", SAMPLE, "--status", "observed")
    assert rejected.returncode == 1
    assert "requires" in rejected.stderr
    assert not (repo / "knowledge.md").exists() or "Status: observed" not in read_archive(repo)


def test_observed_with_evidence_persists_both(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    added = run(
        repo, "add", "gotcha", SAMPLE,
        "--status", "observed", "--evidence", "kernel/agent/compaction_test.go:41 -> pass",
    )
    assert added.returncode == 0, added.stderr
    text = read_archive(repo)
    assert "Status: observed" in text
    assert "Evidence: kernel/agent/compaction_test.go:41 -> pass" in text


def test_update_can_set_provenance_without_new_content(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    entry_id = [line for line in read_archive(repo).splitlines() if line.startswith("## [")][0][4:-1]

    updated = run(
        repo, "update", entry_id,
        "--status", "observed", "--evidence", "tests/test_memory.py:1 -> ran",
    )
    assert updated.returncode == 0, updated.stderr
    text = read_archive(repo)
    assert "Status: observed" in text
    assert "Evidence: tests/test_memory.py:1 -> ran" in text
    assert SAMPLE in text  # content untouched


def test_update_rejects_observed_without_any_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    entry_id = [line for line in read_archive(repo).splitlines() if line.startswith("## [")][0][4:-1]

    rejected = run(repo, "update", entry_id, "--status", "observed")
    assert rejected.returncode == 1
    assert "Status: proposed" in read_archive(repo)  # unchanged


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------

def test_exact_duplicate_reconfirms_instead_of_adding(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    second = run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    third = run(repo, "add", "gotcha", SAMPLE, "--status", "proposed")
    assert first.returncode == 0 and second.returncode == 0 and third.returncode == 0
    assert "Reconfirmed:" in second.stdout and "no new entry" in second.stdout
    text = read_archive(repo)
    assert text.count("## [") == 1
    assert "Reconfirmed:" in text and "(2)" in text  # two reconfirmations after creation


def test_force_adds_a_second_entry(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(repo, "add", "gotcha", SAMPLE)
    forced = run(repo, "add", "gotcha", SAMPLE, "--force")
    assert forced.returncode == 0, forced.stderr
    text = read_archive(repo)
    assert text.count("## [") == 2
    ids = [line[4:-1] for line in text.splitlines() if line.startswith("## [")]
    assert len(set(ids)) == 2  # a forced second add never reuses the id


def test_similar_entry_is_added_with_advisory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    words = [f"word{i:02d}" for i in range(30)]
    base = " ".join(words)
    variant = " ".join(words[:-1] + ["different"])
    run(repo, "add", "gotcha", base)
    second = run(repo, "add", "gotcha", variant)
    assert second.returncode == 0, second.stderr
    assert read_archive(repo).count("## [") == 2  # near-identical facts are never merged
    assert "similar:" in second.stdout


def test_related_but_distinct_entry_is_added_with_advisory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    words = [f"word{i:02d}" for i in range(20)]
    base = " ".join(words)
    variant = " ".join(words[:-3] + ["other1", "other2", "other3"])
    run(repo, "add", "gotcha", base)
    second = run(repo, "add", "gotcha", variant)
    assert second.returncode == 0, second.stderr
    assert read_archive(repo).count("## [") == 2
    assert "similar:" in second.stdout


def test_archive_routes_through_dedup(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(repo, "add", "gotcha", SAMPLE)
    added = run(repo, "session", "add", "todo", SAMPLE)
    assert added.returncode == 0, added.stderr
    entry_id = [line for line in (repo / "session.md").read_text().splitlines() if line.startswith("## [")][0][4:-1]

    archived = run(repo, "session", "archive", entry_id)
    assert archived.returncode == 0, archived.stderr
    assert "Reconfirmed:" in archived.stdout
    assert read_archive(repo).count("## [") == 1


def test_invalid_status_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    rejected = run(repo, "add", "gotcha", SAMPLE, "--status", "confirmed")
    assert rejected.returncode == 1
    assert "Invalid status" in rejected.stderr


def test_update_observed_accepts_existing_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(repo, "add", "gotcha", SAMPLE, "--status", "proposed", "--evidence", "cmd: rg -n prefix -> 2 hits")
    entry_id = [line for line in read_archive(repo).splitlines() if line.startswith("## [")][0][4:-1]

    updated = run(repo, "update", entry_id, "--status", "observed")
    assert updated.returncode == 0, updated.stderr
    assert "Status: observed" in read_archive(repo)


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_search_has_no_staleness_noise(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    old = (datetime.now() - timedelta(days=400)).isoformat()
    write_archive(repo, [
        "## [got-20250101000000-aaaaaa]\n"
        "Category: gotcha\n"
        f"Changed: {old}\n"
        "Reviewed: " + old + "\n"
        "\nCache prefix invalidation rewrites from the mutated message.\n",
    ])
    found = run(repo, "search", "cache prefix")
    assert found.returncode == 0, found.stderr
    assert "last touched" not in found.stdout  # the caution is not on every result
    assert "Cache prefix invalidation" in found.stdout


def test_check_reports_stale_without_failing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    old = (datetime.now() - timedelta(days=400)).isoformat()
    write_archive(repo, [
        "## [got-20250101000000-bbbbbb]\n"
        "Category: gotcha\n"
        f"Changed: {old}\n"
        "\nStale but structurally valid.\n",
    ])
    checked = run(repo, "check")
    assert checked.returncode == 0, checked.stderr
    assert "Stale entries: 1" in checked.stdout


def test_check_still_fails_on_structural_defect(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write_archive(repo, [
        "## [got-20250101000000-cccccc]\n"
        "Category: not-a-category\n"
        "Changed: 2026-01-01T00:00:00\n"
        "\nBody.\n",
    ])
    checked = run(repo, "check")
    assert checked.returncode == 1


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_legacy_entry_without_new_fields_round_trips(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write_archive(repo, [
        "## [con-20260104060631-f0782b]\n"
        "Category: config\n"
        "Tags: ports, services\n"
        "Changed: 2026-01-04T06:06:31\n"
        "\nService ports: 11001, 11002.\n",
    ])

    identical = run(repo, "update", "con-20260104060631-f0782b", "--tags", "ports,services")
    assert identical.returncode == 0, identical.stderr

    after = read_archive(repo)
    assert "Service ports: 11001, 11002." in after
    assert "Tags: ports, services" in after
    assert "Status:" not in after.split("Service ports")[0].split("Tags:")[1]
    assert "Reviewed:" in after  # every update stamps the review date


def test_round_trip_of_every_new_field(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    run(
        repo, "add", "gotcha", SAMPLE,
        "--status", "observed", "--evidence", "cmd: pytest -q -> 12 passed",
    )
    added_twice = run(repo, "add", "gotcha", SAMPLE, "--status", "observed",
                      "--evidence", "cmd: pytest -q -> 12 passed")
    assert added_twice.returncode == 0, added_twice.stderr

    text = read_archive(repo)
    assert "Status: observed" in text
    assert "Evidence: cmd: pytest -q -> 12 passed" in text
    assert "Reconfirmed:" in text
    assert "Reviewed:" in text

    # A second command in the same run must parse the file identically.
    stats = run(repo, "stats")
    assert stats.returncode == 0, stats.stderr
    assert "observed: 1" in stats.stdout
