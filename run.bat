@echo off
title tDownUp Transfer Bot (Buzzheavier & Gofile)
echo ========================================================
echo     Starting tDownUp Bot (Buzzheavier & Gofile)   
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
    echo [ERROR] Node.js is required for real-time memory transfer.
    echo Please install Node.js from https://nodejs.org
    pause
    exit /b
)

:: Install Node dependencies if missing
if not exist node_modules (
    echo [*] Installing Node transfer packages...
    call npm install
)

:: Install Python dependencies
echo [*] Checking Python dependencies...
pip install -r requirements.txt

:: Start the bot
echo [*] Launching Bot...
python bot.py
pause
