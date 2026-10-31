@echo off
title Build TorrentStreamBot to EXE
echo ========================================================
echo        Building TorrentStreamBot to Standalone EXE       
echo ========================================================

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b
)

:: Run build script
python build_exe.py

echo.
echo ========================================================
echo Build finished! The executable is located in the 'dist' folder.
echo ========================================================
pause
