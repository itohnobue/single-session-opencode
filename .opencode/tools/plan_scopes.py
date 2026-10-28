#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["wcmatch>=8.0", "pathspec>=0.11", "tomli; python_version < '3.11'"]
# ///
"""
plan_scopes.py - deterministic volume-scope resolver for the orchestrator workflow.

Replaces the mechanical part of the `volume-splitter` agent: resolves a plan's
`toml plan-scopes` block to exact files with exact `wc -l` LOC counts, applies
the volume caps, applies the planner's cut, merges back pointless splits,
computes the stage packing line and the size class, and writes one generated
section into the plan between two marker lines.

Determinism: every list is sorted, every path is repo-relative POSIX, the
section uses LF newlines, and no timestamp or absolute path is ever emitted.
Two consecutive `resolve` runs produce byte-identical files.

Usage:
    plan_scopes.py resolve --plan <path-to-glm-plan.md> [--root DIR] [--json] [--dry-run]
    plan_scopes.py check   --plan <path-to-glm-plan.md> [--root DIR] [--json]
    plan_scopes.py propose --plan <path-to-glm-plan.md> [--root DIR] [--json]

Exit codes:
    resolve   0 ok, 2 input error
    check     0 clean, 1 drift or invariant violation, 2 input error
    propose   0 ok, 2 input error
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from pathspec import PathSpec
from wcmatch import glob

# --------------------------------------------------------------------------
# Constants — the contract's numbers
# --------------------------------------------------------------------------

SPEC_FENCE = "```toml plan-scopes"
FENCE_CLOSE = "```"
BEGIN_MARK = "<!-- plan-scopes:begin -->"
END_MARK = "<!-- plan-scopes:end -->"

CAP_LOC = 5000
CAP_FILES = 15
FILES_UPPER = 20
MERGE_FLOOR = 3000
THIN_LOC = 3000
COHESIVE_FILE_LOC = 300
SIZE_LARGE_LOC = 10000
SIZE_LARGE_FILES = 30
SIZE_MEDIUM_LOC = 5000
SIZE_MEDIUM_FILES = 15

SIZE_RANK = {"none": 0, "tiny": 1, "small": 2, "medium": 3, "large": 4}
CATEGORIES = {
    "module-complete",
    "distinct lens",
    "boundary slice",
    "cap/file blocked",
    "other",
}
# Scope classes: the size axis counts source files and source LOC only.
SCOPE_CLASSES = {"source", "test", "docs", "build", "config"}
DEFAULT_CLASS = "source"
# Rough size proxy for delegation budgeting: ~10 tokens of context per source
# line. An estimate only — never used in a rule, a verdict or an exit code.
TOKENS_PER_SOURCE_LINE = 10
TOKENS_EST_NOTE = "~10 tokens per line (estimate only; not used in any rule)"

GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE

# warning sort categories, in the order the contract states them
W_NEWLINE = 1
W_THIN = 2
W_ESCAPE = 3
W_MERGE = 4
W_OVERLAP = 5
W_UNCATEGORIZED = 6

SPAN_RE = re.compile(r"^(.+):(\d+)-(\d+)$")
SIZE_RE = re.compile(r"size=([a-z]+)")
FILE_BULLET_RE = re.compile(
    "^- (?P<name>.*?) \u2014 (?P<loc>\\d+) LOC(?P<span> \\(span\\))?$"
)


class InputError(Exception):
    """A plan, spec block or filesystem state that the contract rejects (exit 2)."""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class Entry:
    """One resolved read: a whole file, or a span inside one."""

    path: str
    loc: int
    kind: str  # "file" | "span"
    start: Optional[int] = None
    end: Optional[int] = None

    def render(self) -> str:
        if self.kind == "span":
            return f"- {self.path}:{self.start}-{self.end} \u2014 {self.loc} LOC (span)"
        return f"- {self.path} \u2014 {self.loc} LOC"

    def as_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": self.path, "loc": self.loc, "kind": self.kind}
        if self.kind == "span":
            out["span"] = [self.start, self.end]
        return out


@dataclass
class Scope:
    id: str
    stage: str
    agent: str
    role: str
    category: Optional[str] = None
    entries: List[Entry] = field(default_factory=list)
    reads: List[Entry] = field(default_factory=list)
    scope_class: str = DEFAULT_CLASS
    verdict: str = ""
    reason: Optional[str] = None
    split_of: Optional[str] = None

    @property
    def loc(self) -> int:
        """Audited LOC only — the number the caps and the size class use."""
        return sum(e.loc for e in self.entries)

    @property
    def files_n(self) -> int:
        return len({e.path for e in self.entries})

    @property
    def paths(self) -> List[str]:
        return sorted({e.path for e in self.entries})

    @property
    def reads_loc(self) -> int:
        """LOC of every read entry, before audited/read dedupe."""
        return sum(e.loc for e in self.reads)

    @property
    def read_only_loc(self) -> int:
        """Read LOC that adds volume: read paths outside the audited set.

        A read whose path is audited is already counted by `loc`, so it is
        dropped here — audited wins.
        """
        audited = {e.path for e in self.entries}
        return sum(e.loc for e in self.reads if e.path not in audited)

    @property
    def in_scope_loc(self) -> int:
        """What this agent reads, deduped — the packing-in-scope basis."""
        return self.loc + self.read_only_loc

    def as_json(self, warnings: Sequence[str]) -> Dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "agent": self.agent,
            "files": [e.as_json() for e in sorted(self.entries, key=_entry_key)],
            "loc": self.loc,
            "files_n": self.files_n,
            "reads": [e.as_json() for e in sorted(self.reads, key=_entry_key)],
            "reads_loc": self.reads_loc,
            "class": self.scope_class,
            "verdict": self.verdict,
            "warnings": list(warnings),
        }


@dataclass
class Warning:
    cat: int
    key: str
    scope: Optional[str]
    text: str


@dataclass
class Resolution:
    plan_rel: str
    scopes: List[Scope]
    stages: Dict[str, Dict[str, Any]]
    packing: Dict[str, Dict[str, Any]]
    size: Dict[str, Any]
    warnings: List[Warning]
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)


class _Warnings:
    """Collector that keeps rendering order deterministic."""

    def __init__(self) -> None:
        self.items: List[Warning] = []

    def add(self, cat: int, key: str, text: str, scope: Optional[str] = None) -> None:
        self.items.append(Warning(cat, key, scope, text))

    def sorted(self) -> List[Warning]:
        return sorted(self.items, key=lambda w: (w.cat, w.key, w.text))

    def unique(self) -> List[Warning]:
        """Each warning text at most once, first occurrence wins."""
        seen: set = set()
        out: List[Warning] = []
        for warning in self.sorted():
            if warning.text in seen:
                continue
            seen.add(warning.text)
            out.append(warning)
        return out

    def texts(self) -> List[str]:
        return [w.text for w in self.sorted()]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _entry_key(e: Entry) -> Tuple[str, int]:
    return (e.path, e.start or 0)


def normalize_rel(path: str) -> str:
    """Repo-relative POSIX path; accepts Windows separators and a leading ./."""
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return p
    return posixpath.normpath(p)


def parse_span(text: str) -> Tuple[str, int, int]:
    m = SPAN_RE.match(text.strip())
    if not m:
        raise InputError(f"bad span {text!r}: expected path:start-end")
    try:
        start = int(m.group(2))
        end = int(m.group(3))
    except ValueError:  # defensive: the regex only admits digits
        raise InputError(f"bad span {text!r}: start/end must be integers")
    return m.group(1), start, end


def count_loc(path: Path) -> Tuple[int, bytes]:
    """LOC = number of b'\\n' bytes, exactly `wc -l`."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read file: {path.as_posix()} ({exc.strerror})")
    return data.count(b"\n"), data


def splitlines_count(loc: int, data: bytes) -> int:
    if not data or data.endswith(b"\n"):
        return loc
    return loc + 1


def _require_inside(rel: str) -> None:
    if not rel:
        raise InputError("empty path in spec")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise InputError(f"absolute path not allowed in spec: {rel}")
    if rel == ".." or rel.startswith("../"):
        raise InputError(f"path escapes --root: {rel}")


def _on_disk_case_path(root: Path, segments: Sequence[str]) -> str:
    """Best-effort on-disk spelling of a path, walking segment by segment."""
    current = root
    parts: List[str] = []
    for segment in segments:
        try:
            names = [entry.name for entry in os.scandir(current)]
        except OSError:
            parts.append(segment)
            current = current / segment
            continue
        if segment in names:
            chosen = segment
        else:
            lowered = segment.lower()
            near = sorted(name for name in names if name.lower() == lowered)
            chosen = near[0] if near else segment
        parts.append(chosen)
        current = current / chosen
    return "/".join(parts)


