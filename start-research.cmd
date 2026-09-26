@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:4381/api/health',timeout=2)); assert d.get('service')=='research-swarm'" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:4381"
  exit /b 0
)
python -c "import pypdf" >nul 2>&1
if errorlevel 1 (
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. Please check the message above.
    pause
    exit /b 1
  )
)
if not exist "dist\index.html" (
  call npm.cmd ci
  if errorlevel 1 exit /b 1
  call npm.cmd run build
  if errorlevel 1 exit /b 1
)
start "" "http://127.0.0.1:4381"
python -X utf8 -m research_swarm --port 4381
pause
