@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title PharmaSense AI - Setup and Run

REM ============================================================================
REM  PharmaSense AI - one-click setup + launcher (Windows)
REM  Usage:  double-click, or run from a terminal:
REM     setup_and_run.bat            -> interactive menu
REM     setup_and_run.bat ui         -> setup (if needed) then start Chat UI
REM     setup_and_run.bat api        -> start REST API
REM     setup_and_run.bat both       -> API (new window) + UI
REM     setup_and_run.bat eval       -> run evaluation on the golden set
REM     setup_and_run.bat test       -> run offline unit tests
REM     setup_and_run.bat docker     -> build and run with Docker
REM     setup_and_run.bat reinstall  -> force re-install of dependencies
REM ============================================================================

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "MODE=%~1"

echo.
echo  ===========================================
echo   PharmaSense AI  -  setup and launcher
echo  ===========================================
echo.

if /i "%MODE%"=="docker" goto :docker_only

REM ---------- 1. find a usable Python (3.9+) ----------
set "BASEPY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "BASEPY=py -3"
if not defined BASEPY (
    python --version >nul 2>nul
    if not errorlevel 1 set "BASEPY=python"
)
if not defined BASEPY goto :nopython
%BASEPY% -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
if errorlevel 1 goto :oldpython
echo [1/5] Python found: 
%BASEPY% --version

REM ---------- 2. virtual environment ----------
if /i "%MODE%"=="reinstall" if exist "%VENV%\.deps_ok" del /q "%VENV%\.deps_ok"
if exist "%PY%" goto :venv_ready
echo [2/5] Creating virtual environment in %VENV% ...
%BASEPY% -m venv %VENV%
if errorlevel 1 goto :fail
:venv_ready
echo [2/5] Virtual environment ready.

REM ---------- 3. dependencies ----------
if exist "%VENV%\.deps_ok" goto :deps_ready
echo [3/5] Installing dependencies (first run takes a few minutes) ...
"%PY%" -m pip install --upgrade pip >nul 2>nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo ok> "%VENV%\.deps_ok"
:deps_ready
echo [3/5] Dependencies ready.

REM ---------- 4. .env with the Groq key ----------
if not exist ".env" copy /y ".env.example" ".env" >nul
set "HASKEY="
findstr /b /c:"GROQ_API_KEY=gsk_" ".env" >nul 2>nul
if not errorlevel 1 set "HASKEY=1"
if defined HASKEY goto :env_ready
echo.
echo [4/5] A FREE Groq API key is needed.  Get one in 1 minute (no credit card):
echo        https://console.groq.com/keys
echo.
set "GKEY="
set /p "GKEY=      Paste your key here (starts with gsk_) or press Enter to skip: "
if not defined GKEY goto :env_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='.env'; $t=Get-Content -Raw $p; $t=[regex]::Replace($t,'(?m)^GROQ_API_KEY=.*$','GROQ_API_KEY=%GKEY%'); Set-Content -Path $p -Value $t -NoNewline -Encoding ASCII"
echo        Key saved to .env
:env_ready
echo [4/5] Configuration ready (.env).

REM ---------- 5. Streamlit first-run prompt off + health check ----------
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit" >nul 2>nul
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    >"%USERPROFILE%\.streamlit\credentials.toml" echo [general]
    >>"%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)
echo [5/5] Running health check ...
"%PY%" scripts\check_setup.py

if /i "%MODE%"=="ui" goto :ui
if /i "%MODE%"=="api" goto :api
if /i "%MODE%"=="both" goto :both
if /i "%MODE%"=="eval" goto :eval
if /i "%MODE%"=="test" goto :test
if /i "%MODE%"=="reinstall" goto :menu

:menu
echo.
echo  ------------------- MENU -------------------
echo   [1] Start Chat UI            http://localhost:8501
echo   [2] Start REST API           http://localhost:8000/docs
echo   [3] Start BOTH (API in a new window + UI)
echo   [4] Run evaluation (golden set, uses your Groq key)
echo   [5] Run offline unit tests (no API key needed)
echo   [6] Build and run with Docker (deployable container)
echo   [7] Exit
echo  --------------------------------------------
set "CH="
set /p "CH=Choose 1-7: "
if "%CH%"=="1" goto :ui
if "%CH%"=="2" goto :api
if "%CH%"=="3" goto :both
if "%CH%"=="4" goto :eval
if "%CH%"=="5" goto :test
if "%CH%"=="6" goto :docker_only
if "%CH%"=="7" goto :end
goto :menu

:ui
echo.
echo Starting the Chat UI on http://localhost:8501  (Ctrl+C to stop)
"%PY%" -m streamlit run app.py --server.port 8501
goto :end

:api
echo.
echo Starting the REST API on http://localhost:8000/docs  (Ctrl+C to stop)
"%PY%" -m uvicorn api:app --host 0.0.0.0 --port 8000
goto :end

:both
echo.
start "PharmaSense API" cmd /k ""%PY%" -m uvicorn api:app --host 0.0.0.0 --port 8000"
echo API started in a new window: http://localhost:8000/docs
echo Starting the Chat UI on http://localhost:8501  (Ctrl+C to stop)
"%PY%" -m streamlit run app.py --server.port 8501
goto :end

:eval
echo.
echo Running evaluation. Groq free tier has rate limits, so a quick 10-question run is used.
echo (For the full 30-question set run:  %PY% scripts\run_eval.py --judge)
"%PY%" scripts\run_eval.py --limit 10
echo.
echo Report saved to eval\report.md
pause
goto :menu

:test
echo.
"%PY%" -m pytest -q tests
pause
goto :menu

:docker_only
where docker >nul 2>nul
if errorlevel 1 (
    echo Docker is not installed or not on PATH. Install Docker Desktop: https://www.docker.com/products/docker-desktop
    pause
    goto :end
)
if not exist ".env" copy /y ".env.example" ".env" >nul
findstr /b /c:"GROQ_API_KEY=gsk_" ".env" >nul 2>nul
if errorlevel 1 (
    echo Please put your Groq key in the .env file first - the line GROQ_API_KEY=gsk_xxx - then run this again.
    notepad ".env"
    pause
    goto :end
)
echo Building and starting containers (UI: http://localhost:8501 , API: http://localhost:8000/docs) ...
docker compose up --build
goto :end

:nopython
echo [ERROR] Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" in the installer, then run this file again.
pause
goto :end

:oldpython
echo [ERROR] Python 3.9 or newer is required. Please upgrade: https://www.python.org/downloads/
pause
goto :end

:fail
echo.
echo [ERROR] Setup failed - see the messages above.
pause
exit /b 1

:end
endlocal
