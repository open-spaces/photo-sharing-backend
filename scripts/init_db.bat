@echo off
REM Initialize the database (create tables) for the backend
SETLOCAL
set PYTHONPATH=%~dp0..;%PYTHONPATH%
python "%~dp0setup_db.py"
ENDLOCAL

