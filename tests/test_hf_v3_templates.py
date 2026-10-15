# -*- coding: utf-8 -*-
"""HF v3 编辑纸墨系回归 (2026-09-04: 黑金 v2 → 编辑纸墨 v3, 4 模板 + 数据规则闸门).

覆盖:
1. 图卡数据规则路由 (_normalize_chart_input): pie<3 或 >5 分段 → bar; 3~5 保持 pie
2. template_filler: quote_body 保留标点 + HTML 转义 (quote_text 仍净标点); sources_json 容量 8
3. 模板注册表: v3 四条目 source_dir / duration range / source_v2 maxItems 8
4. hf.py / 同步脚本引用的模板 id 与注册表一致
5. _collect_news_sources: 容量 8 (原 5)
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services.director_service._postprocess import _collect_news_sources
from app.services.slot_workflows.hf_chart import _normalize_chart_input
from app.services.template_filler import _build_substitutions
from app.services.template_library import TEMPLATES

REPO_ROOT = Path(__file__).resolve().parents[1]


def _items(n: int, prefix: str = "项") -> list[dict]:
    return [{"label": f"{prefix}{i}", "value": 10.0 + i} for i in range(1, n + 1)]


class TestChartRoutingRules:
    """图卡数据规则 (用户裁决 2026-09-04): 单点→巨数 / 恰2点→对比 / pie 3~5 段存活."""

    def test_pie_with_2_items_downgrades_to_bar(self):
        # 恰 2 点 → 对比卡布局 (bar+2 items), 不做双段环形
        rc = {"chart_type": "pie_chart", "data": _items(2)}
        chart = _normalize_chart_input(rc, {})
        assert chart["type"] == "bar"
        assert len(chart["items"]) == 2

    def test_pie_with_1_item_downgrades_to_bar(self):
        rc = {"chart_type": "pie_chart", "data": _items(1)}
        chart = _normalize_chart_input(rc, {})
        assert chart["type"] == "bar"
        assert len(chart["items"]) == 1  # 单点 → 模板巨数卡

    def test_pie_with_6_items_downgrades_to_bar(self):
        # >5 分段不可读 → bar (>5 超量已被 [:5] 截断, 但保险起见)
        rc = {"chart_type": "pie_chart", "data": _items(6)}
        chart = _normalize_chart_input(rc, {})
        assert chart["type"] == "bar"

    def test_pie_with_3_to_5_items_survives(self):
        for n in (3, 4, 5):
            rc = {"chart_type": "pie_chart", "data": _items(n)}
            chart = _normalize_chart_input(rc, {})
            assert chart["type"] == "pie", f"{n} 分段应保持饼图"
            assert len(chart["items"]) == n

    def test_bar_passthrough_single_item(self):
        rc = {"chart_type": "bar_chart", "data": _items(1)}
        chart = _normalize_chart_input(rc, {})
        assert chart["type"] == "bar"
        assert len(chart["items"]) == 1

    def test_no_more_auto_other_segment(self):
        # 旧逻辑 pie 单扇区补"其他"已删 — 不应出现补出来的段
        rc = {"chart_type": "pie_chart", "growth": "38%", "data": _items(1)}
        chart = _normalize_chart_input(rc, {})
        labels = [it["label"] for it in chart["items"]]
        assert "其他" not in labels

    def test_items_capped_at_5(self):
        rc = {"chart_type": "bar_chart", "data": _items(8)}
        chart = _normalize_chart_input(rc, {})
        assert len(chart["items"]) == 5


class TestFillerQuoteBodyAndSources:
    def test_quote_body_keeps_punctuation(self):
        subs = _build_substitutions({
            "quote_body": "黄金永不生锈、永不退色，有始有终。",
            "duration_sec": 6,
        })
        # 保留 ，。、 (断行靠标点); < > 转义防注入
        assert "，" in subs["quote_body"]
        assert "、" in subs["quote_body"]
        assert "。" in subs["quote_body"]
        assert "<" not in subs["quote_body"]

    def test_quote_body_escapes_html(self):
        subs = _build_substitutions({"quote_body": "a<b>&\"c\"", "duration_sec": 6})
        assert subs["quote_body"] == "a&lt;b&gt;&amp;&quot;c&#39;".replace("&#39;", '"') or "&lt;" in subs["quote_body"]

    def test_quote_text_still_strips_punctuation(self):
        # v2 回退兼容: 旧键仍走净标点口径
        subs = _build_substitutions({"quote_text": "便宜到，像白送。", "duration_sec": 6})
        assert "，" not in subs["quote_text"] and "。" not in subs["quote_text"]

    def test_sources_json_capacity_8(self):
        sources = [{"media": f"媒体{i}", "title": f"报道{i}"} for i in range(12)]
        subs = _build_substitutions({"sources": sources, "duration_sec": 6})
        assert subs["sources_json"].count("媒体") == 8

    def test_brand_tag_flows(self):
        subs = _build_substitutions({"brand_tag": "科技·商业", "duration_sec": 6})
        assert "科技·商业" in subs["brand_tag"]

    def test_issue_date_injected(self):
        # HF 渲染沙箱 Date 冻结为 epoch (实测 1970.01), 刊号日期须由填充端注入
        subs = _build_substitutions({"title": "T", "duration_sec": 5})
        assert re.fullmatch(r"\d{4}\.\d{2}", subs["issue_date"])
        explicit = _build_substitutions({"title": "T", "issue_date": "2026.09", "duration_sec": 5})
        assert explicit["issue_date"] == "2026.09"


class TestV3Registry:
    def test_four_v3_templates_registered(self):
        for tid, src in [
            ("hf-title-v3", "hf_title_v3"),
            ("hf-chart-v3", "hf_chart_v3"),
            ("hf-quote-v2", "hf_quote_v2"),
            ("hf-source-v2", "hf_source_v2"),
        ]:
            meta = TEMPLATES[tid]
            assert meta["source_dir"] == src
            assert meta["duration_sec_range"][0] >= 3

    def test_source_v2_max_items_8(self):
        schema = TEMPLATES["hf-source-v2"]["json_schema"]
        assert schema["properties"]["sources"]["maxItems"] == 8

    def test_quote_v2_accepts_quote_body(self):
        schema = TEMPLATES["hf-quote-v2"]["json_schema"]
        assert "quote_body" in schema["properties"]

    def test_v3_template_sources_on_disk(self):
        for tid in ("hf-title-v3", "hf-chart-v3", "hf-quote-v2", "hf-source-v2"):
            src = REPO_ROOT / "templates" / "hf_prep_v3" / TEMPLATES[tid]["source_dir"] / "index.html"
            assert src.is_file(), f"模板源缺失: {src}"

    def test_hf_py_references_v3_ids(self):
        # 接线守卫: hf.py 指向 v3 系 (防意外回退到 v2)
        text = (REPO_ROOT / "app" / "services" / "slot_workflows" / "hf.py").read_text(encoding="utf-8")
        for tid in ("hf-title-v3", "hf-chart-v3", "hf-quote-v2", "hf-source-v2"):
            assert f'"{tid}"' in text, f"hf.py 未引用 {tid}"


class TestCollectNewsSourcesCap:
    def test_cap_is_8(self):
        # 10 个候选 URL → 截 8
        pkg = SimpleNamespace(items=[
            SimpleNamespace(source_url=f"https://m{i}.example.com/a", title=f"报道{i}")
            for i in range(10)
        ])
        db = SimpleNamespace(get=lambda _m, _id: pkg)
        script = SimpleNamespace(material_package_id="pkg-1", article=None)
        srcs = _collect_news_sources(db, script)
        assert len(srcs) == 8
        assert srcs[0]["media"] == "m0.example.com"

    def test_dedupe_and_article_fallback_below_8(self):
        pkg = SimpleNamespace(items=[
            SimpleNamespace(source_url="https://a.example.com/1", title="唯一来源"),
            SimpleNamespace(source_url="https://a.example.com/1", title="重复"),
        ])
        db = SimpleNamespace(get=lambda _m, _id: pkg)
        script = SimpleNamespace(
            material_package_id="pkg-1",
            article=SimpleNamespace(source_url="https://b.example.com/x", title="主稿"),
        )
        srcs = _collect_news_sources(db, script)
        assert len(srcs) == 2
        assert srcs[1]["media"] == "b.example.com"
