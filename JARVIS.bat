@echo off
rem Lance JARVIS dans sa propre fenetre, sans console (journal : data\jarvis.log).
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw server.py --app) || (start "" pyw server.py --app)
