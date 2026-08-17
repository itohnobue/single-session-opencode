#!/usr/bin/env bash
# inject-research.sh — Inject a briefing DIGEST into a task file (the digest + full-report
# scheme, same as assemble-task.sh --research-file). The digest may be produced by the
# prepare agent OR curated by the main model from its own research.
# Produces the final task file with structure:
#   ## RESEARCH DATA (your briefing — compact digest)
#   <digest content>
#   <original task content>
# The full report file (no size cap) stays on disk at the path the digest's
# `FULL RESEARCH REPORT:` line states; the executor consults it on demand.
# This final file is then run through assemble-task.sh, giving the executor
# prompt structure: template → research data → task.
# Usage: .opencode/tools/inject-research.sh <research-file> <task-file> <output-file>

set -uo pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 <research-file> <task-file> <output-file>" >&2
  exit 1
fi

RESEARCH="$1"
TASK="$2"
OUT="$3"

if [ ! -s "$RESEARCH" ]; then
  echo "ERROR: research digest file missing or empty: $RESEARCH" >&2
  exit 1
fi
if [ ! -f "$TASK" ]; then
  echo "ERROR: task file missing: $TASK" >&2
  exit 1
fi

{
  echo "## RESEARCH DATA (your briefing — compact digest)"
  echo ""
  cat "$RESEARCH"
  echo ""
  echo "---"
  echo ""
  cat "$TASK"
} > "$OUT"

bytes=$(wc -c < "$OUT" | tr -d ' ')
echo "INJECTED|$(basename "$OUT")|${bytes} bytes"
