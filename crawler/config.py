# Crawler configuration file

# News sources configuration - verified working RSS feeds (2026-07-21)
NEWS_SOURCES = {
    '36kr': {
        'rss': 'https://www.36kr.com/feed',
        'name': '36氪',
        'description': 'Chinese business and technology news'
    },
    'solidot': {
        'rss': 'https://www.solidot.org/index.rss',
        'name': 'Solidot',
        'description': 'Chinese science and technology news'
    },
    'sspai': {
        'rss': 'https://sspai.com/feed',
        'name': '少数派',
        'description': 'Chinese technology lifestyle news'
    },
    'cnbc': {
        'rss': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
        'name': 'CNBC',
        'description': 'International finance and business news'
    },
    'techcrunch': {
        'rss': 'https://techcrunch.com/feed/',
        'name': 'TechCrunch',
        'description': 'International technology startup news'
    },
    'engadget': {
        'rss': 'https://www.engadget.com/rss.xml',
        'name': 'Engadget',
        'description': 'International consumer technology news'
    }
}

# Request configuration
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# Save configuration
SAVE_PATH = '../data/raw/'

# Scraping configuration
SCRAPING_CONFIG = {
    'max_items_per_source': 10,
    'timeout': 12,
    'retry_times': 2,
    'delay_between_requests': 0.5  # seconds
}

# Filter configuration
FILTER_CONFIG = {
    'min_content_length': 30,
    'max_content_length': 10000,
    'keep_days': 7
}

# Hot news detection configuration
HOT_KEYWORDS = [
    'China', 'US', 'trade', 'economy', 'technology', 'stock', 'AI', 'artificial intelligence',
    'policy', 'development', 'market', 'finance', 'company', ' billion', 'million'
]

# Categories use Chinese keys to match the frontend buttons
CATEGORIES = {
    '政治': ['government', 'policy', 'leader', 'meeting', 'bill', 'election', 'politics'],
    '经济': ['economy', 'market', 'finance', 'investment', 'stock', 'trade', 'business', '融资', '上市', '财报', '经济'],
    '科技': ['technology', 'AI', 'artificial intelligence', 'internet', 'software', 'chip', 'programming', 'tech', '科技', '人工智能', '芯片'],
    '社会': ['society', 'people', 'education', 'medical', 'culture', 'entertainment', 'life', '社会', '教育', '医疗'],
    '国际': ['international', 'diplomacy', 'UN', 'US', 'EU', 'Japan', 'Russia', 'global', 'world']
}

HOT_NEWS_PARAMS = {
    'min_sources': 1,
    'min_keyword_matches': 1,
    'min_hot_score': 30,
    'top_n': 20
}
