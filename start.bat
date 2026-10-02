@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "FRONTEND_URL=http://localhost:5174"
set "BACKEND_URL=http://127.0.0.1:8031"
set "MODE=%~1"

if "%MODE%"=="" set "MODE=local"

if /i "%MODE%"=="local" goto local
if /i "%MODE%"=="dev" goto local
if /i "%MODE%"=="backend" goto backend
if /i "%MODE%"=="worker" goto backend
if /i "%MODE%"=="wedding-worker" goto backend
if /i "%MODE%"=="frontend" goto frontend
if /i "%MODE%"=="docker" goto docker
if /i "%MODE%"=="stop" goto stop
if /i "%MODE%"=="stop-local" goto stop_local
if /i "%MODE%"=="help" goto usage
if /i "%MODE%"=="-h" goto usage
if /i "%MODE%"=="--help" goto usage
if /i "%MODE%"=="/?" goto usage

echo Unknown option: %MODE%
echo.
goto usage

:local
title AiWardrobe Local Launcher
echo.
echo AiWardrobe one-click start
echo Mode: local API/wardrobe-worker/wedding-worker/frontend
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.11 or newer.
    pause
    exit /b 1
)
python --version

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found. Install Node.js first.
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js with npm.
    pause
    exit /b 1
)
node --version
cmd /c npm --version
if errorlevel 1 (
    echo [ERROR] npm failed to run.
    pause
    exit /b 1
)

echo.
echo Preparing backend dependencies...
pushd "%BACKEND_DIR%"
where uv >nul 2>&1
if errorlevel 1 goto local_backend_pip

uv --version
if not exist ".venv\Scripts\python.exe" (
    uv venv .venv --python python
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
)
uv pip install --link-mode=copy --python ".venv\Scripts\python.exe" -e ".[dev]"
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
goto local_backend_ready

:local_backend_pip
echo uv was not found; falling back to python venv and pip.
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 (
    popd
    pause
    exit /b 1
)

:local_backend_ready
popd

echo.
echo Preparing frontend dependencies...
pushd "%FRONTEND_DIR%"
cmd /c npm install
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
popd

pushd "%BACKEND_DIR%"
".venv\Scripts\python.exe" -m app.local_launcher local
set "LOCAL_CODE=%ERRORLEVEL%"
popd
if not "%LOCAL_CODE%"=="0" (
    echo [ERROR] Local services failed to start. See .local-services logs.
    pause
    exit /b %LOCAL_CODE%
)

timeout /t 5 /nobreak >nul
start "" "%FRONTEND_URL%"

echo.
echo Frontend: %FRONTEND_URL%
echo Backend:  %BACKEND_URL%/docs
echo.
echo API, wardrobe worker, wedding worker and frontend are independent hidden processes.
echo Logs and process IDs are in .local-services.
echo.
pause
exit /b 0

:backend
title AiWardrobe Backend Launcher
echo.
echo Starting AiWardrobe backend...

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.11 or newer.
    pause
    exit /b 1
)
python --version

echo.
echo Preparing backend dependencies...
pushd "%BACKEND_DIR%"
where uv >nul 2>&1
if errorlevel 1 goto backend_pip

uv --version
if not exist ".venv\Scripts\python.exe" (
    uv venv .venv --python python
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
)
uv pip install --link-mode=copy --python ".venv\Scripts\python.exe" -e ".[dev]"
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
goto backend_ready

:backend_pip
echo uv was not found; falling back to python venv and pip.
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 (
    popd
    pause
    exit /b 1
)

:backend_ready
popd

pushd "%BACKEND_DIR%"
if /i "%MODE%"=="wedding-worker" (
    ".venv\Scripts\python.exe" -m app.local_launcher wedding-worker
) else if /i "%MODE%"=="worker" (
    ".venv\Scripts\python.exe" -m app.local_launcher worker
) else (
    ".venv\Scripts\python.exe" -m app.local_launcher backend
)
set "SERVICE_CODE=%ERRORLEVEL%"
popd
if not "%SERVICE_CODE%"=="0" (
    echo [ERROR] Local service failed to start. See .local-services logs.
    pause
    exit /b %SERVICE_CODE%
)

echo Backend: %BACKEND_URL%/docs
echo.
pause
exit /b 0

:frontend
title AiWardrobe Frontend Launcher
echo.
echo Starting AiWardrobe frontend...

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found. Install Node.js first.
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js with npm.
    pause
    exit /b 1
)
node --version
cmd /c npm --version
if errorlevel 1 (
    echo [ERROR] npm failed to run.
    pause
    exit /b 1
)

echo.
echo Preparing frontend dependencies...
pushd "%FRONTEND_DIR%"
cmd /c npm install
if errorlevel 1 (
    popd
    pause
    exit /b 1
)
popd

set "VITE_BACKEND_TARGET=%BACKEND_URL%"
echo Frontend: %FRONTEND_URL%
echo Press Ctrl+C in this console to stop the frontend.
start "" "%FRONTEND_URL%"
pushd "%FRONTEND_DIR%"
cmd /c npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
set "FRONTEND_CODE=%ERRORLEVEL%"
popd
exit /b %FRONTEND_CODE%

:docker
title AiWardrobe Docker Launcher
echo.
echo AiWardrobe one-click start
echo Mode: Docker Compose full stack
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker was not found. Install Docker Desktop first.
    pause
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Engine is not running. Start Docker Desktop first.
    pause
    exit /b 1
)

pushd "%ROOT%"
docker compose up --build -d
set "DOCKER_CODE=%ERRORLEVEL%"
popd
if not "%DOCKER_CODE%"=="0" (
    echo [ERROR] Docker Compose failed.
    pause
    exit /b %DOCKER_CODE%
)

timeout /t 5 /nobreak >nul
start "" "http://localhost:5173"
echo Frontend: http://localhost:5173
echo Backend:  http://localhost:8000/docs
echo.
pause
exit /b 0

:stop_local
if not exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    echo [ERROR] Backend virtual environment was not found.
    exit /b 1
)
pushd "%BACKEND_DIR%"
".venv\Scripts\python.exe" -m app.local_launcher stop-local
set "LOCAL_STOP_CODE=%ERRORLEVEL%"
popd
exit /b %LOCAL_STOP_CODE%

:stop
echo.
echo Stopping Docker services...
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker was not found.
    pause
    exit /b 1
)
pushd "%ROOT%"
docker compose down
set "STOP_CODE=%ERRORLEVEL%"
popd
exit /b %STOP_CODE%

:usage
echo AiWardrobe startup script
echo.
echo Usage:
echo   start.bat             Start the local API, wardrobe worker, wedding worker and frontend
echo   start.bat local       Start the local API, wardrobe worker, wedding worker and frontend
echo   start.bat dev         Alias for local
echo   start.bat backend     Start only the local FastAPI backend
echo   start.bat worker      Start only the independent task worker
echo   start.bat wedding-worker  Start only the independent wedding worker
echo   start.bat frontend    Start only the local Vite frontend
echo   start.bat docker      Start the full stack with Docker Compose
echo   start.bat stop        Stop Docker Compose services
echo   start.bat stop-local  Stop verified local service processes
echo   start.bat help        Show this help
echo.
exit /b 0
