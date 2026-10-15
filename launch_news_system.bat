@echo off

echo =================================================
echo 新闻爬虫系统启动程序
echo =================================================
echo.

cd /d "%~dp0"

echo 步骤 1: 启动新闻爬虫...
python run_crawler.py
echo.
echo 步骤 2: 启动HTTP服务器 (端口 54321)...

echo.
echo 服务器启动成功!
echo 访问地址: http://localhost:54321/news_dashboard.html
echo.
echo 按任意键在浏览器中打开页面...
pause

start http://localhost:54321/news_dashboard.html

echo.
echo 服务器正在运行中...
echo 关闭此窗口可停止服务器

:: 启动服务器（保持运行）
python start_server.py