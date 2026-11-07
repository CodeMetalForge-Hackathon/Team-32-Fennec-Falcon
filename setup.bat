@echo off
setlocal
cd /d "%~dp0"

echo [1/5] Finding Python ...
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import venv" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo No Python found. Installing Python 3.12 from python.org via winget ...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements || goto fail
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
echo Using: %PY%

echo [2/5] Creating .venv ...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto fail
)

echo [3/5] Installing OpenCV ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail

echo [4/5] Initializing git repo ...
where git >nul 2>&1 && (
  if not exist ".git" (
    git init -b main >nul
    git add -A >nul
    git commit -m "Initial thermal camera project" >nul 2>&1
  )
) || echo Git not found, skipping.

echo [5/5] Opening VS Code ...
where code >nul 2>&1 || (
  echo VS Code "code" command not found. Open this folder in VS Code manually.
  pause & exit /b 0
)
call code --install-extension ms-python.python >nul
call code --install-extension ms-python.debugpy >nul
call code "%~dp0"
echo Done.
timeout /t 5 >nul
exit /b 0

:fail
echo Setup failed. See the error above.
pause
exit /b 1
