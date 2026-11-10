@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "%~dp0KoikatsuModManager.exe" (
start "" "%~dp0KoikatsuModManager.exe"
exit /b
)
if not exist "%~dp0runtime\python-path.txt" goto missing
set /p "managerPython="<"%~dp0runtime\python-path.txt"
if not exist "%managerPython%\pythonw.exe" goto missing
"%managerPython%\python.exe" -B -c "import PIL,msgpack,UnityPy" >nul 2>nul
if errorlevel 1 goto missing
start "" "%managerPython%\pythonw.exe" -B "%~dp0BrowserLauncher.pyw"
exit /b
:missing
echo Please run Install-ModManager.bat first.
echo If installation fails, share the error shown in that window.
pause
