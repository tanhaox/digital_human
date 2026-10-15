# 新闻爬虫主程序

import feedparser
import requests
import json
import logging
from datetime import datetime
from typing import List, Dict
import sys
import os

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 将当前目录添加到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 从当前目录导入模块
from config import NEWS_SOURCES
from hot_news_detector import HotNewsDetector

class NewsScraper:
    """
    新闻爬虫主类
    """

    def __init__(self):
        self.sources = NEWS_SOURCES
        self.logger = logger
        self.hot_detector = HotNewsDetector()

    def fetch_rss_news(self, rss_url: str, max_items: int = 10) -> List[Dict]:
        """
        抓取RSS新闻

        Args:
            rss_url (str): RSS源URL
            max_items (int): 最大抓取条数

        Returns:
            List[Dict]: 新闻列表
        """
        try:
            feed = feedparser.parse(rss_url)
            news_list = []

            # 检查feed是否有效
            if not feed:
                self.logger.warning(f"Feed is empty for {rss_url}")
                return []

            # 检查entries是否有效
            if not hasattr(feed, 'entries') or not feed.entries:
                self.logger.warning(f"No entries found in feed for {rss_url}")
                return []

            for entry in feed.entries[:max_items]:
                news_item = {
                    'title': entry.get('title', ''),
                    'summary': entry.get('summary', ''),
                    'link': entry.get('link', ''),
                    'published': entry.get('published', ''),
                    'source': 'rss',
                    'scraped_at': datetime.now().isoformat()
                }
                news_list.append(news_item)

            self.logger.info(f"成功从 {rss_url} 抓取 {len(news_list)} 条新闻")
            return news_list

        except Exception as e:
            self.logger.error(f"RSS抓取失败 {rss_url}: {e}")
            return []

    def fetch_news_from_all_sources(self) -> List[Dict]:
        """
        从所有配置的源抓取新闻

        Returns:
            List[Dict]: 所有新闻列表
        """
        all_news = []

        for source_name, source_config in self.sources.items():
            rss_url = source_config.get('rss')
            if rss_url:
                try:
                    news_list = self.fetch_rss_news(rss_url)
                    for item in news_list:
                        item['source_name'] = source_name
                        item['source_display'] = source_config.get('name', source_name)
                    all_news.extend(news_list)
                except Exception as e:
                    self.logger.error(f"从 {source_name} 抓取新闻失败: {e}")
                    continue

        self.logger.info(f"总共抓取 {len(all_news)} 条新闻")
        return all_news

    def save_news(self, news_list: List[Dict], filename: str):
        """
        保存新闻到JSON文件

        Args:
            news_list (List[Dict]): 新闻列表
            filename (str): 保存文件名
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(news_list, f, ensure_ascii=False, indent=2)
            self.logger.info(f"新闻已保存到 {filename}")
        except Exception as e:
            self.logger.error(f"保存新闻失败: {e}")

    def detect_hot_news(self, news_list: List[Dict], min_sources: int = 2,
                       min_keyword_matches: int = 1) -> List[Dict]:
        """
        检测热点新闻

        Args:
            news_list (List[Dict]): 新闻列表
            min_sources (int): 最小出现源数
            min_keyword_matches (int): 最小关键词匹配数

        Returns:
            List[Dict]: 热点新闻列表
        """
        return self.hot_detector.detect_hot_news(news_list, min_sources, min_keyword_matches)

    def get_top_news(self, news_list: List[Dict], top_n: int = 10) -> List[Dict]:
        """
        获取TOP N热点新闻

        Args:
            news_list (List[Dict]): 新闻列表
            top_n (int): 返回前N条新闻

        Returns:
            List[Dict]: TOP N热点新闻列表
        """
        return self.hot_detector.get_top_news(news_list, top_n)

    def classify_news(self, news_list: List[Dict]) -> Dict[str, List[Dict]]:
        """
        对新闻进行分类

        Args:
            news_list (List[Dict]): 新闻列表

        Returns:
            Dict[str, List[Dict]]: 分类后的新闻字典
        """
        return self.hot_detector.classify_news(news_list)

    def get_category_news(self, news_list: List[Dict], category: str) -> List[Dict]:
        """
        获取特定分类的新闻

        Args:
            news_list (List[Dict]): 新闻列表
            category (str): 分类名称

        Returns:
            List[Dict]: 指定分类的新闻列表
        """
        return self.hot_detector.get_category_news(news_list, category)

    def get_hot_topics(self, news_list: List[Dict], top_n: int = 10) -> List[Dict]:
        """
        获取热门话题

        Args:
            news_list (List[Dict]): 新闻列表
            top_n (int): 返回前N个热门话题

        Returns:
            List[Dict]: 热门话题列表
        """
        return self.hot_detector.get_hot_topics(news_list, top_n)

    def filter_news_by_hot_score(self, news_list: List[Dict], min_score: int = 30) -> List[Dict]:
        """
        根据热度评分过滤新闻

        Args:
            news_list (List[Dict]): 新闻列表
            min_score (int): 最小热度评分

        Returns:
            List[Dict]: 过滤后的新闻列表
        """
        return self.hot_detector.filter_by_hot_score(news_list, min_score)

    def filter_news(self, news_list: List[Dict], min_length: int = 50) -> List[Dict]:
        """
        过滤新闻内容

        Args:
            news_list (List[Dict]): 新闻列表
            min_length (int): 最小内容长度

        Returns:
            List[Dict]: 过滤后的新闻列表
        """
        filtered_news = []
        for news in news_list:
            if len(news.get('summary', '')) >= min_length:
                filtered_news.append(news)

        self.logger.info(f"过滤后剩余 {len(filtered_news)} 条新闻")
        return filtered_news

    def create_sample_data(self) -> List[Dict]:
        """
        创建示例数据，用于测试
        """
        sample_news = [
            {
                'title': '中国与美国在贸易问题上达成新的协议',
                'summary': '中美两国在贸易谈判中取得了重要进展，双方就关税问题达成了新的共识。',
                'link': 'http://example.com/news1',
                'published': '2026-07-21',
                'source': 'rss',
                'source_name': 'xinhua',
                'source_display': '新华社',
                'scraped_at': datetime.now().isoformat()
            },
            {
                'title': '美国股市大幅上涨，科技股表现强劲',
                'summary': '纽约股市今日大幅上涨，主要得益于科技股的强劲表现。',
                'link': 'http://example.com/news2',
                'published': '2026-07-21',
                'source': 'rss',
                'source_name': 'bbc',
                'source_display': 'BBC新闻',
                'scraped_at': datetime.now().isoformat()
            },
            {
                'title': '人工智能技术取得重大突破',
                'summary': '最新的人工智能技术在多个领域实现了突破性进展。',
                'link': 'http://example.com/news3',
                'published': '2026-07-21',
                'source': 'rss',
                'source_name': 'sina',
                'source_display': '新浪新闻',
                'scraped_at': datetime.now().isoformat()
            },
            {
                'title': '中国与欧盟举行高层会谈',
                'summary': '中欧双方在重要议题上进行了深入交流，就多项合作达成共识。',
                'link': 'http://example.com/news4',
                'published': '2026-07-21',
                'source': 'rss',
                'source_name': 'xinhua',
                'source_display': '新华社',
                'scraped_at': datetime.now().isoformat()
            },
            {
                'title': '国际油价上涨，市场担忧供应问题',
                'summary': '由于中东局势紧张，国际油价出现明显上涨。',
                'link': 'http://example.com/news5',
                'published': '2026-07-21',
                'source': 'rss',
                'source_name': 'reuters',
                'source_display': '路透社',
                'scraped_at': datetime.now().isoformat()
            }
        ]
        return sample_news

if __name__ == "__main__":
    # 测试爬虫
    scraper = NewsScraper()
    news = scraper.fetch_news_from_all_sources()

    # 如果没有获取到数据，使用示例数据
    if not news:
        print("没有获取到真实新闻数据，使用示例数据")
        news = scraper.create_sample_data()

    # 保存到文件
    scraper.save_news(news, 'test_news.json')

    # 显示前几条
    for i, item in enumerate(news[:3]):
        print(f"\n--- 新闻 {i+1} ---")
        print(f"标题: {item['title']}")
        print(f"来源: {item['source_display']}")
        print(f"摘要: {item['summary'][:100]}...")
        print(f"链接: {item['link']}")

    # 测试热点检测
    print("\n=== 热点新闻检测测试 ===")
    hot_news = scraper.detect_hot_news(news)
    if hot_news:
        print(f"检测到 {len(hot_news)} 条热点新闻")
        for i, item in enumerate(hot_news[:3]):
            print(f"\n--- 热点新闻 {i+1} ---")
            print(f"标题: {item['title'][:100]}...")
            print(f"热度评分: {item['hot_score']}")
            print(f"关键词匹配数: {item['keyword_matches']}")
            print(f"多源交叉数: {item['source_cross_count']}")
    else:
        print("未检测到热点新闻")