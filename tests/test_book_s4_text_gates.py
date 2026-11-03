# -*- coding: utf-8 -*-
"""S4 文字闸 (1103): Markdown 星号剥除+阻断闸 / ABAB 叠词听感废句 lint.

实锤: ep2 (c5148d33) 「**专业的门槛是懂行。/**信任的门槛是被需要。」跨行错位对
3 处进 segments, TTS 三层清洗均不剥会念出 (对照 -0.3s- 控制符先例);
「书里讲一个教授教授统计学」叠词结巴 = 听感废句 (北斗星令#21)。
"""
from app.services.book_service.creation_common import _listen_feel_issues, validate_script
from app.services.script_parser import clean_episode_script


def _v(text: str) -> list[str]:
    return validate_script(text, 240.0, book_title="值得信赖的顾问")[1]


# ── Markdown 星号 ──

def test_asterisk_gate_blocks():
    iss = _v("**专业的门槛是懂行。\n**信任的门槛是被需要。")
    assert any("Markdown星号残留(阻断)" in i for i in iss), iss


def test_clean_episode_script_strips_asterisk_pair():
    """产线入口剥除 — 盖住修稿/produce 旁路 (含跨行错位对)."""
    t = ("【0-25秒｜钩子】\n为什么说，越专业越签不下单？\n"
         "**专业的门槛是懂行。\n"
         "**信任的门槛是被需要。\n"
         "【25-50秒｜回顾+引入】\n上集讲了闭嘴。\n")
    cleaned, _ = clean_episode_script(t)
    assert "**" not in cleaned and "专业的门槛是懂行。" in cleaned


def test_clean_passes_without_asterisk():
    cleaned, _ = clean_episode_script("【0-25秒｜钩子】\n开头。\n【25-50秒｜回顾+引入】\n承接。\n")
    assert cleaned == "开头。\n承接。"


# ── ABAB 叠词 ──

def test_redup_flagged_ep2_style():
    iss = _listen_feel_issues("书里讲一个教授教授统计学。他问这一点大家会了吗。")
    assert any("叠词听感废句" in i and "教授教授" in i for i in iss), iss


def test_redup_whitelist_passes():
    assert _listen_feel_issues("这事你回去考虑考虑，研究研究再说。") == []


def test_redup_aabb_safe():
    """AABB 型 (认认真真/家家户户) 天然不中 — 只打 ABAB 整块重复."""
    assert _listen_feel_issues("他认认真真记了三页笔记，家家户户都传遍了。") == []
