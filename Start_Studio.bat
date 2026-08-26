@echo off
REM VisoSwap Studio launcher. One port: uvicorn serves the API, the websocket
REM and the built frontend, so the browser only ever talks to one origin.
REM Pass --build to force a frontend rebuild after changing anything under
REM frontend\src; the build runs automatically when frontend\dist is missing.
REM
REM BIND_HOST decides who can reach the studio. 0.0.0.0 answers on every
REM interface, which is what puts it on the LAN. The app has no login of any
REM kind, so on 0.0.0.0 anybody who can route to this machine can open the
REM projects, upload faces, spend this GPU and delete takes. Bind it wide only
REM on a network you trust; set BIND_HOST=127.0.0.1 for this machine only.

setlocal
cd /d "%~dp0"

if "%BACKEND_PORT%"=="" set BACKEND_PORT=8000
if "%BIND_HOST%"=="" set BIND_HOST=0.0.0.0

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
if not "%BIND_HOST%"=="127.0.0.1" (
    echo [*] Also reachable from this LAN ^(no login -- trusted networks only^):
    for /f "delims=" %%A in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } ^| Select-Object -Expand IPAddress) -join ' '"') do (
        for %%B in (%%A) do echo       http://%%B:%BACKEND_PORT%/
    )
)
REM The browser on this machine still opens the loopback address: it is the
REM one URL that is right no matter which interface the LAN address is on.
start "" http://127.0.0.1:%BACKEND_PORT%/
"%PYTHON_EXECUTABLE%" -m uvicorn backend.main:app --host %BIND_HOST% --port %BACKEND_PORT%

:halt
echo.
pause
endlocal
