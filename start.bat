@echo off
rem Boltjar for Windows. Finds Python 3.11 to 3.13 (or fetches a portable 3.12),
rem prepares .venv, installs requirements.txt when it changed, builds the editor
rem when it is missing, then runs the server. Arguments pass through to
rem "python -m boltjar serve", for example:  start.bat --port 8771 --no-browser
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Boltjar

rem The portable Python used when none is installed: a pinned release of
rem https://github.com/astral-sh/python-build-standalone. PBS_SUMS_SHA256 pins
rem that release's SHA256SUMS file, and SHA256SUMS pins the archive.
set "PBS_TAG=20260924"
set "PBS_PYTHON=3.12.14"
set "PBS_SUMS_SHA256=f4c51660f65ce980a1113028ce6cb751d6251e17550bc2252bfd2be878c2cc1e"
set "PBS_ASSET=cpython-%PBS_PYTHON%+%PBS_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz"
set "PBS_ASSET_URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PBS_TAG%/cpython-%PBS_PYTHON%%%2B%PBS_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz"
set "PBS_SUMS_URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PBS_TAG%/SHA256SUMS"

set "VENV_PY=.venv\Scripts\python.exe"
set "SUPPORTED=import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)"

if exist "%VENV_PY%" goto :venv_ready
if exist ".venv" (
  echo The .venv folder has no Scripts\python.exe ^(it was made on another system^).
  echo Delete the .venv folder and run start.bat again to rebuild it.
  goto :failed
)
call :find_python || goto :failed
echo Creating .venv with %PY_LABEL%...
"%PY%" %PY_ARGS% -m venv .venv || goto :failed

:venv_ready
"%VENV_PY%" -c "%SUPPORTED%" >nul 2>&1
if errorlevel 1 (
  echo .venv holds a Python that Boltjar does not support ^(it needs 3.11 to 3.13^).
  echo Delete the .venv folder and run start.bat again to rebuild it.
  goto :failed
)
"%VENV_PY%" -m boltjar.deps || goto :failed
if not exist "editor\dist\index.html" call :build_editor
"%VENV_PY%" -m boltjar serve %*
set "CODE=%errorlevel%"
if "%CODE%"=="0" exit /b 0
if "%CODE%"=="130" exit /b 130
goto :failed


:find_python
rem 1. the py launcher, 3.12 first
for %%V in (3.12 3.13 3.11) do (
  py -%%V -c "%SUPPORTED%" >nul 2>&1 && (
    set "PY=py"
    set "PY_ARGS=-%%V"
    set "PY_LABEL=Python %%V from the py launcher"
    exit /b 0
  )
)
rem 2. python on PATH (the Microsoft Store placeholder fails this check)
python -c "%SUPPORTED%" >nul 2>&1 && (
  set "PY=python"
  set "PY_ARGS="
  set "PY_LABEL=the Python on PATH"
  exit /b 0
)
rem 3. the portable Python, fetched now unless an earlier run did
if not exist ".python\python.exe" call :fetch_python || exit /b 1
set "PY=%CD%\.python\python.exe"
set "PY_ARGS="
set "PY_LABEL=the portable Python in .python"
exit /b 0


:fetch_python
if exist ".python" (
  echo The .python folder holds no working Python: delete it and run start.bat again to fetch it anew.
  exit /b 1
)
echo No Python 3.11 to 3.13 was found. Fetching a portable Python %PBS_PYTHON%, about 46 MB...
where curl.exe >nul 2>&1 || goto :no_tools
where tar.exe >nul 2>&1 || goto :no_tools
set "DL=%TEMP%\boltjar-python-%PBS_TAG%"
if exist "%DL%" rmdir /s /q "%DL%"
mkdir "%DL%" || exit /b 1
curl.exe -fsSL --retry 2 -o "%DL%\SHA256SUMS" "%PBS_SUMS_URL%" || goto :fetch_failed
call :sha256 "%DL%\SHA256SUMS" GOT
if /i not "%GOT%"=="%PBS_SUMS_SHA256%" (
  echo SHA256SUMS does not match the hash pinned in start.bat, so it is not used.
  goto :fetch_failed
)
set "WANT="
for /f "tokens=1" %%H in ('findstr /l /c:"  %PBS_ASSET%" "%DL%\SHA256SUMS"') do set "WANT=%%H"
if not defined WANT (
  echo %PBS_ASSET% is not listed in SHA256SUMS.
  goto :fetch_failed
)
curl.exe -fL --retry 2 -o "%DL%\python.tar.gz" "%PBS_ASSET_URL%" || goto :fetch_failed
call :sha256 "%DL%\python.tar.gz" GOT
if /i not "%GOT%"=="%WANT%" (
  echo The download does not match its SHA256, so it is not used.
  goto :fetch_failed
)
if exist ".python.partial" rmdir /s /q ".python.partial"
mkdir ".python.partial" || goto :fetch_failed
tar.exe -xzf "%DL%\python.tar.gz" -C ".python.partial" --strip-components=1 || goto :fetch_failed
ren ".python.partial" ".python" || goto :fetch_failed
rmdir /s /q "%DL%"
exit /b 0

:fetch_failed
if exist ".python.partial" rmdir /s /q ".python.partial"
if exist "%DL%" rmdir /s /q "%DL%"
echo Could not set up the portable Python. Install Python 3.12 from https://www.python.org/downloads/ and run start.bat again.
exit /b 1

:no_tools
echo curl.exe and tar.exe come with Windows 10 1803 and later, and one is missing here.
echo Install Python 3.12 from https://www.python.org/downloads/ and run start.bat again.
exit /b 1


:sha256
rem %1 = a file, %2 = the variable that receives its SHA-256 (hex, no spaces)
set "%~2="
for /f "skip=1 delims=" %%L in ('certutil -hashfile "%~1" SHA256') do (
  if not defined %~2 set "%~2=%%L"
)
call set "%~2=%%%~2: =%%"
exit /b 0


:build_editor
where npm >nul 2>&1 || (
  echo The editor is not built and npm was not found: install Node.js 18+ from https://nodejs.org and run start.bat again. The server starts without the editor page.
  exit /b 0
)
echo Building the editor, first run only...
if not exist "editor\node_modules" call npm ci --prefix editor || (
  echo npm ci failed: the server starts without the editor page.
  exit /b 0
)
call npm run build --prefix editor || echo The editor build failed: the server starts without the editor page.
exit /b 0


:failed
echo.
echo Boltjar did not start: the lines above say why.
pause
exit /b 1
