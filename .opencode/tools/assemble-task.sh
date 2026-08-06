#!/usr/bin/env bash
# assemble-task.sh — Compose a task prompt for native opencode subagent delegation
#
# Replacement for assemble-prompt.sh. Builds ONLY the task prompt (templates +
# task assignment) — the agent .md is loaded natively by opencode as the subagent's
# system prompt, so it is NOT embedded here.
#
# The assembled task prompt is passed to the opencode `task` tool as the `prompt`
# parameter (subagent_type = AGENT). Agents run as native opencode subagents with
# full permissions inherited from the project config.
#
# Usage:
#   .opencode/tools/assemble-task.sh -a AGENT -t TYPE -n NAME --task TASK_FILE [-o OUT]
#
# Arguments:
#   -a, --agent       Agent name — validates .opencode/agents/{agent}.md exists
#   -t, --task-type   Task type: review | code | research
#   -n, --name        Agent instance name (e.g. s1-reviewer, s2i1-impl-auth)
#   --task            Path to task assignment file (PROJECT, ENVIRONMENT,
#                     PRIOR CONTEXT, YOUR TASK, WRITABLE FILES — lead-written)
#   -o, --output      Override output path (default: tmp/{name}-task-prompt.txt)
#
# Task type → template selection (same as assemble-prompt.sh):
#   review:   coordination-review + severity-guide + quality-rules-review
#   code:     coordination-code   +                  quality-rules-code
#   research: coordination-review +                  quality-rules-review
#
# Output (stdout):
#   ASSEMBLED|name|output_path|bytes
#
# Examples:
#   # Review — single task file (reviewers are read-only)
#   .opencode/tools/assemble-task.sh -a code-reviewer -t review -n s1-reviewer --task tmp/task.txt
#
#   # Code implementation — writes directly to original files
#   .opencode/tools/assemble-task.sh -a python-pro -t code -n s1-impl --task tmp/s1-impl-task.txt

set -euo pipefail

# ── Locate repo assets (templates, agents) via SCRIPT_DIR ──
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
cd "$REPO_ROOT"
AGENTS_DIR="$REPO_ROOT/.opencode/agents"
TEMPLATES_DIR="$REPO_ROOT/.opencode/templates"

# ── Parse arguments ──
AGENT="" TYPE="" NAME="" TASK_FILE="" OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--agent)     AGENT="$2";     shift 2 ;;
    -t|--task-type) TYPE="$2";      shift 2 ;;
    -n|--name)      NAME="$2";      shift 2 ;;
    --task)         TASK_FILE="$2"; shift 2 ;;
    -o|--output)    OUTPUT="$2";    shift 2 ;;
    -h|--help)      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "ERROR: Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Validate required args ──
[[ -z "$AGENT" ]]     && { echo "ERROR: -a AGENT required" >&2; exit 1; }
[[ -z "$TYPE" ]]      && { echo "ERROR: -t TYPE required (review|code|research)" >&2; exit 1; }
[[ -z "$NAME" ]]      && { echo "ERROR: -n NAME required" >&2; exit 1; }
[[ -z "$TASK_FILE" ]] && { echo "ERROR: --task FILE required" >&2; exit 1; }

# Reject NAME values that would break sed {NAME} substitution or filenames
case "$NAME" in
  */*|*\\*|*\|*|*\&*|*\$*|*\"*|*\`*)
    echo "ERROR: NAME contains unsafe characters (/, \\, |, &, \$, \", \`): $NAME" >&2
    exit 1
    ;;
esac

# Reject AGENT values that would enable path traversal
case "$AGENT" in
  */*|*\\*|*\.\.*)
    echo "ERROR: AGENT contains unsafe characters (/, \\, ..): $AGENT" >&2
    exit 1
    ;;
esac

# ── Resolve input files ──
AGENT_MD="$AGENTS_DIR/${AGENT}.md"
[[ ! -f "$AGENT_MD" ]]   && { echo "ERROR: Agent file not found: $AGENT_MD (subagent_type must match a loaded agent)" >&2; exit 1; }
[[ ! -s "$AGENT_MD" ]]   && { echo "ERROR: Agent file is empty: $AGENT_MD" >&2; exit 1; }
[[ ! -f "$TASK_FILE" ]]  && { echo "ERROR: Task file not found: $TASK_FILE" >&2; exit 1; }
[[ ! -s "$TASK_FILE" ]]  && { echo "ERROR: Task file is empty: $TASK_FILE" >&2; exit 1; }

# ── Select templates based on task type ──
INCLUDE_SEVERITY=false
case "$TYPE" in
  review)
    COORDINATION="$TEMPLATES_DIR/coordination-review.txt"
    QUALITY="$TEMPLATES_DIR/quality-rules-review.txt"
    SEVERITY="$TEMPLATES_DIR/severity-guide.txt"
    INCLUDE_SEVERITY=true
    ;;
  research)
    COORDINATION="$TEMPLATES_DIR/coordination-review.txt"
    QUALITY="$TEMPLATES_DIR/quality-rules-review.txt"
    SEVERITY=""
    ;;
  code)
    COORDINATION="$TEMPLATES_DIR/coordination-code.txt"
    QUALITY="$TEMPLATES_DIR/quality-rules-code.txt"
    SEVERITY=""
    ;;
  *)
    echo "ERROR: Invalid task type '$TYPE' — must be review|code|research" >&2
    exit 1
    ;;
