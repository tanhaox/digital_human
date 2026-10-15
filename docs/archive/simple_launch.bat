@echo off

cd /d "%~dp0"

echo 启动新闻爬虫...
python run_crawler.py

echo.
echo 启动HTTP服务器 (端口 54321)...
python start_server.py