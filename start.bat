@echo off
rem Start Rub rub on http://localhost:8000  (needs Python 3.9+, nothing else)
cd /d "%~dp0"
python server.py %*
pause
