@echo off
rem Start the whole ArnaAI stack locally on Windows, each service in its own window.
rem Usage: start.cmd   (from the project root, or double-click)
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist ".env" (
  echo .env not found. Copy .env.example to .env and fill it in first.
  exit /b 1
)

rem Prefer the project virtualenv; fall back to python on PATH.
set "PY=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%ROOT%frontend\node_modules" (
  echo Installing frontend dependencies...
  pushd "%ROOT%frontend" && call npm install && popd
)

start "ArnaAI emotion :8081" /d "%ROOT%services\emotion" cmd /k "set PORT=8081&& "%PY%" app.py"
start "ArnaAI therapy :8080" /d "%ROOT%services\therapy" cmd /k "set PORT=8080&& "%PY%" app.py"
start "ArnaAI frontend :3000" /d "%ROOT%frontend" cmd /k "set THERAPY_API_URL=http://127.0.0.1:8080&& npm run dev"

echo Started emotion (8081), therapy (8080) and frontend (3000) in separate windows.
echo Open http://localhost:3000  -  close a window to stop that service.
endlocal
