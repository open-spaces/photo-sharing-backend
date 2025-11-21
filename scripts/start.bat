@echo off
REM Start the FastAPI backend with default DB_URL if not set
SETLOCAL
if "%DB_URL%"=="" (
  set DB_URL=sqlite:///./data/app.db
)
if "%SERVER_HOST%"=="" (
  set SERVER_HOST=0.0.0.0
)
if "%SERVER_PORT%"=="" (
  set SERVER_PORT=8000
)
echo Using DB_URL=%DB_URL%
python -c "from app.db.database import init_db; init_db()"
uvicorn main:app --host %SERVER_HOST% --port %SERVER_PORT%
ENDLOCAL

