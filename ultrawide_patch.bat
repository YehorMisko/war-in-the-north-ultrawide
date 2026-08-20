@echo off
rem Launcher for ultrawide_patch.ps1 - keep both files together in the game
rem folder, next to witn.exe, and just double-click this one.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ultrawide_patch.ps1"
echo.
pause
