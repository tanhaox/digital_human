# 测试脚本

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from crawler.news_scraper import NewsScraper
from crawler.rss_fetcher import RSSFetcher
from crawler.news_filter import NewsFilter


def test_crawler():
    """
    测试爬虫功能
    """
    print("=== 开始测试新闻爬虫 ===")

    # 测试RSS抓取
    print("\n1. 测试RSS抓取...")
    rss_fetcher = RSSFetcher()

    # 测试单个RSS源
    test_rss_url = 'http://www.xinhuanet.com/english/rss/news.xml'
    news_list = rss_fetcher.fetch_rss(test_rss_url, max_items=5)

    if news_list:
        print(f"成功抓取 {len(news_list)} 条新闻")
        for i, news in enumerate(news_list[:2]):
            print(f"  - {news['title'][:50]}...")
    else:
        print("RSS抓取失败或无数据")

    # 测试主爬虫
    print("\n2. 测试主爬虫...")
    scraper = NewsScraper()
    all_news = scraper.fetch_news_from_all_sources()

    if all_news:
        print(f"成功抓取 {len(all_news)} 条新闻")

        # 测试过滤
        print("\n3. 测试新闻过滤...")
        filter_obj = NewsFilter()
        filtered_news = filter_obj.remove_duplicates(all_news)
        filtered_news = filter_obj.filter_by_content_length(filtered_news)

        print(f"过滤后剩余 {len(filtered_news)} 条新闻")

        # 保存测试数据
        scraper.save_news(filtered_news, 'test_news.json')
        print("\n4. 测试数据已保存到 test_news.json")
    else:
        print("无新闻数据可测试")

    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    test_crawler()