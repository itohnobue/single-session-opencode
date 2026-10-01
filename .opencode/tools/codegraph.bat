@echo off
setlocal enabledelayedexpansion
REM CodeGraph CLI wrapper (Windows) - repo-local, self-bootstrapping (uv model:
REM no system install, no PATH edits). Installs the bundle into tmp\codegraph on first use.
REM Usage: codegraph.bat <subcommand> [args]

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

set "CG_DIR=%REPO_ROOT%\tmp\codegraph"
set "CG_BIN=%CG_DIR%\current\bin\codegraph.cmd"
if defined CODEGRAPH_BIN set "CG_BIN=%CODEGRAPH_BIN%"
set "CG_PS1=https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1"

if not exist "%CG_BIN%" call :bootstrap
if not exist "%CG_BIN%" (
    echo CodeGraph verification failed; retrying once ...
    call :bootstrap
)
if not exist "%CG_BIN%" (
    echo error: CodeGraph is not available ^(%CG_BIN%^) 1>&2
    exit /b 2
)

pushd "%REPO_ROOT%" >nul 2>nul
echo %1 | findstr /I /B "init index uninit unlock install uninstall upgrade daemon daemons telemetry version help" >nul 2>nul
if errorlevel 1 (
    call "%CG_BIN%" sync >nul 2>nul
)
call "%CG_BIN%" %*
set "EC=%errorlevel%"
popd
exit /b %EC%

:bootstrap
echo Installing CodeGraph ^(latest^) into "%CG_DIR%" ...
if not exist "%CG_DIR%" mkdir "%CG_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:CODEGRAPH_INSTALL_DIR='%CG_DIR%'; irm %CG_PS1% | iex" >nul
REM install.ps1 appends its bin dir to the user PATH - undo it (repo-local, no PATH edits).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=Join-Path '%CG_DIR%' 'current\bin'; $p=[Environment]::GetEnvironmentVariable('Path','User'); if($p){ $n=(($p -split ';') | Where-Object { $_ -ne $b -and $_ -ne '' }) -join ';'; if($n -ne $p){ [Environment]::SetEnvironmentVariable('Path',$n,'User') } }" >nul 2>nul
exit /b 0
