@echo off
rem Boltjar update for Windows. Pulls the latest code (fast-forward only),
rem rebuilds the editor when its source changed, then runs start.bat with the
rem same arguments. start.bat installs requirements.txt again if it changed.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

where git >nul 2>&1 || (
  echo git was not found: install it from https://git-scm.com and run update.bat again.
  goto :failed
)
git rev-parse --is-inside-work-tree >nul 2>&1 || (
  echo This folder is not a git checkout, so it cannot pull: download the latest release instead.
  goto :failed
)
for /f %%H in ('git rev-parse HEAD') do set "BEFORE=%%H"
git pull --ff-only || (
  echo The update stopped: git could not fast-forward ^(local changes, or a branch that has diverged^).
  goto :failed
)
git diff --quiet %BEFORE% HEAD -- editor || call :rebuild_editor
call "%~dp0start.bat" %*
exit /b %errorlevel%


:rebuild_editor
where npm >nul 2>&1 || (
  echo The editor changed but npm was not found: install Node.js 18+ from https://nodejs.org to rebuild it.
  exit /b 0
)
echo The editor changed: rebuilding it...
set "FRESH_MODULES="
if not exist "editor\node_modules" set "FRESH_MODULES=1"
git diff --quiet %BEFORE% HEAD -- editor/package-lock.json || set "FRESH_MODULES=1"
if defined FRESH_MODULES call npm ci --prefix editor
call npm run build --prefix editor || echo The editor build failed: run "npm run build --prefix editor" to see why.
exit /b 0


:failed
echo.
pause
exit /b 1
