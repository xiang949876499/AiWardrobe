@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title AiWardrobe 启动器

echo.
echo   ╔══════════════════════════════════╗
echo   ║     AiWardrobe — AI 个人衣橱     ║
echo   ╚══════════════════════════════════╝
echo.
echo   选择启动方式:
echo     [1] 本地开发 (前端 + 后端)
echo     [2] Docker Compose (全服务)
echo     [3] 仅后端
echo     [4] 仅前端
echo     [0] 退出
echo.

choice /c 12340 /n /m "请输入选项: "

if errorlevel 5 goto :exit
if errorlevel 4 goto :frontend
if errorlevel 3 goto :backend
if errorlevel 2 goto :docker
if errorlevel 1 goto :dev

:dev
echo.
echo  === 本地开发模式 ===
call :check_deps
call :start_backend
call :start_frontend
call :open_browser
echo.
echo  后端: http://localhost:8000/docs
echo  前端: http://localhost:5173
echo.
echo  按 Ctrl+C 停止所有服务...
pause >nul
goto :exit

:docker
echo.
echo  === Docker Compose 模式 ===
docker compose up --build
goto :exit

:backend
echo.
echo  === 启动后端 ===
call :check_python
start "AiWardrobe Backend" cmd /c "cd /d "%~dp0backend" && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
echo  后端已在新窗口启动 → http://localhost:8000/docs
echo.
pause
goto :exit

:frontend
echo.
echo  === 启动前端 ===
call :check_node
start "AiWardrobe Frontend" cmd /c "cd /d "%~dp0frontend" && npm run dev"
echo  前端已在新窗口启动 → http://localhost:5173
echo.
pause
goto :exit

:exit
endlocal
exit /b 0

:: ===== 辅助函数 =====

:check_deps
call :check_python
call :check_node
echo.
exit /b

:check_python
where python >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未找到 Python，请先安装 Python ^>=3.11
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do echo  Python %%v ✓
exit /b

:check_node
where node >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未找到 Node.js，请先安装 Node.js
    pause
    exit /b 1
)
for /f "tokens=1,2 delims=v." %%a in ('node --version 2^>^&1') do echo  Node.js v%%b ✓
exit /b

:start_backend
echo  启动后端...
cd /d "%~dp0backend"
if not exist ".venv\" (
    echo  创建虚拟环境...
    python -m venv .venv
)
call .venv\Scripts\activate.bat 2>nul
python -m pip install -e .[dev] -q 2>nul
start "AiWardrobe Backend" cmd /c "cd /d "%~dp0backend" && .venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
exit /b

:start_frontend
echo  启动前端...
cd /d "%~dp0frontend"
if not exist "node_modules\" (
    echo  安装依赖...
    call npm install
)
start "AiWardrobe Frontend" cmd /c "cd /d "%~dp0frontend" && npm run dev"
exit /b

:open_browser
timeout /t 3 /nobreak >nul
start http://localhost:5173 2>nul
exit /b