def _verify_case(root: Path, rel: str) -> None:
    """Every segment must match the on-disk case exactly.

    macOS resolves `src/Foo.py` to `src/foo.py`; Linux does not. Without this
    check a spec authored on macOS passes there and fails on Linux with a
    confusing "not a regular file".
    """
    segments = [segment for segment in rel.split("/") if segment not in ("", ".")]
    current = root
    for segment in segments:
        try:
            names = [entry.name for entry in os.scandir(current)]
        except OSError:
            return
        if segment in names:
            current = current / segment
            continue
        lowered = segment.lower()
        if not any(name.lower() == lowered for name in names):
            return
        actual = _on_disk_case_path(root, segments)
        raise InputError(
            "path case mismatch: %s (on disk: %s)" % (rel, actual)
        )


def _require_inside_root(root: Path, rel: str) -> None:
    """Refuse a path whose real location (after symlinks) leaves --root."""
    try:
        real_root = root.resolve()
        real = (root / rel).resolve()
    except OSError as exc:
        raise InputError(f"cannot resolve path: {rel} ({exc.strerror})")
    try:
        real.relative_to(real_root)
    except ValueError:
        raise InputError(
            "path escapes --root through a symlink: %s (resolves to %s)"
            % (rel, real.as_posix())
        )


def _as_str_list(value: Any, where: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InputError(f"{where} must be a list of strings")
    return list(value)


def _check_keys(table: Dict[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise InputError(f"unknown key(s) in {where}: {', '.join(unknown)}")


SCOPE_KEYS = {
    "id",
    "stage",
    "agent",
    "role",
    "files",
    "spans",
    "reads",
    "exclude",
    "cohesive",
    "category",
    "class",
    "split",
}
SPLIT_KEYS = {"id", "files", "spans", "reads", "exclude", "agent", "class"}
STAGE_KEYS = {"kind", "intersections", "second_opinions"}


# --------------------------------------------------------------------------
# Plan + spec parsing
# --------------------------------------------------------------------------


def read_text_file(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        raise InputError(f"file not found: {path.as_posix()}")
    except OSError as exc:
        raise InputError(f"cannot read file: {path.as_posix()} ({exc.strerror})")


def extract_spec_block(text: str) -> str:
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.strip() == SPEC_FENCE]
    if len(starts) != 1:
        raise InputError(
            "expected exactly one '```toml plan-scopes' block, found %d" % len(starts)
        )
    body: List[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.strip() == FENCE_CLOSE:
            return "\n".join(body)
        body.append(line)
    raise InputError("unterminated '```toml plan-scopes' block")


def parse_spec(text: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse the fenced `toml plan-scopes` block of a plan file."""
    return parse_spec_toml(extract_spec_block(text))


def parse_spec_toml(block: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse a bare spec (the `[stage.*]` / `[[scope]]` tables, no markdown fence)."""
    try:
        data = tomllib.loads(block)
    except Exception as exc:  # tomllib.TOMLDecodeError, and tomli's
        raise InputError(f"cannot parse the spec: {exc}")
    if not isinstance(data, dict):
        raise InputError("the 'toml plan-scopes' block must be a TOML table")
    _check_keys(data, {"stage", "scope"}, "the plan-scopes block")

    stages: Dict[str, Dict[str, Any]] = {}
    raw_stages = data.get("stage", {})
    if not isinstance(raw_stages, dict):
        raise InputError("[stage.<id>] must be tables")
    for stage_id, cfg in raw_stages.items():
        if not isinstance(cfg, dict):
            raise InputError(f"[stage.{stage_id}] must be a table")
        _check_keys(cfg, STAGE_KEYS, f"[stage.{stage_id}]")
        if "kind" in cfg and not isinstance(cfg["kind"], str):
            raise InputError(f"[stage.{stage_id}].kind must be a string")
        stages[stage_id] = {
            "kind": cfg.get("kind"),
            "intersections": _as_str_list(
                cfg.get("intersections"), f"[stage.{stage_id}].intersections"
            ),
            "second_opinions": _as_str_list(
                cfg.get("second_opinions"), f"[stage.{stage_id}].second_opinions"
            ),
        }

    raw_scopes = data.get("scope", [])
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise InputError("the plan-scopes block carries no [[scope]] entries")

    scopes: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for index, raw in enumerate(raw_scopes):
        where = f"[[scope]] #{index + 1}"
        if not isinstance(raw, dict):
            raise InputError(f"{where} must be a table")
        _check_keys(raw, SCOPE_KEYS, where)
        for required in ("id", "stage", "agent", "role"):
            if required not in raw:
                raise InputError(f"{where} is missing required key '{required}'")
            if not isinstance(raw[required], str) or not raw[required]:
                raise InputError(f"{where}.{required} must be a non-empty string")
        scope_id = raw["id"]
        if scope_id in seen_ids:
            raise InputError(f"duplicate scope id: {scope_id}")
        seen_ids.add(scope_id)
        if raw["role"] not in ("primary", "intersection"):
            raise InputError(
                f"scope {scope_id}: role must be 'primary' or 'intersection', got {raw['role']!r}"
            )
        category = raw.get("category")
        if category is not None:
            if not isinstance(category, str):
                raise InputError(f"scope {scope_id}: category must be a string")
            if category not in CATEGORIES:
                raise InputError(
                    "scope %s: unknown category %r (allowed: %s)"
                    % (scope_id, category, ", ".join(sorted(CATEGORIES)))
                )
        cohesive = raw.get("cohesive", False)
        if not isinstance(cohesive, bool):
            raise InputError(f"scope {scope_id}: cohesive must be a boolean")

        scope_class = raw.get("class", DEFAULT_CLASS)
        if not isinstance(scope_class, str) or scope_class not in SCOPE_CLASSES:
            raise InputError(
                "scope %s: unknown class %r (allowed: %s)"
                % (scope_id, scope_class, ", ".join(sorted(SCOPE_CLASSES)))
            )

        files = _as_str_list(raw.get("files"), f"scope {scope_id}: files")
        spans = _as_str_list(raw.get("spans"), f"scope {scope_id}: spans")
        reads = _as_str_list(raw.get("reads"), f"scope {scope_id}: reads")
        exclude = _as_str_list(raw.get("exclude"), f"scope {scope_id}: exclude")
        if not files and not spans:
            if reads:
                raise InputError(
                    f"scope {scope_id} has no audited files (files and spans are both empty; "
                    "reads do not count as audited)"
                )
            raise InputError(f"scope {scope_id} has no files (files and spans are both empty)")

        raw_split = raw.get("split", [])
        if not isinstance(raw_split, list):
            raise InputError(f"scope {scope_id}: [[scope.split]] must be an array of tables")
        splits: List[Dict[str, Any]] = []
        for sindex, part in enumerate(raw_split):
            pwhere = f"scope {scope_id} [[scope.split]] #{sindex + 1}"
            if not isinstance(part, dict):
                raise InputError(f"{pwhere} must be a table")
            _check_keys(part, SPLIT_KEYS, pwhere)
            if "id" not in part or not isinstance(part["id"], str) or not part["id"]:
                raise InputError(f"{pwhere} is missing required non-empty key 'id'")
            part_files = _as_str_list(part.get("files"), f"{pwhere}: files")
            part_spans = _as_str_list(part.get("spans"), f"{pwhere}: spans")
            part_reads = _as_str_list(part.get("reads"), f"{pwhere}: reads")
            part_class = part.get("class")
            if part_class is not None:
                if not isinstance(part_class, str) or part_class not in SCOPE_CLASSES:
                    raise InputError(
                        "%s: unknown class %r (allowed: %s)"
                        % (pwhere, part_class, ", ".join(sorted(SCOPE_CLASSES)))
                    )
            if not part_files and not part_spans:
                if part_reads:
                    raise InputError(
                        f"{pwhere} has no audited files (files and spans are both empty; "
                        "reads do not count as audited)"
                    )
                raise InputError(f"{pwhere} has no files (files and spans are both empty)")
            splits.append(
                {
                    "id": part["id"],
                    "files": part_files,
                    "spans": part_spans,
                    "reads": part_reads,
                    "exclude": _as_str_list(part.get("exclude"), f"{pwhere}: exclude"),
                    "agent": part.get("agent"),
                    "class": part_class,
                }
            )

        scopes.append(
            {
                "id": scope_id,
                "stage": raw["stage"],
                "agent": raw["agent"],
                "role": raw["role"],
                "files": files,
                "spans": spans,
                "reads": reads,
                "exclude": exclude,
                "cohesive": cohesive,
                "category": category,
                "class": scope_class,
                "split": splits,
            }
        )

    scopes.sort(key=lambda s: (s["stage"], s["id"]))
    return stages, scopes


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def resolve_scope(
    raw: Dict[str, Any],
    root: Path,
    warnings: _Warnings,
    *,
    scope_id: Optional[str] = None,
) -> Scope:
    sid = scope_id or raw["id"]
    entries: List[Entry] = []  # audited: files + spans
    reads: List[Entry] = []  # oracle reads: never audited, never capped
    seen_paths: set = set()
    seen_spans: set = set()
    seen_read_paths: set = set()
    seen_read_spans: set = set()
    data_cache: Dict[str, Tuple[int, bytes]] = {}

    def cached(path: str) -> Tuple[int, bytes]:
        if path not in data_cache:
            data_cache[path] = count_loc(root / path)
        return data_cache[path]

    spec = None
    if raw["exclude"]:
        spec = PathSpec.from_lines(
            "gitignore", [normalize_rel(p) for p in raw["exclude"]]
        )

    def expand(patterns: Sequence[str]) -> List[str]:
        matches: List[str] = []
        for pattern in patterns:
            pat = normalize_rel(pattern)
            try:
                hits = glob.glob(pat, flags=GLOB_FLAGS, root_dir=str(root))
            except Exception as exc:
                raise InputError(f"invalid glob pattern {pattern!r}: {exc}")
            if not hits:
                raise InputError(f"pattern matched nothing: {pattern}")
            for hit in hits:
                matches.append(normalize_rel(hit))
        return matches

    def add_whole_file(rel: str, bucket: List[Entry], seen: set) -> None:
        if rel in seen:
            return
        if spec is not None and spec.match_file(rel):
            return
        _require_inside(rel)
        _verify_case(root, rel)
        full = root / rel
        _require_inside_root(root, rel)
        if full.is_dir():
            if full.is_symlink():
                raise InputError(
                    f"pattern matched a directory symlink, not a file: {rel}"
                )
            raise InputError(f"pattern matched a directory, not a file: {rel}")
        if not full.is_file():
            raise InputError(f"not a regular file: {rel}")
        loc, data = cached(rel)
        if data and not data.endswith(b"\n"):
            warnings.add(
                W_NEWLINE,
                f"{rel}",
                "no trailing newline: wc -l=%d, splitlines=%d (%s)"
                % (loc, loc + 1, rel),
                sid,
            )
        bucket.append(Entry(rel, loc, "file"))
        seen.add(rel)

    def add_span(span_text: str, bucket: List[Entry], seen_spans: set) -> None:
        raw_path, start, end = parse_span(span_text)
        rel = normalize_rel(raw_path)
        if spec is not None and spec.match_file(rel):
            return
        _require_inside(rel)
        _verify_case(root, rel)
        full = root / rel
        _require_inside_root(root, rel)
        if full.is_dir():
            if full.is_symlink():
                raise InputError(
                    f"span path is a directory symlink, not a file: {rel} (in {span_text!r})"
                )
            raise InputError(
                f"span path is a directory, not a file: {rel} (in {span_text!r})"
            )
        if not full.is_file():
            raise InputError(f"span path is not a regular file: {rel} (in {span_text!r})")
        loc, data = cached(rel)
        total = splitlines_count(loc, data)
        if start < 1 or end < start or end > total:
            raise InputError(
                "span out of range: %s (file has %d lines)" % (span_text, total)
            )
        key = (rel, start, end)
        if key in seen_spans:
            return
        seen_spans.add(key)
        bucket.append(Entry(rel, end - start + 1, "span", start, end))

    if raw["files"]:
        for rel in expand(raw["files"]):
            add_whole_file(rel, entries, seen_paths)
    for span_text in raw["spans"]:
        add_span(span_text, entries, seen_spans)

    # A read entry is a span when it carries a range, otherwise a path or glob.
    for token in raw.get("reads", []):
        if SPAN_RE.match(token.strip()):
            add_span(token, reads, seen_read_spans)
        else:
            for rel in expand([token]):
                add_whole_file(rel, reads, seen_read_paths)

    if not entries:
        raise InputError(
            f"scope {sid} resolves to no audited files (reads do not count as audited)"
        )

    return Scope(
        id=sid,
        stage=raw["stage"],
        agent=raw["agent"],
        role=raw["role"],
        category=raw.get("category"),
        entries=entries,
        reads=reads,
        scope_class=raw.get("class") or DEFAULT_CLASS,
    )


def cap_verdict(scope: Scope, cohesive: bool = False) -> Tuple[str, Optional[str]]:
    """Apply the cap rules to one scope. Returns (verdict, must-split reason)."""
    n = scope.files_n
    loc = scope.loc

    # Rule 1 — thin stubs: many files, little content. Takes precedence over the
    # file-count cap (AGENTS.md: ">30 files but <3,000 total LOC is accepted").
    if n > FILES_UPPER and loc < THIN_LOC:
        return "thin stub", None

    # Rule 5 — a single whole file over the LOC cap with nothing to cut.
    if (
        n == 1
        and len(scope.entries) == 1
        and scope.entries[0].kind == "file"
        and scope.entries[0].loc > CAP_LOC
    ):
        return "single-file escape", None

    # Rule 2 — the hard caps.
    if n > FILES_UPPER:
        return "must split", f"files={n} > {FILES_UPPER}"
    if loc > CAP_LOC:
        return "must split", f"LOC={loc} > {CAP_LOC}"

    # Rule 6 — one oversized read inside a multi-entry scope.
    for entry in sorted(scope.entries, key=_entry_key):
        if entry.loc > CAP_LOC:
            return "must split", f"{entry.path} {entry.loc} LOC > {CAP_LOC}"

    # Rule 3 — the 16-20 file band.
    if 16 <= n <= FILES_UPPER:
        biggest = max((e.loc for e in scope.entries), default=0)
        if cohesive and biggest <= COHESIVE_FILE_LOC:
            return "no split", None
        return (
            "must split",
            f"files={n} in 16-20 band (cohesive={'true' if cohesive else 'false'}, "
            f"max file LOC={biggest})",
        )

    # Rule 4 — inside every cap.
    return "no split", None


def build_resolution(
    source: Source,
    root: Path,
    *,
    uncut: str = "raise",
) -> Resolution:
    """Resolve a plan or a bare spec.

    `uncut` selects what an over-cap scope with no cut does: "raise" is the
    resolver (exit 2), "propose" collects a directory proposal instead, and
    "collect" records a violation for `check --spec` (exit 1).
    """
    text = source.text
    if source.is_plan:
        stages, raw_scopes = parse_spec(text)
    else:
        stages, raw_scopes = parse_spec_toml(text)
    warnings = _Warnings()
    violations: List[str] = []

    def rule(message: str) -> None:
        if uncut == "collect":
            violations.append(message)
        else:
            raise InputError(message)

    resolved: List[Scope] = []
    proposals: List[Dict[str, Any]] = []

    for raw in raw_scopes:
        scope = resolve_scope(raw, root, warnings)
        if scope.role != "primary":
            # Caps and cuts are primary-scope rules; an intersection is counted
            # in the packing line but never split or cap-checked.
            scope.verdict = "no split"
            if raw["split"]:
                warnings.add(
                    W_UNCATEGORIZED,
                    f"{scope.id}",
                    "split ignored: %s is an intersection scope (caps are primary-only)"
                    % scope.id,
                    scope.id,
                )
            resolved.append(scope)
            continue

        verdict, reason = cap_verdict(scope, raw["cohesive"])
        scope.verdict = verdict
        scope.reason = reason

        if raw["split"]:
            # The parent is replaced by its parts (or restored by merge-back), so
            # its own verdict warning is emitted only after that decision.
            parts = _apply_split(raw, scope, root, warnings, rule)
            resolved.extend(parts)
            continue

        _emit_verdict_warning(scope, verdict, warnings)

        if verdict == "must split":
            if uncut == "propose":
                proposals.append(_proposal(scope))
                resolved.append(scope)
                continue
            rule(
                "scope %s must split (%s); supply [[scope.split]] blocks (see propose)"
                % (scope.id, reason or "caps exceeded")
            )
            resolved.append(scope)
            continue

        resolved.append(scope)

    resolved.sort(key=lambda s: (s.stage, s.id))

    # One agent id per stage: a plan that names the same agent twice is broken.
    agents_seen: Dict[Tuple[str, str], str] = {}
    for scope in resolved:
        key = (scope.stage, scope.agent)
        if key in agents_seen:
            raise InputError(
                "stage %s: duplicate agent id %r used by scopes %s and %s"
                % (scope.stage, scope.agent, agents_seen[key], scope.id)
            )
        agents_seen[key] = scope.id

    # Overlaps across scopes are legal (architectural overlap) but reported.
    # Scopes that partition one file by disjoint spans are NOT an overlap: the
    # shared path is only reported when the line ranges actually intersect.
    owners: Dict[str, List[Scope]] = {}
    for scope in resolved:
        for path in scope.paths:
            owners.setdefault(path, []).append(scope)
    for path in sorted(owners):
        holders = sorted(owners[path], key=lambda s: s.id)
        if len(holders) < 2:
            continue
        total = _file_lines(root, path)
        ranges = [
            (
                holder.id,
                _merge_intervals(
                    [_entry_interval(e, total) for e in holder.entries if e.path == path]
                ),
            )
            for holder in holders
        ]
        overlapping: List[str] = []
        for index in range(len(ranges)):
            for other in range(index + 1, len(ranges)):
                if _intervals_intersect(ranges[index][1], ranges[other][1]):
                    overlapping.append(ranges[index][0])
                    overlapping.append(ranges[other][0])
        ids = sorted(set(overlapping))
        if len(ids) > 1:
            warnings.add(
                W_OVERLAP,
                path,
                "overlap: %s in %s" % (path, ", ".join(ids)),
                None,
            )

    primaries = [s for s in resolved if s.role == "primary"]
    for scope in primaries:
        if scope.in_scope_loc < MERGE_FLOOR and scope.category is None:
            warnings.add(
                W_UNCATEGORIZED,
                f"{scope.id}",
                "uncategorized: %s (%d LOC) has no category"
                % (scope.id, scope.in_scope_loc),
                scope.id,
            )

    packing = _compute_packing(resolved, stages)
    size = _compute_size(primaries, text if source.is_plan else None)
    plan_rel = _rel_to_root(source.path, root)

    return Resolution(
        plan_rel=plan_rel,
        scopes=resolved,
        stages=stages,
        packing=packing,
        size=size,
        warnings=warnings.unique(),
        proposals=proposals,
        violations=violations,
    )


def _emit_verdict_warning(scope: Scope, verdict: str, warnings: _Warnings) -> None:
    """The one warning a cap verdict carries (thin stub / single-file escape)."""
    if verdict == "thin stub":
        warnings.add(
            W_THIN,
            f"{scope.id}",
            "thin-stub: %d files, %d LOC" % (scope.files_n, scope.loc),
            scope.id,
        )
    elif verdict == "single-file escape":
        warnings.add(
            W_ESCAPE,
            f"{scope.id}",
            "single-file escape: %s %d LOC"
            % (scope.entries[0].path, scope.entries[0].loc),
            scope.id,
        )


def _file_lines(root: Path, path: str) -> int:
    loc, data = count_loc(root / path)
    return splitlines_count(loc, data)


def _entry_interval(entry: Entry, total: int) -> Tuple[int, int]:
    if entry.kind == "span":
        return (entry.start or 1, entry.end or total)
    return (1, total)


def _intervals_intersect(
    left: Sequence[Tuple[int, int]], right: Sequence[Tuple[int, int]]
) -> bool:
    return any(a <= d and c <= b for (a, b) in left for (c, d) in right)


def _merge_intervals(intervals: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _first_gap(
    merged: Sequence[Tuple[int, int]], start: int, end: int
) -> Optional[Tuple[int, int]]:
    """First uncovered line range inside [start, end], or None when fully covered."""
    cursor = start
    for (a, b) in merged:
        if b < cursor:
            continue
        if a > cursor:
            return (cursor, min(a - 1, end))
        cursor = max(cursor, b + 1)
        if cursor > end:
            return None
    return (cursor, end) if cursor <= end else None


def _apply_split(
    raw: Dict[str, Any],
    scope: Scope,
    root: Path,
    warnings: _Warnings,
    rule,
) -> List[Scope]:
    parent_paths = set(scope.paths)
    parts: List[Scope] = []
    seen_part_ids: set = set()

    for part_raw in raw["split"]:
        part_id = part_raw["id"]
        if part_id in seen_part_ids:
            raise InputError(f"scope {scope.id}: duplicate split part id {part_id}")
        seen_part_ids.add(part_id)
        # A part without an explicit agent gets a deterministic, distinct id —
        # inheriting the parent's would name one agent twice.
        part_agent = part_raw.get("agent") or "%s-%s" % (scope.agent, part_id)
        part_source = dict(part_raw)
        part_source["class"] = part_raw.get("class") or scope.scope_class
        part_source.setdefault("stage", scope.stage)
        part_source.setdefault("role", scope.role)
        part_source["agent"] = part_agent
        part = resolve_scope(part_source, root, warnings, scope_id=part_id)
        part.stage = scope.stage
        part.role = scope.role
        part.agent = part_agent
        part.category = scope.category
        part.split_of = scope.id
        if not part.entries:
            raise InputError(f"split part {part_id} of scope {scope.id} resolves to no files")
        verdict, reason = cap_verdict(part, raw["cohesive"])
        if verdict == "must split":
            rule(
                "split part %s of scope %s still must split (%s)"
                % (part_id, scope.id, reason or "caps exceeded")
            )
        part.verdict = verdict
        _emit_verdict_warning(part, verdict, warnings)
        parts.append(part)

    # The parts must partition the parent: every parent entry fully covered,
    # nothing added, and no overlap between parts on the same path. Whole-file
    # entries and spans are both intervals, so an in-file split (parts that are
    # spans of one file) validates on the same rules.
    part_paths: set = {entry.path for part in parts for entry in part.entries}
    problems: List[str] = []
    lost = sorted(parent_paths - part_paths)
    added = sorted(part_paths - parent_paths)
    if lost:
        problems.append("lost file(s): " + ", ".join(lost))
    if added:
        problems.append("file(s) not in the parent: " + ", ".join(added))

    whole_owners: Dict[str, int] = {}
    for part in parts:
        for path in {e.path for e in part.entries if e.kind == "file"}:
            whole_owners[path] = whole_owners.get(path, 0) + 1
    whole_duplicates = sorted(path for path, count in whole_owners.items() if count > 1)
    if whole_duplicates:
        problems.append("file(s) in two parts: " + ", ".join(whole_duplicates))

    totals: Dict[str, int] = {}
    for path in sorted(parent_paths & part_paths):
        if path in whole_duplicates:
            continue
        if path not in totals:
            totals[path] = _file_lines(root, path)
        total = totals[path]
        parent_ivs = _merge_intervals(
            [_entry_interval(e, total) for e in scope.entries if e.path == path]
        )
        per_part = []
        for part in parts:
            ivs = [_entry_interval(e, total) for e in part.entries if e.path == path]
            if ivs:
                per_part.append((part.id, _merge_intervals(ivs)))
        spans = sorted(
            (start, end, part_id)
            for part_id, ivs in per_part
            for (start, end) in ivs
        )
        for index in range(1, len(spans)):
            previous = spans[index - 1]
            current = spans[index]
            if current[0] <= previous[1]:
                problems.append(
                    "overlap in %s: lines %d-%d (%s) and %d-%d (%s)"
                    % (
                        path,
                        previous[0],
                        previous[1],
                        previous[2],
                        current[0],
                        current[1],
                        current[2],
                    )
                )
        for (start, end, part_id) in spans:
            if not any(a <= start and end <= b for (a, b) in parent_ivs):
                problems.append(
                    "outside the parent in %s: lines %d-%d (%s)"
                    % (path, start, end, part_id)
                )
        merged = _merge_intervals([(start, end) for (start, end, _pid) in spans])
        for (a, b) in parent_ivs:
            gap = _first_gap(merged, a, b)
            if gap is not None:
                problems.append(
                    "gap in %s: lines %d-%d not covered by split parts"
                    % (path, gap[0], gap[1])
                )
    if problems:
        rule(
            "split of scope %s does not partition the parent (%s)"
            % (scope.id, "; ".join(problems))
        )
        return parts

    # Merge-back: a split that produces a fragment and fits the parent caps is undone.
    total_files = len({e.path for part in parts for e in part.entries})
    total_loc = sum(part.loc for part in parts)
    if (
        total_files <= CAP_FILES
        and total_loc <= CAP_LOC
        and any(part.loc < MERGE_FLOOR for part in parts)
    ):
        warnings.add(
            W_MERGE,
            f"{scope.id}",
            "merge-back: %s (%d parts -> parent)" % (scope.id, len(parts)),
            scope.id,
        )
        merged = Scope(
            id=scope.id,
            stage=scope.stage,
            agent=scope.agent,
            role=scope.role,
            category=scope.category,
            entries=scope.entries,
            reads=scope.reads,
            scope_class=scope.scope_class,
        )
        merged.verdict, _ = cap_verdict(merged, raw["cohesive"])
        _emit_verdict_warning(merged, merged.verdict, warnings)
        return [merged]

    return parts


# --------------------------------------------------------------------------
# Packing and size
# --------------------------------------------------------------------------


def _packing_body(
    n_primary: int, n_inter: int, n_second: int, loc: int, capacity: int
) -> str:
    agents = n_primary + n_inter + n_second
    util = (loc / capacity * 100.0) if capacity else 0.0
    return (
        "primaries=%d + intersection=%d + second opinions=%d \u2192 read-heavy agents=%d, "
        "in-scope LOC=%d, capacity=%d \u00d7 5,000, utilisation=%.1f%%"
        % (n_primary, n_inter, n_second, agents, loc, agents, util)
    )


def _compute_packing(
    scopes: Sequence[Scope], stages: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    by_stage: Dict[str, List[Scope]] = {}
    for scope in scopes:
        by_stage.setdefault(scope.stage, []).append(scope)
    by_id = {scope.id: scope for scope in scopes}

    out: Dict[str, Dict[str, Any]] = {}
    for stage_id in sorted(by_stage):
        group = by_stage[stage_id]
        primaries = [s for s in group if s.role == "primary"]
        intersections = [s for s in group if s.role == "intersection"]
        config = stages.get(stage_id, {})
        second = list(config.get("second_opinions", []))

        ids = {s.id for s in group}
        for sid in second:
            if sid not in ids:
                raise InputError(
                    "stage %s: second opinion %r is not a primary/intersection scope of "
                    "that stage" % (stage_id, sid)
                )
        for sid in config.get("intersections", []):
            if sid not in ids:
                raise InputError(
                    "stage %s: intersections entry %r is not a scope of that stage"
                    % (stage_id, sid)
                )

        loc = (
            sum(s.in_scope_loc for s in primaries)
            + sum(s.in_scope_loc for s in intersections)
            + sum(by_id[sid].in_scope_loc for sid in second)
        )
        agents = len(primaries) + len(intersections) + len(second)
        capacity = agents * CAP_LOC
        body = _packing_body(len(primaries), len(intersections), len(second), loc, capacity)
        under = [
            {
                "id": s.id,
                "loc": s.in_scope_loc,
                "category": s.category or "uncategorized",
            }
            for s in sorted(primaries, key=lambda s: s.id)
            if s.in_scope_loc < MERGE_FLOOR
        ]
        out[stage_id] = {
            "kind": config.get("kind"),
            "primaries": len(primaries),
            "intersection": len(intersections),
            "second_opinions": len(second),
            "read_heavy_agents": agents,
            "in_scope_loc": loc,
            "capacity": capacity,
            "utilisation": "%.1f%%" % ((loc / capacity * 100.0) if capacity else 0.0),
            "under_target": under,
            "line": body,
        }
    return out


def _compute_size(
    primaries: Sequence[Scope], plan_text: Optional[str]
) -> Dict[str, Any]:
    """Size class from SOURCE scopes only (tests/docs/build/config excluded).

    `plan_text` is the plan body used to read the declared class; None means a
    bare spec with nothing declared, so no override can apply.
    """
    source = [s for s in primaries if s.scope_class == "source"]
    source_loc = sum(s.loc for s in source)
    source_files = sum(s.files_n for s in source)
    loc_total = sum(s.loc for s in primaries)
    files_total = sum(s.files_n for s in primaries)

    suggested: Optional[str] = None
    if source_loc > SIZE_LARGE_LOC or source_files > SIZE_LARGE_FILES:
        suggested = "large"
    elif source_loc > SIZE_MEDIUM_LOC or source_files > SIZE_MEDIUM_FILES:
        suggested = "medium"

    declared = "none"
    override = None
    if plan_text is not None:
        match = SIZE_RE.search(plan_text)
        if match:
            declared = match.group(1).lower()
        if suggested is not None:
            if SIZE_RANK.get(suggested, 0) > SIZE_RANK.get(declared, 0):
                override = "size override: declared %s, resolved %s" % (
                    declared,
                    suggested,
                )
    return {
        "declared": declared,
        "suggested": suggested,
        "override": override,
        "files": files_total,
        "loc": loc_total,
        "source_files": source_files,
        "source_loc": source_loc,
        "other_files": files_total - source_files,
        "other_loc": loc_total - source_loc,
        "tokens_est": source_loc * TOKENS_PER_SOURCE_LINE,
        "tokens_est_note": TOKENS_EST_NOTE,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_section(res: Resolution) -> str:
    lines: List[str] = [BEGIN_MARK, "## Resolved scopes", ""]
    lines.append("| id | stage | agent | files | LOC | reads LOC | verdict |")
    lines.append("|---|---|---|---|---|---|---|")
    for scope in res.scopes:
        lines.append(
            "| %s | %s | %s | %d | %d | %d | %s |"
            % (
                scope.id,
                scope.stage,
                scope.agent,
                scope.files_n,
                scope.loc,
                scope.reads_loc,
                scope.verdict,
            )
        )
    lines.extend(["", "### Files", ""])
    for scope in res.scopes:
        heading = "#### %s" % scope.id
        if scope.split_of:
            heading += " (split of %s)" % scope.split_of
        lines.append(heading)
        for entry in sorted(scope.entries, key=_entry_key):
            lines.append(entry.render())
        if scope.reads:
            audited = {e.path for e in scope.entries}
            lines.append("")
            lines.append("reads (not audited):")
            for entry in sorted(scope.reads, key=_entry_key):
                line = entry.render()
                if entry.path not in audited:
                    line += " (read-only)"
                lines.append(line)
        lines.append("")

    lines.extend(["## Packing", ""])
    for stage_id in sorted(res.packing):
        entry = res.packing[stage_id]
        lines.append("Stage packing %s: %s" % (stage_id, entry["line"]))
        for under in entry["under_target"]:
            lines.append(
                "- %s %d \u2014 not merged: %s"
                % (under["id"], under["loc"], under["category"])
            )
    lines.append("")

    lines.extend(["## Size check", ""])
    lines.append("suggested: %s" % (res.size["suggested"] or "none"))
    if res.size["override"]:
        lines.append(res.size["override"])
    lines.append(
        "source files=%d LOC=%d" % (res.size["source_files"], res.size["source_loc"])
    )
    if res.size["other_files"] or res.size["other_loc"]:
        lines.append(
            "other files=%d LOC=%d" % (res.size["other_files"], res.size["other_loc"])
        )
    lines.append("files=%d LOC=%d" % (res.size["files"], res.size["loc"]))
    lines.append("")

    lines.extend(["## Warnings", ""])
    texts = [w.text for w in res.warnings]
    if texts:
        lines.extend(texts)
    else:
        lines.append("None")
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def replace_section(text: str, section: str) -> str:
    begins = text.count(BEGIN_MARK)
    ends = text.count(END_MARK)
    if begins == 0 and ends == 0:
        if text == "":
            return section
        if text.endswith("\n\n"):
            prefix = text
        elif text.endswith("\n"):
            prefix = text + "\n"
        else:
            prefix = text + "\n\n"
        return prefix + section
    if begins != 1 or ends != 1:
        raise InputError(
            "expected at most one plan-scopes marker pair, found %d begin / %d end"
            % (begins, ends)
        )
    begin_at = text.index(BEGIN_MARK)
    end_at = text.index(END_MARK)
    if end_at < begin_at:
        raise InputError("plan-scopes end marker appears before the begin marker")
    newline_after = text.find("\n", end_at)
    tail_start = len(text) if newline_after == -1 else newline_after + 1
    return text[:begin_at] + section + text[tail_start:]


def extract_section(text: str) -> Optional[str]:
    if BEGIN_MARK not in text or END_MARK not in text:
        return None
    begin_at = text.index(BEGIN_MARK)
    end_at = text.index(END_MARK)
    if end_at < begin_at:
        return None
    newline_after = text.find("\n", end_at)
    tail = len(text) if newline_after == -1 else newline_after + 1
    return text[begin_at:tail]


# --------------------------------------------------------------------------
# propose
# --------------------------------------------------------------------------


def _common_prefix_dirs(paths: Sequence[str]) -> List[str]:
    split = [p.split("/")[:-1] for p in paths]
    if not split:
        return []
    prefix = list(split[0])
    for parts in split[1:]:
        keep = 0
        while keep < len(prefix) and keep < len(parts) and prefix[keep] == parts[keep]:
            keep += 1
        prefix = prefix[:keep]
    return prefix


def _over_caps(entries: Sequence[Entry]) -> bool:
    return len({e.path for e in entries}) > CAP_FILES or sum(e.loc for e in entries) > CAP_LOC


def _group_entries(
    entries: Sequence[Entry], prefix: Sequence[str], depth: int
) -> List[Tuple[str, List[Entry]]]:
    groups: Dict[str, List[Entry]] = {}
    for entry in sorted(entries, key=_entry_key):
        parts = entry.path.split("/")[:-1]
        rest = parts[len(prefix) :] if list(parts[: len(prefix)]) == list(prefix) else parts
        key = rest[depth] if len(rest) > depth else "."
        groups.setdefault(key, []).append(entry)
    return [(key, groups[key]) for key in sorted(groups)]


def _proposal(scope: Scope) -> Dict[str, Any]:
    prefix = _common_prefix_dirs(scope.paths)
    groups: List[Dict[str, Any]] = []

    def walk(entries: Sequence[Entry], depth: int, label_parts: List[str]) -> None:
        for key, members in _group_entries(entries, prefix, depth):
            label = "/".join([p for p in label_parts if p] + ([] if key == "." else [key]))
            files = len({e.path for e in members})
            loc = sum(e.loc for e in members)
            if _over_caps(members):
                nested = _group_entries(members, prefix, depth + 1)
                if len(nested) > 1:
                    walk(members, depth + 1, label_parts + ([] if key == "." else [key]))
                    continue
                groups.append(
                    {
                        "group": label or ".",
                        "files": [e.path for e in sorted(members, key=_entry_key)],
                        "files_n": files,
                        "loc": loc,
                        "note": "cannot subdivide further by directory",
                    }
                )
                continue
            groups.append(
                {
                    "group": label or ".",
                    "files": [e.path for e in sorted(members, key=_entry_key)],
                    "files_n": files,
                    "loc": loc,
                    "note": None,
                }
            )

    walk(scope.entries, 0, [])
    return {
        "id": scope.id,
        "stage": scope.stage,
        "reason": scope.reason,
        "files_n": scope.files_n,
        "loc": scope.loc,
        "groups": groups,
    }


# --------------------------------------------------------------------------
# JSON / reporting
# --------------------------------------------------------------------------


def _rel_to_root(path: Path, root: Path) -> str:
    try:
        return normalize_rel(os.path.relpath(str(path), str(root)))
    except ValueError:  # different drives on Windows
        return normalize_rel(str(path))


def to_json(res: Resolution, ok: bool = True, errors: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "ok": ok,
        "plan": res.plan_rel,
        "scopes": [
            s.as_json([w.text for w in res.warnings if w.scope == s.id])
            for s in res.scopes
        ],
        "packing": res.packing,
        "size": res.size,
        "errors": list(errors),
        "warnings": [w.text for w in res.warnings],
        "proposals": res.proposals,
    }


def _empty_json(plan_rel: str, errors: Sequence[str]) -> Dict[str, Any]:
    return {
        "ok": False,
        "plan": plan_rel,
        "scopes": [],
        "packing": {},
        "size": {"declared": None, "suggested": None},
        "errors": list(errors),
        "warnings": [],
        "proposals": [],
    }


def _resolution_json(res: Resolution) -> Dict[str, Any]:
    return to_json(res)


def default_root(plan_path: Path) -> Path:
    absolute = plan_path if plan_path.is_absolute() else Path.cwd() / plan_path
    parent = absolute.parent
    if parent.name == "tmp":
        return parent.parent
    return parent


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@dataclass
class Source:
    """The resolution input: a plan file (fenced spec + declared size) or a bare spec."""

    kind: str  # "plan" | "spec"
    path: Path
    text: str

    @property
    def is_plan(self) -> bool:
        return self.kind == "plan"


def load_source(args: argparse.Namespace) -> Source:
    plan = getattr(args, "plan", None)
    spec = getattr(args, "spec", None)
    if plan and spec:
        raise InputError("--plan and --spec are mutually exclusive")
    if not plan and not spec:
        raise InputError("one of --plan or --spec is required")
    kind = "plan" if plan else "spec"
    path = Path(plan if plan else spec)
    if not path.exists():
        raise InputError(f"{kind} not found: {path.as_posix()}")
    return Source(kind=kind, path=path, text=read_text_file(path))


def load_root(args: argparse.Namespace, source: Source) -> Path:
    root = Path(args.root) if args.root else default_root(source.path)
    if not root.is_dir():
        raise InputError(f"--root is not a directory: {args.root}")
    return root


def cmd_resolve(args: argparse.Namespace) -> int:
    source = load_source(args)
    root = load_root(args, source)

    res = build_resolution(source, root, uncut="raise")

    if source.is_plan:
        section = render_section(res)
        updated = replace_section(source.text, section)
        if not args.dry_run and updated != source.text:
            with open(
                source.path, "w", encoding="utf-8", errors="surrogateescape", newline=""
            ) as handle:
                handle.write(updated)
        written = "not written (--dry-run)" if args.dry_run else "written"
    else:
        # A bare spec has no plan to annotate: --spec never writes anywhere.
        written = "not written (--spec mode)"

    if args.json:
        print(json.dumps(_resolution_json(res), indent=2, sort_keys=False))
        return 0

    primaries = sum(1 for s in res.scopes if s.role == "primary")
    intersections = sum(1 for s in res.scopes if s.role == "intersection")
    print("plan: %s" % res.plan_rel)
    print("scopes: %d (primary=%d, intersection=%d)" % (len(res.scopes), primaries, intersections))
    for stage_id in sorted(res.packing):
        print("stage %s: %s" % (stage_id, res.packing[stage_id]["line"]))
    print(
        "size: declared=%s suggested=%s"
        % (res.size["declared"], res.size["suggested"] or "none")
    )
    if res.size["override"]:
        print(res.size["override"])
    print("warnings: %d" % len(res.warnings))
    print("section: %s" % written)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    source = load_source(args)
    root = load_root(args, source)

    if source.kind == "spec":
        return _check_spec(source, root, args)

    text = source.text
    stored = extract_section(text)

    # Invariants 1 and 2 are filesystem truth about what the section claims, so
    # they run before the spec is re-resolved (a deleted file would otherwise
    # surface as a resolution error instead of a violation). Read entries are
    # verified the same way: they exist, their count matches, their range is valid.
    blocks: Dict[str, Dict[str, List[_ParsedBullet]]] = {}
    if stored is not None:
        blocks = _parse_blocks(stored)
        entry_problems = _verify_blocks(blocks, root)
        if entry_problems:
            return _report_check(entry_problems, None, args)

    try:
        res = build_resolution(source, root, uncut="raise")
    except InputError as exc:
        # A plan that was already resolved and now violates the caps (or no
        # longer resolves) is drift, not a fresh input error: check exits 1.
        if stored is None:
            raise
        return _report_check([str(exc)], None, args)

    problems: List[str] = []

    if stored is None:
        problems.append("generated section missing (no plan-scopes markers)")
    else:
        problems.extend(_check_read_figures(stored, blocks, res))
        problems.extend(_check_packing_lines(stored, res))
        problems.extend(_check_size_lines(stored, res))
        fresh = render_section(res)
        if stored != fresh:
            for line in _first_difference(stored, fresh):
                problems.append(line)

    if not problems:
        updated = replace_section(text, render_section(res))
        if updated != text:
            problems.append("idempotence: resolve would still change the plan")

    return _report_check(problems, res, args)


def _check_spec(source: Source, root: Path, args: argparse.Namespace) -> int:
    """Validate a bare spec: no stored rendering exists to diff against.

    A well-formed but badly-scoped spec (over the caps with no cut, or a cut that
    does not partition its parent) is a violation (exit 1); a spec that cannot be
    read, parsed or resolved against the filesystem is an input error (exit 2).
    """
    res = build_resolution(source, root, uncut="collect")
    problems = list(res.violations)
    problems.extend(_check_spec_invariants(res, root))
    return _report_check(
        problems, res, args, clean="check: spec valid (%d scopes)" % len(res.scopes)
    )


def _check_spec_invariants(res: Resolution, root: Path) -> List[str]:
    """Independent re-verification of what the resolution produced."""
    problems: List[str] = []
    counts: Dict[str, Tuple[int, bytes]] = {}
    for scope in res.scopes:
        for entry in list(scope.entries) + list(scope.reads):
            full = root / entry.path
            if not full.is_file():
                problems.append("missing or not a regular file: %s" % entry.path)
                continue
            if entry.path not in counts:
                counts[entry.path] = count_loc(full)
            loc, data = counts[entry.path]
            if entry.kind == "span":
                total = splitlines_count(loc, data)
                if entry.start < 1 or entry.end < entry.start or entry.end > total:
                    problems.append(
                        "span out of range: %s:%d-%d (file has %d lines)"
                        % (entry.path, entry.start, entry.end, total)
                    )
                elif entry.loc != entry.end - entry.start + 1:
                    problems.append(
                        "span LOC drift: %s:%d-%d recorded %d, computed %d"
                        % (
                            entry.path,
                            entry.start,
                            entry.end,
                            entry.loc,
                            entry.end - entry.start + 1,
                        )
                    )
            elif entry.loc != loc:
                problems.append(
                    "LOC drift: %s recorded %d, wc -l=%d" % (entry.path, entry.loc, loc)
                )

    # Packing arithmetic, recomputed from the scope objects rather than reused.
    for stage_id, packing in res.packing.items():
        in_stage = [s for s in res.scopes if s.stage == stage_id]
        second = list(res.stages.get(stage_id, {}).get("second_opinions", []))
        by_id = {s.id: s for s in res.scopes}
        expected_loc = sum(s.in_scope_loc for s in in_stage) + sum(
            by_id[sid].in_scope_loc for sid in second
        )
        expected_agents = len(in_stage) + len(second)
        if packing["in_scope_loc"] != expected_loc:
            problems.append(
                "packing LOC drift: stage %s recorded %d, recomputed %d"
                % (stage_id, packing["in_scope_loc"], expected_loc)
            )
        if packing["read_heavy_agents"] != expected_agents:
            problems.append(
                "packing agent count drift: stage %s recorded %d, recomputed %d"
                % (stage_id, packing["read_heavy_agents"], expected_agents)
            )
        if packing["capacity"] != expected_agents * CAP_LOC:
            problems.append(
                "packing capacity drift: stage %s recorded %d, recomputed %d"
                % (stage_id, packing["capacity"], expected_agents * CAP_LOC)
            )

    # Size class, recomputed from the source-classed primaries.
    source_scopes = [
        s for s in res.scopes if s.role == "primary" and s.scope_class == "source"
    ]
    source_loc = sum(s.loc for s in source_scopes)
    source_files = sum(s.files_n for s in source_scopes)
    if res.size["source_loc"] != source_loc or res.size["source_files"] != source_files:
        problems.append(
            "size source totals drift: recorded %d/%d, recomputed %d/%d"
            % (
                res.size["source_files"],
                res.size["source_loc"],
                source_files,
                source_loc,
            )
        )
    if source_loc > SIZE_LARGE_LOC or source_files > SIZE_LARGE_FILES:
        expected_class: Optional[str] = "large"
    elif source_loc > SIZE_MEDIUM_LOC or source_files > SIZE_MEDIUM_FILES:
        expected_class = "medium"
    else:
        expected_class = None
    if res.size["suggested"] != expected_class:
        problems.append(
            "size class drift: recorded %s, recomputed %s"
            % (res.size["suggested"], expected_class)
        )
    if res.size["tokens_est"] != source_loc * TOKENS_PER_SOURCE_LINE:
        problems.append(
            "token estimate drift: recorded %d, recomputed %d"
            % (res.size["tokens_est"], source_loc * TOKENS_PER_SOURCE_LINE)
        )
    return problems


def _report_check(
    problems: Sequence[str],
    res: Optional[Resolution],
    args,
    clean: Optional[str] = None,
) -> int:
    if args.json:
        doc = _resolution_json(res) if res is not None else _empty_json(
            _input_rel(args), list(problems)
        )
        doc["ok"] = not problems
        doc["errors"] = list(problems)
        print(json.dumps(doc, indent=2, sort_keys=False))
        return 0 if not problems else 1

    for line in problems:
        print(line)
    if problems:
        return 1
    assert res is not None
    print(clean if clean is not None else "check: clean (%d scopes)" % len(res.scopes))
    return 0


@dataclass
class _ParsedBullet:
    path: str
    loc: int
    start: Optional[int] = None
    end: Optional[int] = None
    read: bool = False
    read_only: bool = False


def _parse_bullet(line: str) -> Optional[_ParsedBullet]:
    read_only = line.endswith(" (read-only)")
    if read_only:
        line = line[: -len(" (read-only)")]
    match = FILE_BULLET_RE.match(line)
    if not match:
        return None
    name = match.group("name")
    loc = int(match.group("loc"))
    if match.group("span"):
        span = SPAN_RE.match(name)
        if not span:
            return None
        return _ParsedBullet(
            path=normalize_rel(span.group(1)),
            loc=loc,
            start=int(span.group(2)),
            end=int(span.group(3)),
            read_only=read_only,
        )
    return _ParsedBullet(path=normalize_rel(name), loc=loc, read_only=read_only)


def _parse_blocks(section: str) -> Dict[str, Dict[str, List[_ParsedBullet]]]:
    """Split the `### Files` section into per-scope audited and read bullet lists."""
    blocks: Dict[str, Dict[str, List[_ParsedBullet]]] = {}
    scope_id: Optional[str] = None
    mode = "audited"
    for raw in section.split("\n"):
        line = raw.strip()
        if line.startswith("#### "):
            scope_id = line[5:].split(" (split of ")[0].strip()
            blocks[scope_id] = {"audited": [], "reads": []}
            mode = "audited"
            continue
        if line == "reads (not audited):":
            mode = "reads"
            continue
        if not line or not line.startswith("- ") or scope_id is None:
            continue
        bullet = _parse_bullet(line)
        if bullet is None:
            continue
        bullet.read = mode == "reads"
        blocks[scope_id][mode].append(bullet)
    return blocks


def _verify_blocks(
    blocks: Dict[str, Dict[str, List[_ParsedBullet]]], root: Path
) -> List[str]:
    """Invariants 1-2 for audited and read entries alike, plus read markers."""
    problems: List[str] = []
    for scope_id in sorted(blocks):
        block = blocks[scope_id]
        audited_paths = {b.path for b in block["audited"]}
        for kind in ("audited", "reads"):
            for bullet in block[kind]:
                full = root / bullet.path
                if not full.is_file():
                    problems.append(
                        "missing or not a regular file: %s%s"
                        % (bullet.path, " (read)" if kind == "reads" else "")
                    )
                    continue
                loc, data = count_loc(full)
                if bullet.start is not None:
                    total = splitlines_count(loc, data)
                    if (
                        bullet.start < 1
                        or bullet.end is None
                        or bullet.end < bullet.start
                        or bullet.end > total
                    ):
                        problems.append(
                            "span out of range: %s:%d-%d (file has %d lines)"
                            % (bullet.path, bullet.start, bullet.end or 0, total)
                        )
                        continue
                    expected = bullet.end - bullet.start + 1
                    if bullet.loc != expected:
                        problems.append(
                            "span LOC drift: %s:%d-%d recorded %d, computed %d"
                            % (bullet.path, bullet.start, bullet.end, bullet.loc, expected)
                        )
                elif bullet.loc != loc:
                    problems.append(
                        "LOC drift: %s recorded %d, wc -l=%d"
                        % (bullet.path, bullet.loc, loc)
                    )
        for bullet in block["reads"]:
            should_mark = bullet.path not in audited_paths
            if bullet.read_only != should_mark:
                problems.append(
                    "read marker drift: %s (%s, %s)"
                    % (
                        bullet.path,
                        "marked read-only" if bullet.read_only else "not marked read-only",
                        "path is audited" if not should_mark else "path is not audited",
                    )
                )
    return problems


def _check_read_figures(
    section: str,
    blocks: Dict[str, Dict[str, List[_ParsedBullet]]],
    res: Resolution,
) -> List[str]:
    problems: List[str] = []
    for scope in res.scopes:
        block = blocks.get(scope.id, {"audited": [], "reads": []})
        recorded_reads = sum(b.loc for b in block["reads"])
        if recorded_reads != scope.reads_loc:
            problems.append(
                "reads LOC drift: %s recorded %d, computed %d"
                % (scope.id, recorded_reads, scope.reads_loc)
            )
        recorded_read_only = sum(b.loc for b in block["reads"] if b.read_only)
        if recorded_read_only != scope.read_only_loc:
            problems.append(
                "read-only LOC drift: %s recorded %d, computed %d"
                % (scope.id, recorded_read_only, scope.read_only_loc)
            )

    # The scope table's `reads LOC` column must equal a fresh computation.
    rows: Dict[str, List[str]] = {}
    for raw in section.split("\n"):
        line = raw.strip()
        if not line.startswith("| ") or line.startswith("| id "):
            continue
        if set(line) <= {"|", "-"}:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 7:
            rows[cells[0]] = cells
    for scope in res.scopes:
        cells = rows.get(scope.id)
        if cells is None:
            continue  # the full-section comparison reports a missing row
        try:
            recorded = int(cells[5])
        except ValueError:
            problems.append("reads LOC column not an integer: %s" % scope.id)
            continue
        if recorded != scope.reads_loc:
            problems.append(
                "reads LOC column drift: %s recorded %d, computed %d"
                % (scope.id, recorded, scope.reads_loc)
            )

    # A read that duplicates an audited path must not be counted twice in the
    # packing line: recompute the stage total from the stored bullets.
    for stage_id, packing in res.packing.items():
        prefix = "Stage packing %s: " % stage_id
        stored_line = None
        for raw in section.split("\n"):
            if raw.startswith(prefix):
                stored_line = raw
                break
        if stored_line is None:
            continue
        match = re.search(r"in-scope LOC=(\d+)", stored_line)
        if not match:
            problems.append("in-scope LOC not found in the stage %s line" % stage_id)
            continue
        agent_ids = [s.id for s in res.scopes if s.stage == stage_id]
        agent_ids += list(res.stages.get(stage_id, {}).get("second_opinions", []))
        total = 0
        for agent_id in agent_ids:
            block = blocks.get(agent_id)
            if block is None:
                continue
            total += sum(b.loc for b in block["audited"])
            total += sum(b.loc for b in block["reads"] if b.read_only)
        if int(match.group(1)) != total:
            problems.append(
                "in-scope LOC drift: stage %s recorded %d, section sums to %d "
                "(audited + read-only)"
                % (stage_id, int(match.group(1)), total)
            )
    return problems


def _check_packing_lines(section: str, res: Resolution) -> List[str]:
    problems: List[str] = []
    stored = section.split("\n")
    for stage_id in sorted(res.packing):
        prefix = "Stage packing %s: " % stage_id
        expected = res.packing[stage_id]["line"]
        found = [line for line in stored if line.startswith(prefix)]
        if not found:
            problems.append("packing line missing for stage %s" % stage_id)
            continue
        actual = found[0][len(prefix) :]
        if actual != expected:
            problems.append(
                "packing line drift for stage %s (stored %r, computed %r)"
                % (stage_id, actual, expected)
            )
    return problems


def _check_size_lines(section: str, res: Resolution) -> List[str]:
    problems: List[str] = []
    stored = [line.strip() for line in section.split("\n")]
    expected_suggested = "suggested: %s" % (res.size["suggested"] or "none")
    if expected_suggested not in stored:
        problems.append("size suggestion line missing or changed (expected %r)" % expected_suggested)

    # The suggestion must follow from the source class alone.
    source_line = "source files=%d LOC=%d" % (
        res.size["source_files"],
        res.size["source_loc"],
    )
    if source_line not in stored:
        problems.append("size source totals missing or changed (expected %r)" % source_line)
    has_other = bool(res.size["other_files"] or res.size["other_loc"])
    other_line = "other files=%d LOC=%d" % (
        res.size["other_files"],
        res.size["other_loc"],
    )
    if has_other:
        if other_line not in stored:
            problems.append(
                "size other totals missing or changed (expected %r)" % other_line
            )
    elif any(line.startswith("other files=") for line in stored):
        problems.append("size other totals present but every scope is source")

    totals = "files=%d LOC=%d" % (res.size["files"], res.size["loc"])
    if totals not in stored:
        problems.append("size totals missing or changed (expected %r)" % totals)
    if res.size["override"]:
        if res.size["override"] not in stored:
            problems.append("size override line missing (expected %r)" % res.size["override"])
    else:
        if any(line.startswith("size override:") for line in stored):
            problems.append("size override line present but no override is required")
    return problems


def _first_difference(stored: str, fresh: str) -> List[str]:
    a = stored.split("\n")
    b = fresh.split("\n")
    for index in range(max(len(a), len(b))):
        left = a[index] if index < len(a) else "<missing>"
        right = b[index] if index < len(b) else "<missing>"
        if left != right:
            return ["generated section drift at line %d: stored %r, fresh %r" % (index + 1, left, right)]
    return ["generated section drift (bytes differ)"]


def cmd_propose(args: argparse.Namespace) -> int:
    source = load_source(args)
    root = load_root(args, source)

    res = build_resolution(source, root, uncut="propose")

    if args.json:
        print(json.dumps(_resolution_json(res), indent=2, sort_keys=False))
        return 0

    if not res.proposals:
        print("propose: no scope requires a split cut")
        return 0
    for proposal in res.proposals:
        print(
            "scope %s must split (%s); no [[scope.split]] supplied"
            % (proposal["id"], proposal["reason"] or "caps exceeded")
        )
        for index, group in enumerate(proposal["groups"]):
            suffix = chr(ord("a") + index) if index < 26 else str(index + 1)
            note = "" if not group["note"] else " (%s)" % group["note"]
            print(
                "- %s-%s: %s — %d files, %d LOC%s"
                % (
                    proposal["id"],
                    suffix,
                    group["group"],
                    group["files_n"],
                    group["loc"],
                    note,
                )
            )
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


EPILOG = (
    "Input is either --plan (a plan markdown file carrying one fenced "
    "'```toml plan-scopes' block; resolve rewrites its generated section) or "
    "--spec (a bare TOML file with the [stage.*] / [[scope]] tables; nothing is "
    "ever written). The two are mutually exclusive.\n\n"
    "The JSON size object carries tokens_est = source LOC x 10, an estimate of "
    "~10 tokens per line for context budgeting; it is never used in a rule, a "
    "verdict or an exit code."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plan_scopes.py",
        description="Deterministic volume-scope resolver for the orchestrator workflow.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("resolve", "resolve the spec and either write the plan's section or print the result"),
        ("check", "verify a plan's generated section, or validate a bare spec"),
        ("propose", "suggest a directory cut for scopes that must split"),
    ):
        cmd = sub.add_parser(name, help=help_text, epilog=EPILOG,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
        cmd.add_argument("--plan", default=None, help="path to the plan markdown file")
        cmd.add_argument(
            "--spec",
            default=None,
            help="path to a bare TOML spec (no fence; nothing is written)",
        )
        cmd.add_argument("--root", default=None, help="repo root all spec paths are relative to")
        cmd.add_argument("--json", action="store_true", help="print a JSON result object")
        if name == "resolve":
            cmd.add_argument(
                "--dry-run", action="store_true", help="compute but do not write the plan"
            )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            return cmd_resolve(args)
        if args.command == "check":
            return cmd_check(args)
        return cmd_propose(args)
    except InputError as exc:
        message = str(exc)
        if getattr(args, "json", False):
            print(json.dumps(_empty_json(_input_rel(args), [message]), indent=2))
        else:
            print("error: %s" % message)
        print("error: %s" % message, file=sys.stderr)
        return 2


def _input_rel(args: argparse.Namespace) -> str:
    raw = getattr(args, "plan", None) or getattr(args, "spec", None)
    if not raw:
        return ""
    path = Path(raw)
    root = Path(args.root) if getattr(args, "root", None) else default_root(path)
    return _rel_to_root(path, root)


if __name__ == "__main__":
    sys.exit(main())
