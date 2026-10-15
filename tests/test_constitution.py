# -*- coding: utf-8 -*-
"""宪法模块测试 (1013 P0 阶段1 — 数值等价 + 分区判定 + 唯一出口)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.anim_pipeline import constitution as CST


def test_duration_table_defaults():
    dt = CST.duration_table()
    assert dt["slot_lo"] == 3.0 and dt["slot_hi"] == 6.0
    assert dt["elastic_lo"] == 2.8 and dt["elastic_hi"] == 6.3
    assert dt["split_sent"] == 9.0
    assert dt["shot_floor"] == 1.5 and dt["shot_min_span"] == 4.0
    assert dt["h3_max_span"] == 10.0


def test_zone_of_deterministic():
    t = lambda reg, ts: {"register": reg, "t_start": ts, "brand_card": False}
    assert CST.zone_of(t("host_brand", 30), {}, 0) == "brand"
    assert CST.zone_of(t("", 10), {}, 0) == "hook"
    assert CST.zone_of(t("", 64.9), {}, 5.5) == "hook"      # 音频轴 59.4
    assert CST.zone_of(t("", 70), {}, 5.5) == "mid"          # 音频轴 64.5
    assert CST.zone_of(t("case_narrative", 200), {}, 5.5) == "case"
    assert CST.zone_of(t("author_cite", 200), {}, 5.5) == "cite"
    assert CST.zone_of(t("", 200), {}, 5.5) == "mid"


def test_person_quota_zone_values():
    # 1013 MTV正名校准: hook/mid 0.6 = 电影情节为主 (试点自然值 76-80% / 老书基线)
    assert CST.zone_value("person_quota", "scene_min_present", "hook") == 0.6
    assert CST.zone_value("person_quota", "scene_min_present", "mid") == 0.6
    assert CST.zone_value("person_quota", "scene_min_present", "case") == 1.0
    assert CST.zone_value("person_quota", "episode_max_single_char", "mid") is None  # 非分区键→None


def test_word_tables_nonempty():
    words = CST.person_words()
    assert "人物" in words and "匠人" in words
    assert "身高" in CST.measure_tails()
    assert "辽阔" in CST.depth_big_words()


def test_prompt_block_hook_contains_numbers():
    block = CST.prompt_block("hook")
    assert "特区条款" in block and "微空间变奏" in block and "视线链" in block


def test_load_missing_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(CST, "_const_path", lambda: tmp_path / "nonexistent.json")
    doc = CST.load()
    assert doc["version"] == 0
    assert CST.person_words()  # 默认词表保命


def test_writer_flag_reads_json():
    assert isinstance(CST.writer_enabled(), bool)
