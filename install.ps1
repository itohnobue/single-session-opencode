# ============================================================================
# OpenCode Single-Session Agent Suite Installer for Windows
#
# Installs the OpenCode Single-Session Agent Suite into a target project.
# Requires OpenCode CLI (https://opencode.ai) to be installed and in PATH.
#
# Usage:
#   .\install.ps1 C:\path\to\your\project     Install into project
#   .\install.ps1 -Help                        Show help
# ============================================================================

param(
    [Parameter(Position=0)]
    [string]$TargetPath,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-Err {
    param([string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> " -ForegroundColor Cyan -NoNewline
    Write-Host $Message -ForegroundColor White
}

function Show-Help {
    @"
OpenCode Single-Session Agent Suite Installer for Windows

Installs the OpenCode Single-Session Agent Suite into a target project directory.

Usage:
  .\install.ps1 C:\path\to\your\project     Install into project
  .\install.ps1 -Help                        Show this help

What it does:
  1. Checks that OpenCode CLI is installed and in PATH
  2. Copies .opencode\ directory (agents, tools, templates) to your project
  3. Creates AGENTS.md with single-session workflow instructions
  4. Creates opencode.json with default allowance (skipped if one exists)
  5. Creates tmp\ directory for agent working files

If .opencode\ already exists, suite files are synchronized to the current
version: .opencode\agents\ is suite-owned (agent definitions not shipped by
the suite are removed, all others updated), tools and templates are updated,
and files you created yourself outside agents\ are kept.
Your existing AGENTS.md is never overwritten.

After installation:
  - Open your project with OpenCode
  - Give it tasks - the model works directly, spawning specialist agents as needed
"@
}

function Main {
    if ($Help) {
        Show-Help
        return
    }

    if ([string]::IsNullOrWhiteSpace($TargetPath)) {
        Write-Err "Target project path required"
        Write-Host ""
        Write-Host "Usage: .\install.ps1 C:\path\to\your\project"
        Write-Host ""
        exit 1
    }

    $Target = Resolve-Path $TargetPath -ErrorAction SilentlyContinue
    if (-not $Target) {
        Write-Err "Directory not found: $TargetPath"
        exit 1
    }
    $Target = $Target.Path

    Write-Host ""
    Write-Host "+======================================+" -ForegroundColor White
    Write-Host "|  Single-Session Agent Suite Installer|" -ForegroundColor White
    Write-Host "+======================================+" -ForegroundColor White
    Write-Host ""
    Write-Host "  Target: $Target"

    # -- Step 1: Check OpenCode CLI --
    Write-Step "Checking OpenCode CLI"

    $opencodeExe = Get-Command opencode -ErrorAction SilentlyContinue
    if ($opencodeExe) {
        Write-Info "OpenCode CLI found: $($opencodeExe.Source)"
    } else {
        Write-Warn "OpenCode CLI not found in PATH"
        Write-Host "  Agents are spawned as native opencode subagents (task tool) - OpenCode must be installed."
        Write-Host "  Install from: https://opencode.ai"
        Write-Host ""
        $answer = Read-Host "  Continue anyway? [y/N]"
        if ($answer -notmatch "^[yY]") {
            Write-Err "Aborting. Install OpenCode first: https://opencode.ai"
            exit 1
        }
        Write-Warn "Continuing without OpenCode - agents will not spawn"
    }

    # -- Step 2: Copy .opencode\ --
    Write-Step "Installing .opencode\ directory"

    $opencodeDir = Join-Path $Target ".opencode"
    $srcOpencode = Join-Path $ScriptDir ".opencode"

    if (Test-Path $opencodeDir) {
        Write-Warn ".opencode\ directory already exists in target"
        Write-Host "  Synchronizing suite files to the current version..."

        # Remove stale agent definitions: agent .md files not shipped by the
        # suite (e.g. leftovers from the old 109-agent version) are removed.
        $srcAgentNames = Get-ChildItem -Path (Join-Path $srcOpencode "agents\*.md") -File | ForEach-Object { $_.Name }
        Get-ChildItem -Path (Join-Path $opencodeDir "agents\*.md") -File | ForEach-Object {
            if ($srcAgentNames -notcontains $_.Name) {
                Remove-Item $_.FullName -Force
                Write-Host "  Removed stale agent: $($_.Name)"
            }
        }

        # Copy all suite files, overwriting previous versions (agents, templates,
        # tools, completions). User-created files outside the suite are kept.
        Copy-Item -Path (Join-Path $srcOpencode "*") -Destination $opencodeDir -Recurse -Force
        Write-Info "Synchronized .opencode\ to the current suite version"
    } else {
        Copy-Item -Path $srcOpencode -Destination $opencodeDir -Recurse
        Write-Info "Installed .opencode\ directory"
    }

    # -- Step 3: AGENTS.md --
    Write-Step "Setting up AGENTS.md"

    $agentsMd = Join-Path $Target "AGENTS.md"
    $srcAgentsMd = Join-Path $ScriptDir "AGENTS.md"

    if (Test-Path $agentsMd) {
        $content = Get-Content $agentsMd -Raw
        if ($content -match "OpenCode") {
            if ($content -match "109") {
                Write-Warn "AGENTS.md is from an old suite version (109-agent workflow)"
                Write-Host "  AGENTS.md was NOT overwritten - replace it with the new workflow manually:"
                Write-Host "    Copy-Item $srcAgentsMd $agentsMd -Force"
            } else {
                Write-Info "AGENTS.md already contains workflow instructions"
            }
        } else {
            Write-Warn "AGENTS.md exists but doesn't have workflow instructions"
            Write-Host "  You can append them manually:"
            Write-Host "    Get-Content $srcAgentsMd | Add-Content $agentsMd"
        }
    } else {
        Copy-Item -Path $srcAgentsMd -Destination $agentsMd
        Write-Info "Created AGENTS.md with single-session workflow instructions"
    }

    # -- Step 4: opencode.json --
    Write-Step "Setting up opencode.json"
    $opencodeJson = Join-Path $Target "opencode.json"
    $srcOpencodeJson = Join-Path $ScriptDir "opencode.json"
    if (Test-Path $opencodeJson) {
        Write-Warn "opencode.json already exists in target - keeping it (may hold machine-local settings)"
    } else {
        Copy-Item -Path $srcOpencodeJson -Destination $opencodeJson
        Write-Info "Created opencode.json with default allowance (permission allow, no model pin)"
    }

    # -- Step 5: tmp\ directory --
    Write-Step "Creating tmp\ directory"
    $tmpDir = Join-Path $Target "tmp"
    if (-not (Test-Path $tmpDir)) {
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    }
    Write-Info "Created tmp\ for agent working files"

    # -- Done --
    $agentCount = (Get-ChildItem (Join-Path $opencodeDir "agents\*.md") | Where-Object { $_.Name -ne "INDEX.md" }).Count

    Write-Host ""
    Write-Host "+======================================+" -ForegroundColor Green
    Write-Host "|     Installation complete!            |" -ForegroundColor Green
    Write-Host "+======================================+" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Installed to: $Target"
    Write-Host ""
    Write-Host "  Contents:" -ForegroundColor White
    Write-Host "    .opencode\agents\     $agentCount agent definitions"
    Write-Host "    .opencode\tools\      Research & memory tools"
    Write-Host "    .opencode\templates\  Agent prompt boilerplate"
    Write-Host "    AGENTS.md             Single-session workflow instructions"
    Write-Host "    opencode.json         Default allowance (permission allow, no model pin)"
    Write-Host "    tmp\                  Agent working directory"
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor White
    Write-Host "    cd $Target"
    Write-Host "    opencode"
    Write-Host "    # Give it any task - the suite activates automatically"
    Write-Host ""
}

Main
