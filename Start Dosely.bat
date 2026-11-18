@echo off
title Dosely server
cd /d "%~dp0"

echo.
echo  Starting Dosely...
echo.

rem --- find Python ("python" first, then the Windows "py" launcher) ---
set "PY=python"
%PY% --version >nul 2>&1 && goto havepython
set "PY=py"
%PY% --version >nul 2>&1 && goto havepython

echo  Python was not found on this computer.
echo  Install it from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during setup, then double-click this file again.
echo.
pause
exit /b 1

:havepython
rem --- first run only: install what Dosely needs (Flask + cryptography) ---
%PY% -c "import flask, cryptography" >nul 2>&1 && goto haveflask
echo  Installing what Dosely needs (first time only, needs internet)...
echo.
%PY% -m pip install -r requirements.txt
echo.
%PY% -c "import flask, cryptography" >nul 2>&1 && goto haveflask
echo  The installation did not work. Please check your internet connection and try again.
echo.
pause
exit /b 1

:haveflask
rem --- open the launcher page; it waits for the server and then opens Dosely by itself ---
start "" "%~dp0Dosely.html"

echo  Dosely is running. Your browser will open it in a moment.
echo  Keep this window open while you use Dosely. Close it to stop Dosely.
echo.
%PY% app.py
echo.
echo  Dosely has stopped.
pause
