# -*- coding: utf-8 -*-
"""pinyin_fix 直测 (2026-09-04): 型号连字符守卫 GPT-6→GPT六 (IndexTTS2 读 '-' 为「负」)
+ apply_pinyin_marks 终态规整 (守卫与词表叠加)。"""
from __future__ import annotations

from app.services.pinyin_fix import apply_pinyin_marks, normalize_model_hyphens


class TestNormalizeModelHyphens:
    def test_gpt_versions(self):
        # 用户报告的原始 case: GPT-6 被读成 GPT负六
        assert normalize_model_hyphens("GPT-6来了一波大的") == "GPT六来了一波大的"
        assert normalize_model_hyphens("GPT-4.5") == "GPT四点五"
        assert normalize_model_hyphens("GPT-4o") == "GPT四o"

    def test_model_series(self):
        assert normalize_model_hyphens("B-52轰炸机") == "B五二轰炸机"
        assert normalize_model_hyphens("GLM-4.5对标") == "GLM四点五对标"
        assert normalize_model_hyphens("RTX-4090显卡") == "RTX四零九零显卡"
        assert normalize_model_hyphens("o3-mini便宜") == "o三mini便宜"

    def test_non_model_hyphens_untouched(self):
        # 日期区间/数量区间归 LLM 适配层 (A到B), 纯英文短语/列表符不碰
        assert normalize_model_hyphens("2024-2025年") == "2024-2025年"
        assert normalize_model_hyphens("3-5个") == "3-5个"
        assert normalize_model_hyphens("state-of-the-art") == "state-of-the-art"
        assert normalize_model_hyphens("- 列表项") == "- 列表项"
        assert normalize_model_hyphens("无连字符") == "无连字符"

    def test_negative_number_untouched(self):
        # 负数 (连字符前非字母) 是真「负」, 不许动
        assert normalize_model_hyphens("零下-6度") == "零下-6度"


class TestApplyPinyinMarksFinal:
    def test_hyphen_guard_runs_before_word_table(self):
        # 守卫 + 词表同轮生效
        out = apply_pinyin_marks("GPT-6发布，银行都涨了")
        assert "GPT-6" not in out
        assert out.startswith("GPT六发布，银")
        assert "<行|HANG2>" in out

    def test_no_hyphen_passthrough(self):
        assert apply_pinyin_marks("普通口播稿") == "普通口播稿"
