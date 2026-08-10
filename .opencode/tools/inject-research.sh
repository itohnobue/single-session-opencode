#!/usr/bin/env bash
# inject-research.sh — Inject prepared research data into a task file.
# Produces the final task file with structure:
#   ## RESEARCH DATA (prepared by prepare agent — your briefing)
#   <research data content>
#   <original task content>
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
  echo "ERROR: research file missing or empty: $RESEARCH" >&2
  exit 1
fi
if [ ! -f "$TASK" ]; then
  echo "ERROR: task file missing: $TASK" >&2
  exit 1
fi

{
  echo "## RESEARCH DATA (prepared by prepare agent — your briefing)"
  echo ""
  cat "$RESEARCH"
  echo ""
  echo "---"
  echo ""
  cat "$TASK"
} > "$OUT"

bytes=$(wc -c < "$OUT" | tr -d ' ')
echo "INJECTED|$(basename "$OUT")|${bytes} bytes"
