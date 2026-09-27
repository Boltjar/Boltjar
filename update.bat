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

rem cmd reads this file one line at a time, from a saved byte offset, and the
rem pull can replace it with a new version. So the pull and every step after it
rem sit in one block, which cmd reads whole before it runs any of it, and the
rem block ends the script. Inside it, no goto or call :label: both read the file.
rem Both ways through end on the block's last line, which hands back the exit
rem code (1, or start.bat's): %errorlevel% in a block expands when the block is
rem read, and call expands it again when the line runs. An exit earlier in the
rem block would return 0 to a caller that ran this through cmd /c.
(
  git pull --ff-only
  if errorlevel 1 (
    echo The update stopped: git could not fast-forward ^(local changes, or a branch that has diverged^).
    echo.
    pause
    cmd /c exit 1
  ) else (
    git diff --quiet %BEFORE% HEAD -- editor || (
      where npm >nul 2>&1
      if errorlevel 1 (
        echo The editor changed but npm was not found: install Node.js 18+ from https://nodejs.org to rebuild it.
      ) else (
        echo The editor changed: rebuilding it...
        set "FRESH_MODULES="
        if not exist "editor\node_modules" set "FRESH_MODULES=1"
        git diff --quiet %BEFORE% HEAD -- editor/package-lock.json || set "FRESH_MODULES=1"
        if defined FRESH_MODULES call npm ci --prefix editor
        call npm run build --prefix editor || echo The editor build failed: run "npm run build --prefix editor" to see why.
      )
    )
    call "%~dp0start.bat" %*
  )
  call exit /b %%errorlevel%%
)


:failed
echo.
pause
exit /b 1
