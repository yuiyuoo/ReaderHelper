@echo off
rem Start ReaderHelper so it can also be opened on your phone.
rem Double-click this file; close the window to stop the server.
cd /d "%~dp0"
echo.
echo  ReaderHelper is starting...
echo.
echo    On this PC:          http://localhost:8000
rem Wi-Fi address(es); 100.x addresses belong to Tailscale and are shown below instead.
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '100.*' }).IPAddress"`) do echo    Phone, same Wi-Fi:   http://%%i:8000
rem Away from home: the https address Tailscale serves (only if Tailscale is installed and "tailscale serve" was set up).
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$t = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'; if (Test-Path $t) { try { $s = & $t status --json | ConvertFrom-Json; if ($s.Self.DNSName) { 'https://' + $s.Self.DNSName.TrimEnd('.') } } catch {} }"`) do echo    Phone, anywhere:     %%i   (Tailscale on in the phone)
echo.
echo  Keep this window open and the PC awake. Close the window to stop the server.
echo.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
