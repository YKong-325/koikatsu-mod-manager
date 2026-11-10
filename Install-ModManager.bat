@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto python_launcher
python -B "%~dp0Install.py"
goto done
:python_launcher
where py >nul 2>nul
if errorlevel 1 goto missing
py -3 -B "%~dp0Install.py"
goto done
:missing
echo Python was not found. Install Python 3.12 ^(64-bit^) and enable Add python.exe to PATH.
:done
pause
