@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Push PharmaSense AI to GitHub

REM ============================================================================
REM  One-click GitHub publisher.
REM  Fully automatic: repo, name, e-mail and overwrite are pre-filled for
REM  https://github.com/Toto-explorer/pharmasense-ai  - just double-click.
REM ============================================================================

if exist "app.py" goto :folder_ok
echo [ERROR] app.py not found here. Put push_to_github.bat INSIDE the project folder ^(next to app.py^) and run it again.
pause
exit /b 1
:folder_ok
echo.
echo  ===========================================
echo   PharmaSense AI  -  push code to GitHub
echo  ===========================================
echo.

REM ---------- 1. Git installed? ----------
git --version >nul 2>nul
if not errorlevel 1 goto :git_ok
echo [!] Git is not installed.
where winget >nul 2>nul
if errorlevel 1 goto :git_manual
set "ANS="
set /p "ANS=    Install Git automatically now with winget? (y/n): "
if /i not "%ANS%"=="y" goto :git_manual
winget install --id Git.Git -e --source winget
echo.
echo     Git installed. Please CLOSE this window and run push_to_github.bat again.
pause
exit /b 0
:git_manual
echo     Download and install Git from https://git-scm.com/download/win
echo     (accept the default options), then run this file again.
pause
exit /b 1
:git_ok
echo [1/7] Git found.

REM ---------- 2. Commit identity (pre-filled, used only for this commit) ----------
set "GNAME=Toto-explorer"
set "GMAIL=155751670+Toto-explorer@users.noreply.github.com"
echo [2/7] Identity: %GNAME%

REM ---------- 3. Git repository in this folder ----------
if exist ".git" goto :repo_ok
git init >nul
if errorlevel 1 goto :fail
echo [3/7] Created a new local Git repository.
goto :branch
:repo_ok
echo [3/7] Existing Git repository found.
:branch
git branch -M main >nul 2>nul

REM ---------- 4. Secret safety: .env must never be uploaded ----------
if not exist ".gitignore" (
    echo .env> ".gitignore"
    echo .venv/>> ".gitignore"
    echo __pycache__/>> ".gitignore"
)
git rm --cached -q .env >nul 2>nul
if exist ".env" (
    git check-ignore -q .env
    if errorlevel 1 (
        echo [ERROR] .env exists but is NOT ignored by Git. Add a line  .env  to .gitignore and run again.
        goto :fail
    )
)
git add . >nul
git grep --cached -n -E "gsk_[A-Za-z0-9]{20,}" >nul 2>nul
if not errorlevel 1 (
    echo [ERROR] A real Groq key ^(gsk_...^) was found inside a file that would be uploaded:
    git grep --cached -n -E "gsk_[A-Za-z0-9]{20,}"
    echo         Remove the key from that file, then run this again. Nothing was pushed.
    git reset -q
    goto :fail
)
echo [4/7] Safety check passed: .env and API keys are not included.

REM ---------- 5. Commit ----------
git diff --cached --quiet
if errorlevel 1 (
    git -c user.name="%GNAME%" -c user.email="%GMAIL%" commit -q -m "PharmaSense AI update %DATE% %TIME%"
    if errorlevel 1 goto :fail
    echo [5/7] Changes committed.
) else (
    echo [5/7] Nothing new to commit.
)

REM ---------- 6. GitHub address ----------
set "URL=https://github.com/Toto-explorer/pharmasense-ai.git"
echo [6/7] GitHub repo: %URL%
git remote get-url origin >nul 2>nul
if errorlevel 1 (
    git remote add origin "%URL%"
) else (
    git remote set-url origin "%URL%"
)

REM ---------- 7. Push ----------
echo [7/7] Uploading to %URL%
echo       A browser window may open the first time - log in to GitHub and click Authorize.
echo.
git push -u origin main
if not errorlevel 1 goto :done
echo.
echo [i] GitHub already has an auto-created README. Replacing it with your project automatically ...
git push -u origin main --force
if errorlevel 1 goto :fail

:done
echo.
echo  ===========================================
echo   DONE - your code is on GitHub:
echo   %URL%
echo  ===========================================
echo.
echo  NEXT: deploy on Streamlit Community Cloud
echo    1. Open https://share.streamlit.io and log in with GitHub
echo    2. Create app - pick this repo, branch main, main file  app.py
echo    3. Advanced settings - Python 3.11 - Secrets box, paste:
echo         GROQ_API_KEY = "gsk_your_real_key"
echo    4. Click Deploy
echo.
pause
exit /b 0

:fail
echo.
echo [ERROR] Stopped. Read the message above, fix it and run this file again.
pause
exit /b 1