esac

# ── Validate templates exist ──
for f in "$COORDINATION" "$QUALITY"; do
  [[ ! -f "$f" ]] && { echo "ERROR: Template not found: $f" >&2; exit 1; }
  [[ ! -s "$f" ]] && { echo "ERROR: Template is empty: $f" >&2; exit 1; }
done
if [[ "$INCLUDE_SEVERITY" == "true" ]]; then
  [[ ! -f "$SEVERITY" ]] && { echo "ERROR: Template not found: $SEVERITY" >&2; exit 1; }
  [[ ! -s "$SEVERITY" ]] && { echo "ERROR: Template is empty: $SEVERITY" >&2; exit 1; }
fi

# ── Output path ──
[[ -z "$OUTPUT" ]] && OUTPUT="${REPO_ROOT}/tmp/${NAME}-task-prompt.txt"
OUT_DIR="$(dirname "$OUTPUT")"
mkdir -p "$OUT_DIR"

# ── Assemble task prompt ──
# Cache-aware ordering: stable content first (reused across calls = cached),
# volatile content last (per-call = uncached). Provider prompt caches match
# on exact prefix — if byte 1 differs, the entire cache invalidates.
{
  # ── STABLE PREFIX (shared across all calls of same type) ──
  sed "s|{NAME}|${NAME}|g" "$COORDINATION"
  printf '\n\n'
  if [[ "$INCLUDE_SEVERITY" == "true" ]]; then
    cat "$SEVERITY"
    printf '\n\n'
  fi
  cat "$QUALITY"
  printf '\n'
  # ── VOLATILE SUFFIX (unique per agent instance) ──
  printf 'You are an AI agent named %s.\n\n' "$NAME"
  # ── OUTPUT DIRECTORY — before TASK ASSIGNMENT to prevent PROJECT anchoring bias ──
  printf '%s\n' '--- OUTPUT DIRECTORY ---'
  printf 'All reports and output files go to: %s/tmp/\n' "$REPO_ROOT"
  printf '%s\n\n' 'The PROJECT directory (below) is for READING source files — do NOT write reports there.'
  printf '%s\n\n' '--- TASK ASSIGNMENT ---'
  # Substitute {NAME}, then strip standalone report-file paths the lead wrote
  # (only lines that are sole report paths — prose references like
  # "See s1-reviewer-report.md for context" are preserved).
  # Resolve relative tmp/ references to absolute. Idempotent: protect any
  # pre-existing ${REPO_ROOT}/tmp/ so absolute paths are never double-prefixed.
  # The word-boundary equivalent (^|[^[:alnum:]_]) is pure POSIX ERE — it
  # replaces GNU-only [[:<:]] (unsupported by MSYS/BSD sed). The , delimiter
  # keeps the alternation | unescaped, so it is valid on GNU, BSD, and MSYS.
  sed "s|{NAME}|${NAME}|g" "$TASK_FILE" \
    | sed -E '/^[[:space:]]*(-[[:space:]]*)?(tmp\/)?[a-zA-Z0-9_.-]+-report\.md[[:space:]]*$/d' \
    | sed "s|${REPO_ROOT}/tmp/|@REPO_TMP_PLACEHOLDER@|g" \
    | sed -E "s,(^|[^[:alnum:]_])tmp/,\1${REPO_ROOT}/tmp/,g" \
    | sed "s|@REPO_TMP_PLACEHOLDER@|${REPO_ROOT}/tmp/|g"
  printf '\n'
  # Auto-inject the WRITABLE FILES directive. For review/research types,
  # source files are read-only. For code type, source files from the task
  # file's WRITABLE FILES section may be writable.
  printf '%s\n' '--- WRITABLE FILES (automatic) ---'
  printf 'You must write your report to EXACTLY `%s/tmp/%s-report.md`.\n' "$REPO_ROOT" "$NAME"
  printf '%s\n' '(This is your orchestrator working directory. NOT the PROJECT directory.)'
  case "$TYPE" in
    review|research)
      printf 'All source files are READ-ONLY — do NOT modify them.\n'
      ;;
    code)
      printf 'You may modify files listed in the WRITABLE FILES section of the task above.\n'
      printf 'All other source files are READ-ONLY.\n'
      ;;
  esac
  printf '\n'
} > "$OUTPUT"

# ── Validate non-empty output ──
[[ ! -s "$OUTPUT" ]] && { echo "ERROR: Output file is empty after assembly: $OUTPUT" >&2; exit 1; }

# ── Validate no unsubstituted template variables remain ──
if grep -q '{NAME}' "$OUTPUT" 2>/dev/null; then
  echo "ERROR: Unsubstituted {NAME} found in assembled task prompt: $OUTPUT" >&2
  exit 1
fi

BYTES=$(wc -c < "$OUTPUT" | tr -d ' ')
echo "ASSEMBLED|${NAME}|${OUTPUT}|${BYTES}"
