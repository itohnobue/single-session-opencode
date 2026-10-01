#!/usr/bin/env sh
# CodeGraph CLI wrapper — repo-local, self-bootstrapping (uv model: no system
# install, no PATH edits). Installs the bundle into tmp/codegraph/ on first use.
# Usage: ./.opencode/tools/codegraph.sh <subcommand> [args]
# macOS / Linux / MSYS(git-bash). Windows cmd/PowerShell: use codegraph.bat.
set -u

# Resolve the real script dir (follow symlinks), then the repo root.
SELF="$0"
while [ -L "$SELF" ]; do
  target="$(readlink "$SELF")"
  case "$target" in
    /*) SELF="$target" ;;
    *)  SELF="$(dirname "$SELF")/$target" ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR/../.." rev-parse --show-toplevel 2>/dev/null)"
[ -n "$REPO_ROOT" ] || REPO_ROOT="$(git -C . rev-parse --show-toplevel 2>/dev/null || printf '%s' "$SCRIPT_DIR/../..")"

CG_DIR="$REPO_ROOT/tmp/codegraph"
INSTALL_SH_URL="https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh"
INSTALL_PS1_URL="https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1"

is_windows() {
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac
}

if is_windows; then
  CG_BIN="${CODEGRAPH_BIN:-$CG_DIR/current/bin/codegraph.cmd}"
else
  CG_BIN="${CODEGRAPH_BIN:-$CG_DIR/bin/codegraph}"
fi

bootstrap() {
  echo "Installing CodeGraph (latest) into $CG_DIR ..." >&2
  mkdir -p "$CG_DIR"
  if is_windows; then
    # The POSIX installer refuses on Windows; bootstrap via PowerShell, then
    # undo the user-PATH entry install.ps1 adds (we invoke by absolute path).
    CG_DIR_WIN="$(cygpath -w "$CG_DIR" 2>/dev/null || printf '%s' "$CG_DIR")"
    powershell -NoProfile -ExecutionPolicy Bypass -Command \
      "\$env:CODEGRAPH_INSTALL_DIR='$CG_DIR_WIN'; irm $INSTALL_PS1_URL | iex" >/dev/null
    powershell -NoProfile -ExecutionPolicy Bypass -Command \
      "\$b=Join-Path '$CG_DIR_WIN' 'current\\bin'; \$p=[Environment]::GetEnvironmentVariable('Path','User'); if(\$p){ \$n=((\$p -split ';') | Where-Object { \$_ -ne \$b -and \$_ -ne '' }) -join ';'; if(\$n -ne \$p){ [Environment]::SetEnvironmentVariable('Path',\$n,'User') } }" >/dev/null 2>&1
  else
    curl -fsSL "$INSTALL_SH_URL" | env CODEGRAPH_INSTALL_DIR="$CG_DIR" CODEGRAPH_BIN_DIR="$CG_DIR/bin" sh >/dev/null
  fi
}

if ! "$CG_BIN" version >/dev/null 2>&1; then
  bootstrap
  if ! "$CG_BIN" version >/dev/null 2>&1; then
    echo "CodeGraph verification failed; retrying once ..." >&2
    bootstrap
  fi
fi
if ! "$CG_BIN" version >/dev/null 2>&1; then
  echo "error: CodeGraph is not available ($CG_BIN)" >&2
  exit 2
fi

cd "$REPO_ROOT" || exit 1

# Always-fresh contract: sync the index before index-READ commands (fast
# incremental no-op when nothing changed). Index-management commands skip it.
case "${1:-}" in
  ""|init|index|uninit|unlock|install|uninstall|upgrade|daemon|daemons|telemetry|version|-v|--version|help|-h|--help)
    ;;
  *)
    "$CG_BIN" sync >/dev/null 2>&1 || true
    ;;
esac

exec "$CG_BIN" "$@"
