"""HF 视觉渲染模板库 (single source of truth for available templates).

Currently ships ONE template (``news-data-v1``) — vertically sliced for future
extension (``comparison-chart-v1`` / ``title-card-v1`` / ``quote-card-v1`` /
``timeline-v1`` etc.) without changing the orchestrator contract.
"""
from __future__ import annotations

from pathlib import Path


TEMPLATES: dict[str, dict] = {
    "news-magazine-v1": {
        "version": "1.0.0",
        "composition_id": "news_main",
        "source_dir": "news_magazine_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 30],
        "required_input": ["title", "metrics"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "subtitle": {"type": "string", "maxLength": 128},
                "metrics": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "emphasis": {"type": "boolean"},
                        },
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "chart": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["pie", "bar", "none"]},
                        "unit": {"type": "string"},
                        "growth": {"type": "string"},
                        "color_scheme": {"type": "string"},
                        "label": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "value": {"type": "number"},
                                },
                                "required": ["label", "value"],
                            },
                        },
                    },
                },
                "caption": {"type": "string", "maxLength": 256},
                "source": {"type": "string", "maxLength": 128},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 30},
                "assets": {"type": "object"},
            },
            "required": ["title", "metrics"],
            "additionalProperties": True,
        },
    },
    "news-magazine-v1-ls": {
        "version": "1.0.0",
        "composition_id": "news_main_ls",
        "source_dir": "news_magazine_v1_ls",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 30],
        "required_input": ["title", "metrics"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "subtitle": {"type": "string", "maxLength": 128},
                "metrics": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "emphasis": {"type": "boolean"},
                        },
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "chart": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["pie", "bar", "none"]},
                        "unit": {"type": "string"},
                        "growth": {"type": "string"},
                        "color_scheme": {"type": "string"},
                        "label": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "value": {"type": "number"},
                                },
                                "required": ["label", "value"],
                            },
                        },
                    },
                },
                "caption": {"type": "string", "maxLength": 256},
                "source": {"type": "string", "maxLength": 128},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 30},
                "assets": {"type": "object"},
            },
            "required": ["title", "metrics"],
            "additionalProperties": True,
        },
    },
    "news-data-v1": {
        "version": "1.0.0",
        "composition_id": "news_main",
        "source_dir": "test_demo",
        "index_html": "index.html",
        "avatar_asset": "avatar.b64",
        "duration_sec_range": [5, 30],
        "required_input": ["title", "metrics"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "subtitle": {"type": "string", "maxLength": 128},
                "metrics": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "emphasis": {"type": "boolean"},
                        },
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "chart": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "unit": {"type": "string"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "value": {"type": "number"},
                                },
                                "required": ["label", "value"],
                            },
                        },
                    },
                },
                "caption": {"type": "string", "maxLength": 256},
                "source": {"type": "string", "maxLength": 128},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 30},
                "assets": {"type": "object"},
            },
            "required": ["title", "metrics"],
            "additionalProperties": True,
        },
    },
    "hf-opening-v1": {
        "version": "1.0.0",
        "composition_id": "hf_opening_main",
        "source_dir": "hf_opening_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 8],
        "required_input": ["opening_lines_json"],
        "json_schema": {
            "type": "object",
            "properties": {
                "opening_lines_json": {"type": "string"},
                "opening_red_words": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 8},
            },
            "required": ["opening_lines_json"],
            "additionalProperties": True,
        },
    },
    "hf-opening-v1-ls": {
        "version": "1.0.0",
        "composition_id": "hf_opening_main_ls",
        "source_dir": "hf_opening_v1_ls",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 8],
        "required_input": ["opening_lines_json"],
        "json_schema": {
            "type": "object",
            "properties": {
                "opening_lines_json": {"type": "string"},
                "opening_red_words": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 8},
            },
            "required": ["opening_lines_json"],
            "additionalProperties": True,
        },
    },
    "hf-opening-v2": {
        "version": "1.0.0",
        "composition_id": "hf_opening_v2_main",
        "source_dir": "hf_opening_v2",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 8],
        "required_input": ["opening_lines_json"],
        "json_schema": {
            "type": "object",
            "properties": {
                "opening_lines_json": {"type": "string"},
                "opening_accent_words": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 8},
            },
            "required": ["opening_lines_json"],
            "additionalProperties": True,
        },
    },
    "hf-opening-v2-ls": {
        "version": "1.0.0",
        "composition_id": "hf_opening_v2_main_ls",
        "source_dir": "hf_opening_v2_ls",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 8],
        "required_input": ["opening_lines_json"],
        "json_schema": {
            "type": "object",
            "properties": {
                "opening_lines_json": {"type": "string"},
                "opening_accent_words": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 8},
            },
            "required": ["opening_lines_json"],
            "additionalProperties": True,
        },
    },
    "hf-opening-v3": {
        "version": "1.0.0",
        "composition_id": "hf_opening_v3",
        "source_dir": "hf_opening_v3",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [5, 8],
        "required_input": ["hero_text"],
        "json_schema": {
            "type": "object",
            "properties": {
                "hero_text": {"type": "string", "minLength": 1},
                "hot_word": {"type": "string"},
                "sub_text": {"type": "string"},
                "scatter_words": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 5, "maximum": 8},
            },
            "required": ["hero_text"],
            "additionalProperties": True,
        },
    },
    "hf-quote-v1": {
        "version": "1.0.0",
        "composition_id": "hf_quote_v1",
        "source_dir": "hf_quote_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["quote_text"],
        "json_schema": {
            "type": "object",
            "properties": {
                "quote_text": {"type": "string", "minLength": 1, "maxLength": 200},
                "hot_word": {"type": "string", "maxLength": 20},
                "attrib_name": {"type": "string", "maxLength": 40},
                "attrib_role": {"type": "string", "maxLength": 80},
                "portrait_b64": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["quote_text"],
            "additionalProperties": True,
        },
    },
    "hf-title-v2": {
        "version": "1.0.0",
        "composition_id": "hf_title_v2",
        "source_dir": "hf_title_v2",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [3, 10],
        "required_input": ["title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "kicker": {"type": "string", "maxLength": 32},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 3, "maximum": 10},
            },
            "required": ["title"],
            "additionalProperties": True,
        },
    },
    "hf-chart-v2": {
        "version": "1.0.0",
        "composition_id": "hf_chart_v2",
        "source_dir": "hf_chart_v2",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 12],
        "required_input": ["chart"],
        "json_schema": {
            "type": "object",
            "properties": {
                "chart": {"type": "object"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 12},
            },
            "required": ["chart"],
            "additionalProperties": True,
        },
    },
    "hf-source-v1": {
        # 片尾来源声明卡 (2026-08-18): 财经体系 (深炭+暖金, 同 title_v2/chart_v2/opening_v3).
        # 原尾卡复用 hf-title-v2 但 subtitle→kicker 受 maxLength 32 校验炸掉,
        # 降级 hf_chart 渲染近黑屏 → 专用模板承载结构化来源列表.
        "version": "1.0.0",
        "composition_id": "hf_source_v1",
        "source_dir": "hf_source_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "sources": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "media": {"type": "string", "maxLength": 40},
                            "title": {"type": "string", "maxLength": 60},
                        },
                        "additionalProperties": False,
                    },
                },
                "disclaimer": {"type": "string", "maxLength": 80},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["title"],
            "additionalProperties": True,
        },
    },
    # ── v3 编辑纸墨系 (2026-09-04): 替代黑金 v2 系, 版式源 .tmp/style_gallery_h.html ──
    "hf-title-v3": {
        "version": "1.0.0",
        "composition_id": "hf_title_v3",
        "source_dir": "hf_title_v3",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [3, 10],
        "required_input": ["title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "kicker": {"type": "string", "maxLength": 32},
                "brand_name": {"type": "string", "maxLength": 64},
                "brand_tag": {"type": "string", "maxLength": 64},
                "issue_date": {"type": "string", "maxLength": 7,
                               "description": "刊号日期 YYYY.MM, 缺省填充端取本机当月 (渲染沙箱 Date 冻结为 epoch)"},
                "duration_sec": {"type": "integer", "minimum": 3, "maximum": 10},
            },
            "required": ["title"],
            "additionalProperties": True,
        },
    },
    "hf-chart-v3": {
        # 四布局合一: pie→环形 / items=1→巨数 / =2→对比 / 3~5→柱群 (榜单并入)
        "version": "1.0.0",
        "composition_id": "hf_chart_v3",
        "source_dir": "hf_chart_v3",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 12],
        "required_input": ["chart"],
        "json_schema": {
            "type": "object",
            "properties": {
                "chart": {"type": "object"},
                "brand_name": {"type": "string", "maxLength": 64},
                "brand_tag": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 12},
            },
            "required": ["chart"],
            "additionalProperties": True,
        },
    },
    "hf-quote-v2": {
        # quote_body = 保留标点原串 (金句按标点断行); quote_text 兼容保留
        "version": "2.0.0",
        "composition_id": "hf_quote_v2",
        "source_dir": "hf_quote_v2",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["quote_text"],
        "json_schema": {
            "type": "object",
            "properties": {
                "quote_text": {"type": "string", "minLength": 1, "maxLength": 200},
                "quote_body": {"type": "string", "maxLength": 200},
                "hot_word": {"type": "string", "maxLength": 20},
                "attrib_name": {"type": "string", "maxLength": 40},
                "attrib_role": {"type": "string", "maxLength": 80},
                "portrait_b64": {"type": "string"},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["quote_text"],
            "additionalProperties": True,
        },
    },
    "hf-source-v2": {
        # 容量 5 → 8 (v1 maxItems 5)
        "version": "2.0.0",
        "composition_id": "hf_source_v2",
        "source_dir": "hf_source_v2",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "sources": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "media": {"type": "string", "maxLength": 40},
                            "title": {"type": "string", "maxLength": 60},
                        },
                        "additionalProperties": False,
                    },
                },
                "disclaimer": {"type": "string", "maxLength": 80},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["title"],
            "additionalProperties": True,
        },
    },
    # ── 身份卡/互动卡 (2026-09-05): 版式源杂志风预览页面 06/07, 财经线(tech)专用 ──
    "hf-identity-v1": {
        "version": "1.0.0",
        "composition_id": "hf_identity_v1",
        "source_dir": "hf_identity_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["identity_text"],
        "json_schema": {
            "type": "object",
            "properties": {
                "identity_text": {"type": "string", "minLength": 1, "maxLength": 200},
                "identity_body": {"type": "string", "maxLength": 200},
                "hot_word": {"type": "string", "maxLength": 20},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["identity_text"],
            "additionalProperties": True,
        },
    },
    "hf-follow-v1": {
        # 全品牌层固定件, slogan 缺省由执行层注入账号口号 → required_input 空
        "version": "1.0.0",
        "composition_id": "hf_follow_v1",
        "source_dir": "hf_follow_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [3, 10],
        "required_input": [],
        "json_schema": {
            "type": "object",
            "properties": {
                "slogan": {"type": "string", "minLength": 1, "maxLength": 40},
                "follow_word": {"type": "string", "maxLength": 24},
                "brand_name": {"type": "string", "maxLength": 64},
                "duration_sec": {"type": "integer", "minimum": 3, "maximum": 10},
            },
            "required": [],
            "additionalProperties": True,
        },
    },
    # ── 拆书线尾卡双卡 (2026-09-05, 版式源 .tmp/style_book_card.html 拍板) ──
    # ── 老谭读书 bs1 族 (2026-09-08, 深空蓝金·13页型; 静读书线不共用) ──
    "bs1-cover-v1": {
        "version": "1.0.0", "composition_id": "bs1_cover_v1", "source_dir": "bs1_cover_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 10], "required_input": ["title"],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "ep_tag": {"type": "string"},
            "book": {"type": "string"}, "brand_tag": {"type": "string"},
            "logo_b64": {"type": "string"}, "img": {"type": "string"},
            "duration_sec": {"type": "number"}}},
    },
    "bs1-contents-v1": {
        "version": "1.0.0", "composition_id": "bs1_contents_v1", "source_dir": "bs1_contents_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-chapter-v1": {
        "version": "1.0.0", "composition_id": "bs1_chapter_v1", "source_dir": "bs1_chapter_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-list-v1": {
        "version": "1.0.0", "composition_id": "bs1_list_v1", "source_dir": "bs1_list_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-passage-v1": {
        "version": "1.0.0", "composition_id": "bs1_passage_v1", "source_dir": "bs1_passage_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-compare-v1": {
        "version": "1.0.0", "composition_id": "bs1_compare_v1", "source_dir": "bs1_compare_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-case-v1": {
        "version": "1.0.0", "composition_id": "bs1_case_v1", "source_dir": "bs1_case_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-quote-v1": {
        "version": "1.0.0", "composition_id": "bs1_quote_v1", "source_dir": "bs1_quote_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['quote'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-data-v1": {
        "version": "1.0.0", "composition_id": "bs1_data_v1", "source_dir": "bs1_data_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-chart-v1": {
        "version": "1.0.0", "composition_id": "bs1_chart_v1", "source_dir": "bs1_chart_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-outro-v1": {
        "version": "1.0.0", "composition_id": "bs1_outro_v1", "source_dir": "bs1_outro_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [3, 40], "required_input": ['quote'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-bookquote-v1": {
        "version": "1.0.0", "composition_id": "bs1_bookquote_v1", "source_dir": "bs1_bookquote_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [4, 8], "required_input": ['book_title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "bs1-bookinfo-v1": {
        "version": "1.0.0", "composition_id": "bs1_bookinfo_v1", "source_dir": "bs1_bookinfo_v1",
        "index_html": "index.html", "avatar_asset": None,
        "duration_sec_range": [4, 10], "required_input": ['title'],
        "json_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "duration_sec": {"type": "number"}}},
    },
    "hf-bookinfo-v1": {
        # B-08 书籍信息卡: 一书一档, author/guide/bio 由 ppt_pipeline 从
        # book_projects (author/author_bio) + input_json 全书核心主张 注入
        "version": "1.0.0",
        "composition_id": "hf_bookinfo_v1",
        "source_dir": "hf_bookinfo_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 10],
        "required_input": ["title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 64},
                "author": {"type": "string", "maxLength": 128},
                "guide": {"type": "string", "maxLength": 200},
                "bio": {"type": "string", "maxLength": 600},
                "brand_name": {"type": "string", "maxLength": 64},
                "logo_b64": {"type": "string", "maxLength": 2000000},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 10},
            },
            "required": ["title"],
            "additionalProperties": True,
        },
    },
    "hf-bookquote-v1": {
        # B-Q1 书摘金句卡: 每集 3 句 (Episode.quotes_json, 全书 6 集不重复,
        # 句长 ≥12 字), kN = 句内锈红关键短语 (须为 qN 子串)
        "version": "1.0.0",
        "composition_id": "hf_bookquote_v1",
        "source_dir": "hf_bookquote_v1",
        "index_html": "index.html",
        "avatar_asset": None,
        "duration_sec_range": [4, 8],
        "required_input": ["book_title"],
        "json_schema": {
            "type": "object",
            "properties": {
                "book_title": {"type": "string", "minLength": 1, "maxLength": 96},
                "q1": {"type": "string", "maxLength": 120},
                "q2": {"type": "string", "maxLength": 120},
                "q3": {"type": "string", "maxLength": 120},
                "k1": {"type": "string", "maxLength": 60},
                "k2": {"type": "string", "maxLength": 60},
                "k3": {"type": "string", "maxLength": 60},
                "brand_name": {"type": "string", "maxLength": 64},
                "logo_b64": {"type": "string", "maxLength": 2000000},
                "duration_sec": {"type": "integer", "minimum": 4, "maximum": 8},
            },
            "required": ["book_title"],
            "additionalProperties": True,
        },
    },
}


def list_templates() -> list[dict]:
    """Return a list of all available template metadata (sans json_schema for brevity)."""
    out: list[dict] = []
    for tid, meta in TEMPLATES.items():
        out.append(
            {
                "template_id": tid,
                "version": meta["version"],
                "composition_id": meta["composition_id"],
                "duration_sec_range": meta["duration_sec_range"],
                "required_input": meta["required_input"],
            }
        )
    return out


def get_template(template_id: str) -> dict | None:
    """Return full template spec (including json_schema) or None if missing."""
    return TEMPLATES.get(template_id)


def resolve_template_dir(template_id: str, hf_template_root: str) -> Path:
    """Resolve the on-disk template directory for a given template id.

    Raises:
        FileNotFoundError: If the template id is unknown or its source dir is missing.
    """
    meta = TEMPLATES.get(template_id)
    if meta is None:
        raise FileNotFoundError(f"Unknown template_id: {template_id}")
    root = Path(hf_template_root)
    src = root / meta["source_dir"]
    if not src.is_dir():
        raise FileNotFoundError(f"Template source dir missing: {src}")
    return src


def validate_input(template_id: str, input_data: dict) -> None:
    """Run jsonschema validation. Raises ``jsonschema.ValidationError`` on failure.

    Imported lazily so the module loads even if jsonschema is not yet installed.
    """
    import jsonschema

    meta = TEMPLATES[template_id]
    jsonschema.validate(instance=input_data, schema=meta["json_schema"])