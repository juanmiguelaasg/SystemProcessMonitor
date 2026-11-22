@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -c "import psutil, customtkinter" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    %PY% -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency installation failed. Make sure Python 3.9+ is installed.
        pause
        exit /b 1
    )
)

%PY% main.py %*
if errorlevel 1 pause
