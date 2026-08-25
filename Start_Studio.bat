@echo off
REM VisoSwap Studio launcher. One port: uvicorn serves the API, the websocket
REM and the built frontend, so the browser only ever talks to 127.0.0.1:8000.
REM Pass --build to force a frontend rebuild after changing anything under
REM frontend\src; the build runs automatically when frontend\dist is missing.

setlocal
cd /d "%~dp0"

if "%BACKEND_PORT%"=="" set BACKEND_PORT=8000

set PYTHON_EXECUTABLE=%~dp0.venv-clean\Scripts\python.exe
if not exist "%PYTHON_EXECUTABLE%" (
    echo [!] No interpreter at %PYTHON_EXECUTABLE%
    echo     Create it with: py -3.10 -m venv .venv-clean ^&^& .venv-clean\Scripts\pip install -r requirements.txt
    goto :halt
)

if /I "%~1"=="--build" goto :build
if not exist "frontend\dist\index.html" goto :build
goto :serve

:build
echo [*] Building the frontend...
if not exist "frontend\node_modules" (
    echo [*] Installing frontend dependencies ^(first run^)...
    call npm --prefix frontend install || goto :halt
)
call npm --prefix frontend run build || goto :halt

:serve
echo [*] Starting VisoSwap Studio on http://127.0.0.1:%BACKEND_PORT%
start "" http://127.0.0.1:%BACKEND_PORT%/
"%PYTHON_EXECUTABLE%" -m uvicorn backend.main:app --host 127.0.0.1 --port %BACKEND_PORT%

:halt
echo.
pause
endlocal
