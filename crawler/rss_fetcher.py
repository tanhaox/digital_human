# RSS源抓取模块

import feedparser
import logging
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

class RSSFetcher:
    """
    RSS源抓取类
    """

    def fetch_rss(self, url: str, max_items: int = 10) -> List[Dict]:
        """
        抓取RSS源

        Args:
            url (str): RSS源URL
            max_items (int): 最大抓取条数

        Returns:
            List[Dict]: 新闻列表
        """
        try:
            feed = feedparser.parse(url)
            news_list = []

            # 检查feed是否有效
            if not feed:
                logger.warning(f"Feed is empty for {url}")
                return []

            # 检查entries是否有效
            if not hasattr(feed, 'entries') or not feed.entries:
                logger.warning(f"No entries found in feed for {url}")
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

            logger.info(f"成功从 {url} 抓取 {len(news_list)} 条新闻")
            return news_list

        except Exception as e:
            logger.error(f"RSS抓取失败 {url}: {e}")
            return []

    def fetch_multiple_rss(self, urls: List[str], max_items: int = 10) -> List[Dict]:
        """
        抓取多个RSS源

        Args:
            urls (List[str]): RSS源URL列表
            max_items (int): 每个源的最大抓取条数

        Returns:
            List[Dict]: 所有新闻列表
        """
        all_news = []
        for url in urls:
            news_list = self.fetch_rss(url, max_items)
            all_news.extend(news_list)

        logger.info(f"总共从 {len(urls)} 个RSS源抓取 {len(all_news)} 条新闻")
        return all_news