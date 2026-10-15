# 新闻内容过滤和去重模块

import hashlib
import logging
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

class NewsFilter:
    """
    新闻内容过滤和去重类
    """

    def __init__(self):
        self.logger = logger

    def remove_duplicates(self, news_list: List[Dict]) -> List[Dict]:
        """
        去除重复新闻

        Args:
            news_list (List[Dict]): 新闻列表

        Returns:
            List[Dict]: 去重后的新闻列表
        """
        seen = set()
        unique_news = []

        for news in news_list:
            # 使用标题和链接的组合创建唯一标识
            title = news.get('title', '')
            link = news.get('link', '')
            identifier = f"{title}_{link}"

            # 使用md5生成哈希值 (去重指纹, 非密码学用途)
            hash_object = hashlib.md5(identifier.encode(), usedforsecurity=False)  # noqa: S324
            hash_value = hash_object.hexdigest()

            if hash_value not in seen:
                seen.add(hash_value)
                unique_news.append(news)

        self.logger.info(f"去重后剩余 {len(unique_news)} 条新闻")
        return unique_news

    def filter_by_content_length(self, news_list: List[Dict], min_length: int = 50) -> List[Dict]:
        """
        按内容长度过滤新闻

        Args:
            news_list (List[Dict]): 新闻列表
            min_length (int): 最小内容长度

        Returns:
            List[Dict]: 过滤后的新闻列表
        """
        filtered_news = []
        for news in news_list:
            content = news.get('summary', '')
            if len(content) >= min_length:
                filtered_news.append(news)

        self.logger.info(f"按长度过滤后剩余 {len(filtered_news)} 条新闻")
        return filtered_news

    def filter_by_date(self, news_list: List[Dict], days_back: int = 7) -> List[Dict]:
        """
        按日期过滤新闻

        Args:
            news_list (List[Dict]): 新闻列表
            days_back (int): 天数限制

        Returns:
            List[Dict]: 过滤后的新闻列表
        """
        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(days=days_back)
        filtered_news = []

        for news in news_list:
            try:
                # 解析发布日期
                pub_date = news.get('published', '')
                if pub_date:
                    # 简单解析日期，实际使用时可能需要更复杂的解析
                    # 这里只做基础处理
                    if '2026' in pub_date or '2025' in pub_date:
                        filtered_news.append(news)
                else:
                    # 如果没有日期，保留
                    filtered_news.append(news)
            except Exception:
                # 如果日期解析失败，保留
                filtered_news.append(news)

        self.logger.info(f"按日期过滤后剩余 {len(filtered_news)} 条新闻")
        return filtered_news

    def clean_news_content(self, news_list: List[Dict]) -> List[Dict]:
        """
        清理新闻内容

        Args:
            news_list (List[Dict]): 新闻列表

        Returns:
            List[Dict]: 清理后的新闻列表
        """
        cleaned_news = []
        for news in news_list:
            cleaned_news.append({
                'title': news.get('title', '').strip(),
                'summary': news.get('summary', '').strip(),
                'link': news.get('link', '').strip(),
                'published': news.get('published', '').strip(),
                'source': news.get('source', ''),
                'source_name': news.get('source_name', ''),
                'source_display': news.get('source_display', ''),
                'scraped_at': news.get('scraped_at', ''),
            })

        self.logger.info(f"清理内容后共 {len(cleaned_news)} 条新闻")
        return cleaned_news