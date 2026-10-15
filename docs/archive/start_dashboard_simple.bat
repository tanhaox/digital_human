@echo off
cd /d "%~dp0"

echo 正在启动新闻爬虫...
python run_crawler.py
echo.
echo 爬虫已完成，正在启动服务器...
echo.

:: 启动服务器（后台运行）
start "News Server" python start_server.py

echo.
echo 服务器已启动，正在打开页面...
echo 访问地址: http://localhost:54321/news_dashboard.html
echo.
start http://localhost:54321/news_dashboard.html

echo.
echo 服务器正在运行中，按任意键可停止...
pause