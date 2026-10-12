@echo off
setlocal
cd /d "%~dp0"

rem ===== 一键启动新版 WebUI（前端 :8502 + API :8080）=====

echo ***** Starting API server (port 8080) ... *****
start "MPT API" cmd /c ".venv\Scripts\python.exe main.py"

echo ***** Starting new WebUI dev server (port 8502) ... *****
start "MPT NewUI" cmd /c "cd frontend && npm run dev"

rem 等服务就绪后自动打开浏览器
timeout /t 6 >nul
start http://127.0.0.1:8502

echo ***** New WebUI: http://127.0.0.1:8502 | API docs: http://127.0.0.1:8080/docs *****
