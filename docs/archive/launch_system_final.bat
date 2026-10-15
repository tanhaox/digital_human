@echo off

echo =================================================
echo 数字人计划 - 新闻爬虫系统
=================================================

cd /d "%~dp0"

echo 步骤 1: 启动新闻爬虫...
python run_crawler_fixed.py

echo.
echo 步骤 2: 启动HTTP服务器 (端口 54321)...

REM 启动服务器（保持运行）
start python start_server_final.py

echo.
echo 服务器正在运行中...
echo 关闭此窗口可停止服务器

echo.
echo 按任意键在浏览器中打开页面...
pause

start http://localhost:54321/news_dashboard.html