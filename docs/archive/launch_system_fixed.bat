@echo off

echo =================================================
echo Digital Human News Crawler System
echo =================================================

cd /d "%~dp0"

echo Step 1: Starting news crawler...
python run_crawler_fixed.py

echo.
echo Step 2: Starting HTTP server (port 54321)...

REM Start server (keep running)
start "News Server" python start_server_final.py

echo.
echo Server is running...
echo Close this window to stop the server

echo.
echo Press any key to open the page in browser...
pause

start http://localhost:54321/news_dashboard.html
