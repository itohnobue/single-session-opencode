#!/usr/bin/env bash
# ============================================================================
# OpenCode Single-Session Agent Suite Installer
#
# Installs the OpenCode Single-Session Agent Suite into a target project.
# Requires OpenCode CLI (https://opencode.ai) to be installed and in PATH.
#
# Usage:
#   ./install.sh /path/to/your/project     Install into project
#   ./install.sh --help                     Show help
# ============================================================================

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# ── Colors ──
if [[ -t 1 ]]; then
  RED=$'\033[0;31m'
  GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'
  CYAN=$'\033[0;36m'
  BOLD=$'\033[1m'
  RESET=$'\033[0m'
else
  RED='' GREEN='' YELLOW='' CYAN='' BOLD='' RESET=''
fi

info()  { printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*" >&2; }
step()  { printf '\n%s==>%s %s%s%s\n' "$CYAN" "$RESET" "$BOLD" "$*" "$RESET"; }

show_help() {
  cat <<'HELP'
OpenCode Single-Session Agent Suite Installer

Installs the OpenCode Single-Session Agent Suite into a target project directory.

Usage:
  ./install.sh /path/to/your/project     Install into project
  ./install.sh --help                     Show this help

What it does:
  1. Checks that OpenCode CLI is installed and in PATH
  2. Copies .opencode/ directory (agents, tools, templates) to your project
  3. Creates AGENTS.md with single-session workflow instructions
  4. Creates opencode.json with default allowance (skipped if one exists)
  5. Creates tmp/ directory for agent working files

If .opencode/ already exists, suite files are synchronized to the current
version: .opencode/agents/ is suite-owned (agent definitions not shipped by
the suite are removed, all others updated), tools and templates are updated,
and files you created yourself outside agents/ are kept.
Your existing AGENTS.md is never overwritten.

After installation:
  - Open your project with OpenCode
  - Give it tasks — the model works directly, spawning specialist agents as needed
HELP
}

# ── Main ──
main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    show_help
    exit 0
  fi

  local target="${1:-}"

  if [[ -z "$target" ]]; then
    error "Target project path required"
    printf '\nUsage: ./install.sh /path/to/your/project\n\n'
    exit 1
  fi

  if [[ ! -d "$target" ]]; then
    error "Directory not found: $target"
    exit 1
  fi

  target="$(cd "$target" && pwd -P)"

  printf '\n%s╔══════════════════════════════════════╗%s\n' "$BOLD" "$RESET"
  printf '%s║     Single-Session Agent Suite         ║%s\n' "$BOLD" "$RESET"
  printf '%s╚══════════════════════════════════════╝%s\n\n' "$BOLD" "$RESET"

  printf '  Target: %s\n' "$target"

  # ── Step 1: Check OpenCode CLI ──
  step "Checking OpenCode CLI"

  if command -v opencode &>/dev/null; then
    info "OpenCode CLI found: $(command -v opencode)"
  else
    warn "OpenCode CLI not found in PATH"
    printf '  Agents are spawned as native opencode subagents (task tool) — OpenCode must be installed.\n'
    printf '  Install from: https://opencode.ai\n\n'
    printf '  Continue anyway? [y/N] '
    read -r answer
    case "$answer" in
      [yY]|[yY][eE][sS]) warn "Continuing without OpenCode — agents will not spawn" ;;
      *) error "Aborting. Install OpenCode first: https://opencode.ai"; exit 1 ;;
    esac
  fi

  # ── Step 2: Copy .opencode/ ──
  step "Installing .opencode/ directory"

  if [[ -d "$target/.opencode" ]]; then
    warn ".opencode/ directory already exists in target"
    printf '  Synchronizing suite files to the current version...\n'
    # Remove stale agent definitions: agent .md files not shipped by the suite
    # (e.g. leftovers from the old 109-agent version) are removed.
    for stale in "$target/.opencode/agents"/*.md; do
      [[ -e "$stale" ]] || continue
      base="${stale##*/}"
      if [[ ! -f "$SCRIPT_DIR/.opencode/agents/$base" ]]; then
        rm -f "$stale"
        printf '  Removed stale agent: %s\n' "$base"
      fi
    done
    # Copy all suite files, overwriting previous versions (agents, templates,
    # tools, completions). User-created files outside the suite are kept.
    cp -R "$SCRIPT_DIR/.opencode/." "$target/.opencode/"
    info "Synchronized .opencode/ to the current suite version"
  else
    cp -R "$SCRIPT_DIR/.opencode" "$target/.opencode"
    info "Installed .opencode/ directory"
  fi

  # Ensure all .sh scripts are executable (fixes macOS clone without +x)
  find "$target/.opencode" -name '*.sh' -exec chmod +x {} + 2>/dev/null || true

  # ── Step 3: AGENTS.md ──
  step "Setting up AGENTS.md"

  if [[ -f "$target/AGENTS.md" ]]; then
    if grep -q "OpenCode" "$target/AGENTS.md" 2>/dev/null; then
      if grep -q "109" "$target/AGENTS.md" 2>/dev/null; then
        warn "AGENTS.md is from an old suite version (109-agent workflow)"
        printf '  AGENTS.md was NOT overwritten — replace it with the new workflow manually:\n'
        printf '    cat %s/AGENTS.md > %s/AGENTS.md\n\n' "$SCRIPT_DIR" "$target"
      else
        info "AGENTS.md already exists with workflow instructions"
      fi
    else
      warn "AGENTS.md exists but doesn't have workflow instructions"
      printf '  You can append them manually:\n'
      printf '    cat %s/AGENTS.md >> %s/AGENTS.md\n\n' "$SCRIPT_DIR" "$target"
    fi
  else
    cp "$SCRIPT_DIR/AGENTS.md" "$target/AGENTS.md"
    info "Created AGENTS.md with single-session workflow instructions"
  fi

  # ── Step 4: opencode.json ──
  step "Setting up opencode.json"
  if [[ -f "$target/opencode.json" ]]; then
    warn "opencode.json already exists in target — keeping it (may hold machine-local settings)"
  else
    cp "$SCRIPT_DIR/opencode.json" "$target/opencode.json"
    info "Created opencode.json with default allowance (permission allow, no model pin)"
  fi

  # ── Step 5: tmp/ directory ──
  step "Creating tmp/ directory"
  mkdir -p "$target/tmp"
  info "Created tmp/ for agent working files"

  # ── Done ──
  printf '\n'
  printf '%s╔══════════════════════════════════════╗%s\n' "$GREEN" "$RESET"
  printf '%s║     Installation complete!            ║%s\n' "$GREEN" "$RESET"
  printf '%s╚══════════════════════════════════════╝%s\n' "$GREEN" "$RESET"
  printf '\n'
  printf '  Installed to: %s\n' "$target"
  printf '\n'
  printf '  %sContents:%s\n' "$BOLD" "$RESET"
  printf '    .opencode/agents/     %s agent definitions\n' "$(find "$target/.opencode/agents" -name '*.md' ! -name 'INDEX.md' 2>/dev/null | wc -l | tr -d ' ')"
  printf '    .opencode/tools/      Research & memory tools\n'
  printf '    .opencode/templates/  Agent prompt boilerplate\n'
  printf '    AGENTS.md             Single-session workflow instructions\n'
  printf '    opencode.json         Default allowance (permission allow, no model pin)\n'
  printf '    tmp/                  Agent working directory\n'
  printf '\n'
  printf '  %sUsage:%s\n' "$BOLD" "$RESET"
  printf '    cd %s\n' "$target"
  printf '    opencode\n'
  printf '    # Give it any task — the suite activates automatically\n'
  printf '\n'
}

main "$@"
