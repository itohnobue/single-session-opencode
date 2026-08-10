#!/usr/bin/env bash
# assemble-task.sh — Compose a task prompt for native opencode subagent delegation
#
# Builds the task prompt (templates + optional RESEARCH DATA injection +
# task assignment) — the agent .md is loaded natively by opencode as the
# subagent's system prompt, so it is NOT embedded here.
#
# The assembled task prompt is passed to the opencode `task` tool as the `prompt`
# parameter (subagent_type = AGENT). Agents run as native opencode subagents with
# full permissions inherited from the project config.
#
# Usage:
#   .opencode/tools/assemble-task.sh -a AGENT -t TYPE -n NAME --task TASK_FILE [-o OUT] [--research-file RESEARCH_FILE]
#
# Arguments:
#   -a, --agent       Agent name — validates .opencode/agents/{agent}.md exists
#   -t, --task-type   Task type: review | code | research | prepare
#   -n, --name        Agent instance name (e.g. exec-review, impl-db, prepare-web)
#   --task            Path to task assignment file (PROJECT, ENVIRONMENT,
#                     PRIOR CONTEXT, YOUR TASK, WRITABLE FILES — main-model-written)
#   --research-file   Path to the prepare agent's research-data file — injected as the
#                     `## RESEARCH DATA` section between template and task (prepare+execute pipeline)
#   -o, --output      Override output path (default: tmp/{name}-task-prompt.txt)
#
# Task type → template selection:
#   review:   coordination-review + severity-guide + quality-rules-review
#   code:     coordination-code   +                  quality-rules-code
#   research: coordination-review +                  quality-rules-review
#   prepare:  coordination-prepare +                 quality-rules-review
#
# Output (stdout):
#   ASSEMBLED|name|output_path|bytes
#
# Examples:
#   # Executor (prepare+execute standard): template → RESEARCH DATA → task
#   .opencode/tools/assemble-task.sh -a executor-high -t code -n exec-impl (or -a executor-max for deep analysis/investigation tasks) --task tmp/impl-task.txt --research-file tmp/prepare/impl-research.md -o tmp/exec-impl-task-prompt.txt
#
#   # Prepare phase (research generation)
#   .opencode/tools/assemble-task.sh -a prepare-agent -t prepare -n prepare-impl --task tmp/prepare-impl-task.txt
#
#   # Second opinion: second prepare (different FOCUS) + second executor

set -euo pipefail

# ── Locate repo assets (templates, agents) via SCRIPT_DIR ──
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
cd "$REPO_ROOT"
AGENTS_DIR="$REPO_ROOT/.opencode/agents"
TEMPLATES_DIR="$REPO_ROOT/.opencode/templates"

# Escape & in REPO_ROOT so it is literal in sed replacements (valid dir chars
# on macOS/Linux/Windows; & and | would otherwise corrupt s||| delimiters)
REPO_ROOT_SED="${REPO_ROOT//&/\\&}"

# ── Parse arguments ──
AGENT="" TYPE="" NAME="" TASK_FILE="" OUTPUT="" RESEARCH_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--agent)     AGENT="$2";     shift 2 ;;
    -t|--task-type) TYPE="$2";      shift 2 ;;
    -n|--name)      NAME="$2";      shift 2 ;;
    --task)         TASK_FILE="$2"; shift 2 ;;
    --research-file) RESEARCH_FILE="$2"; shift 2 ;;
    -o|--output)    OUTPUT="$2";    shift 2 ;;
    -h|--help)      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "ERROR: Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Validate required args ──
[[ -z "$AGENT" ]]     && { echo "ERROR: -a AGENT required" >&2; exit 1; }
[[ -z "$TYPE" ]]      && { echo "ERROR: -t TYPE required (review|code|research|prepare)" >&2; exit 1; }
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
if [[ -n "$RESEARCH_FILE" ]]; then
  [[ ! -f "$RESEARCH_FILE" ]] && { echo "ERROR: Research file not found: $RESEARCH_FILE" >&2; exit 1; }
  [[ ! -s "$RESEARCH_FILE" ]] && { echo "ERROR: Research file is empty: $RESEARCH_FILE" >&2; exit 1; }
  # Guard against double injection: the task file must NOT already contain research data
  # (use EITHER inject-research.sh + plain assemble, OR --research-file — never both)
  if grep -qi '^## RESEARCH DATA' "$TASK_FILE"; then
    echo "ERROR: Task file already contains a RESEARCH DATA section AND --research-file was given — double injection." >&2
    echo "       Use one path: (a) --research-file <file> with the RAW task file, or (b) pre-injected task file without the flag." >&2
    exit 1
  fi
fi

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
  prepare)
    COORDINATION="$TEMPLATES_DIR/coordination-prepare.txt"
    QUALITY="$TEMPLATES_DIR/quality-rules-review.txt"
    SEVERITY=""
    ;;
  *)
    echo "ERROR: Invalid task type '$TYPE' — must be review|code|research|prepare" >&2
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
  # Research-first pipeline: inject the prepare agent's research data right
  # after the template, before the task (structure: template → RESEARCH DATA → task).
  if [[ -n "$RESEARCH_FILE" ]]; then
    printf '%s\n' '## RESEARCH DATA (prepared by prepare agent — your briefing)'
    printf '%s\n\n' 'This is the research the prepare agent did for this task. It is your briefing: use it, do not redo the research. Shape your working form from it before starting the task.'
    cat "$RESEARCH_FILE"
    printf '\n%s\n\n' '---'
  fi
  # Substitute {NAME}, then strip standalone report-file paths written by the main model
  # (only lines that are sole report paths — prose references like
  # "See review-auth-report.md for context" are preserved).
  # Resolve relative tmp/ references to absolute. Idempotent: protect any
  # pre-existing ${REPO_ROOT}/tmp/ so absolute paths are never double-prefixed.
  # The word-boundary equivalent (^|[^[:alnum:]_]) is pure POSIX ERE — it
  # replaces GNU-only [[:<:]] (unsupported by MSYS/BSD sed). The , delimiter
  # keeps the alternation | unescaped, so it is valid on GNU, BSD, and MSYS.
  sed "s|{NAME}|${NAME}|g" "$TASK_FILE" \
    | sed -E '/^[[:space:]]*(-[[:space:]]*)?(tmp\/)?[a-zA-Z0-9_.-]+-report\.md[[:space:]]*$/d' \
    | sed "s|${REPO_ROOT_SED}/tmp/|@REPO_TMP_PLACEHOLDER@|g" \
    | sed -E "s,(^|[^[:alnum:]_])tmp/,\1${REPO_ROOT_SED}/tmp/,g" \
    | sed "s|@REPO_TMP_PLACEHOLDER@|${REPO_ROOT_SED}/tmp/|g"
  printf '\n'
  # Auto-inject the WRITABLE FILES directive. For review/research types,
  # source files are read-only. For code type, source files from the task
  # file's WRITABLE FILES section may be writable.
  printf '%s\n' '--- WRITABLE FILES (automatic) ---'
  printf 'Write your report to EXACTLY `%s/tmp/%s-report.md` UNLESS the task file has a DELIVERABLES section specifying explicit report paths — then use those.\n' "$REPO_ROOT" "$NAME"
  printf '%s\n' '(This is your working directory. NOT the PROJECT directory.)'
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
