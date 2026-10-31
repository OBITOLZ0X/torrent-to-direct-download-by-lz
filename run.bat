@echo off
title Torrent to Stream Bot (Buzzheavier & Gofile)
echo ========================================================
echo     Starting Torrent Stream Bot (Buzzheavier & Gofile)   
echo ========================================================

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ and check 'Add Python to PATH'.
    pause
    exit /b
)

:: Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is required for real-time memory streaming.
    echo Please install Node.js from https://nodejs.org
    pause
    exit /b
)

:: Install Node dependencies if missing
if not exist node_modules (
    echo [*] Installing Node streaming packages...
    call npm install
)

:: Install Python dependencies
echo [*] Checking Python dependencies...
pip install -r requirements.txt

:: Start the bot
echo [*] Launching Bot...
python bot.py
pause
