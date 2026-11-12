@echo off
rem Start ReaderHelper so it can also be opened on your phone (same Wi-Fi).
rem Double-click this file; close the window to stop the server.
cd /d "%~dp0"
echo.
echo  ReaderHelper is starting...
echo.
echo    On this PC:     http://localhost:8000
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress"`) do echo    On your phone:  http://%%i:8000
echo.
echo  Phone and PC must be on the same Wi-Fi. Close this window to stop the server.
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
