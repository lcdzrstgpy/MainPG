@echo off
rem MainPG local dev services - double-click to start both (skips if already running)
set "PATH=C:\Users\15818\.workbuddy\binaries\PortableGit\versions\1.3.0\usr\bin;C:\Users\15818\.workbuddy\binaries\PortableGit\versions\1.3.0\bin;C:\Users\15818\.workbuddy\binaries\node\versions\22.22.2-6;%PATH%"

netstat -ano | findstr ":8010" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [1/2] backend  8010  already running
) else (
  start "mainpg-backend-8010" cmd /k "cd /d D:\xiangmu2\local-runtime && set PYTHONPATH= && D:\xiangmu2\local-runtime\python-runtime\python.exe -m uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010"
  echo [1/2] backend  8010  starting in a new window...
)

netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [2/2] frontend 5173  already running
) else (
  start "mainpg-frontend-5173" cmd /k "cd /d D:\xiangmu2\web-frontend && C:\Users\15818\.workbuddy\binaries\node\versions\22.22.2-6\npm.cmd run dev"
  echo [2/2] frontend 5173  starting in a new window...
)

echo.
echo Backend : http://127.0.0.1:8010
echo Frontend: http://127.0.0.1:5173
echo.
pause
