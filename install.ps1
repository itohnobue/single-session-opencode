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
  4. Creates tmp\ directory for agent working files

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
        Write-Host "  Merging new files (existing files will NOT be overwritten)..."

        Get-ChildItem -Path $srcOpencode -Recurse -File | ForEach-Object {
            $relPath = $_.FullName.Substring($srcOpencode.Length)
            $destFile = Join-Path $opencodeDir $relPath
            $destDir = Split-Path $destFile -Parent

            if (-not (Test-Path $destFile)) {
                if (-not (Test-Path $destDir)) {
                    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
                }
                Copy-Item $_.FullName $destFile
            }
        }
        Write-Info "Merged into existing .opencode\"
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
            Write-Info "AGENTS.md already contains workflow instructions"
        } else {
            Write-Warn "AGENTS.md exists but doesn't have workflow instructions"
            Write-Host "  You can append them manually:"
            Write-Host "    Get-Content $srcAgentsMd | Add-Content $agentsMd"
        }
    } else {
        Copy-Item -Path $srcAgentsMd -Destination $agentsMd
        Write-Info "Created AGENTS.md with single-session workflow instructions"
    }

    # -- Step 4: tmp\ directory --
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
    Write-Host "    tmp\                  Agent working directory"
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor White
    Write-Host "    cd $Target"
    Write-Host "    opencode"
    Write-Host "    # Give it any task - the suite activates automatically"
    Write-Host ""
}

Main
