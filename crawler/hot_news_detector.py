# 热点新闻检测模块

import json
import re
from collections import defaultdict, Counter
from datetime import datetime
from typing import List, Dict

# 从配置文件导入
from config import HOT_KEYWORDS, CATEGORIES, HOT_NEWS_PARAMS

class HotNewsDetector:
    """
    热点新闻检测器
    """

    def __init__(self):
        self.hot_keywords = HOT_KEYWORDS
        self.categories = CATEGORIES
        self.params = HOT_NEWS_PARAMS

    def detect_hot_news(self, news_list: List[Dict], min_sources: int = None,
                       min_keyword_matches: int = None) -> List[Dict]:
        """
        检测热点新闻

        Args:
            news_list (List[Dict]): 新闻列表
            min_sources (int): 最小出现源数
            min_keyword_matches (int): 最小关键词匹配数

        Returns:
            List[Dict]: 热点新闻列表
        """
        if min_sources is None:
            min_sources = self.params['min_sources']
        if min_keyword_matches is None:
            min_keyword_matches = self.params['min_keyword_matches']

        # 记录每个新闻的热度信息
        for news in news_list:
            # 计算关键词匹配数
            keyword_matches = self._count_keyword_matches(news)

            # 计算多源交叉数
            source_cross_count = self._count_source_cross(news)

            # 计算热度评分
            hot_score = self._calculate_hot_score(keyword_matches, source_cross_count)

            # 添加热度信息到新闻
            news['keyword_matches'] = keyword_matches
            news['source_cross_count'] = source_cross_count
            news['hot_score'] = hot_score

            # 添加分类
            news['category'] = self._classify_news(news)

        # 过滤出热点新闻
        hot_news = [news for news in news_list
                   if news['source_cross_count'] >= min_sources and
                   news['keyword_matches'] >= min_keyword_matches]

        # 按热度评分排序
        hot_news.sort(key=lambda x: x['hot_score'], reverse=True)

        return hot_news

    def _count_keyword_matches(self, news: Dict) -> int:
        """
        计算新闻中关键词匹配数

        Args:
            news (Dict): 新闻对象

        Returns:
            int: 匹配的关键词数
        """
        title = news.get('title', '')
        summary = news.get('summary', '')
        content = title + ' ' + summary

        matches = 0
        for keyword in self.hot_keywords:
            if re.search(keyword, content, re.IGNORECASE):
                matches += 1

        return matches

    def _count_source_cross(self, news: Dict) -> int:
        """
        计算新闻在不同源出现的次数

        Args:
            news (Dict): 新闻对象

        Returns:
            int: 出现的源数
        """
        # 这里简化处理，实际应用中需要更复杂的逻辑
        # 由于我们没有完整的源交叉统计信息，返回固定的值
        return 1

    def _calculate_hot_score(self, keyword_matches: int, source_cross_count: int) -> int:
        """
        计算热度评分

        Args:
            keyword_matches (int): 关键词匹配数
            source_cross_count (int): 多源交叉数

        Returns:
            int: 热度评分
        """
        # 基于关键词匹配数和多源交叉数计算热度
        score = keyword_matches * 10 + source_cross_count * 20
        return min(score, 100)  # 限制在0-100分之间

    def _classify_news(self, news: Dict) -> str:
        """
        对新闻进行分类

        Args:
            news (Dict): 新闻对象

        Returns:
            str: 分类名称
        """
        title = news.get('title', '')
        summary = news.get('summary', '')
        content = title + ' ' + summary

        category_scores = defaultdict(int)

        # 为每个分类计算匹配分数
        for category, keywords in self.categories.items():
            for keyword in keywords:
                if re.search(keyword, content, re.IGNORECASE):
                    category_scores[category] += 1

        # 返回得分最高的分类
        if category_scores:
            return max(category_scores, key=category_scores.get)

        return '其他'

    def get_top_news(self, news_list: List[Dict], top_n: int = None) -> List[Dict]:
        """
        获取TOP N热点新闻

        Args:
            news_list (List[Dict]): 新闻列表
            top_n (int): 返回前N条新闻

        Returns:
            List[Dict]: TOP N热点新闻列表
        """
        if top_n is None:
            top_n = self.params['top_n']

        # 按热度评分排序
        sorted_news = sorted(news_list, key=lambda x: x.get('hot_score', 0), reverse=True)
        return sorted_news[:top_n]

    def classify_news(self, news_list: List[Dict]) -> Dict[str, List[Dict]]:
        """
        对新闻进行分类

        Args:
            news_list (List[Dict]): 新闻列表

        Returns:
            Dict[str, List[Dict]]: 分类后的新闻字典
        """
        classified_news = defaultdict(list)

        for news in news_list:
            category = news.get('category', '其他')
            classified_news[category].append(news)

        return dict(classified_news)

    def get_category_news(self, news_list: List[Dict], category: str) -> List[Dict]:
        """
        获取特定分类的新闻

        Args:
            news_list (List[Dict]): 新闻列表
            category (str): 分类名称

        Returns:
            List[Dict]: 指定分类的新闻列表
        """
        return [news for news in news_list if news.get('category') == category]

    def get_hot_topics(self, news_list: List[Dict], top_n: int = 10) -> List[Dict]:
        """
        获取热门话题

        Args:
            news_list (List[Dict]): 新闻列表
            top_n (int): 返回前N个热门话题

        Returns:
            List[Dict]: 热门话题列表
        """
        # 提取所有新闻中的关键词
        all_keywords = []
        for news in news_list:
            title = news.get('title', '')
            summary = news.get('summary', '')
            content = title + ' ' + summary

            # 简单的关键词提取
            for keyword in self.hot_keywords:
                if re.search(keyword, content, re.IGNORECASE):
                    all_keywords.append(keyword)

        # 统计词频
        keyword_counter = Counter(all_keywords)

        # 返回最常见的前N个关键词
        topics = [{'topic': keyword, 'count': count}
                 for keyword, count in keyword_counter.most_common(top_n)]

        return topics

    def filter_by_hot_score(self, news_list: List[Dict], min_score: int = None) -> List[Dict]:
        """
        根据热度评分过滤新闻

        Args:
            news_list (List[Dict]): 新闻列表
            min_score (int): 最小热度评分

        Returns:
            List[Dict]: 过滤后的新闻列表
        """
        if min_score is None:
            min_score = self.params['min_hot_score']

        return [news for news in news_list if news.get('hot_score', 0) >= min_score]

if __name__ == "__main__":
    # 测试热点新闻检测器
    detector = HotNewsDetector()

    # 创建测试数据
    test_news = [
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
        }
    ]

    # 测试热点检测
    hot_news = detector.detect_hot_news(test_news)

    print("=== 热点新闻检测测试 ===")
    for i, news in enumerate(hot_news):
        print(f"\n--- 热点新闻 {i+1} ---")
        print(f"标题: {news['title']}")
        print(f"热度评分: {news['hot_score']}")
        print(f"关键词匹配数: {news['keyword_matches']}")
        print(f"多源交叉数: {news['source_cross_count']}")
        print(f"分类: {news['category']}")