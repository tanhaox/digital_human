"""拆书线字数带三档测试 (2026-10-18 C4a, 北斗星第3/5条):
语速 5.0 实测锚 (《定位》7 集 full_paragraph.wav, median 4.974) + 平台包络 4:30-7:00。
校准锚: 旧 4.6 带把 300s 集压到 1297-1380, 实测语速下只出 ~4:36 视频 (偏短)。
"""
from __future__ import annotations

from app.services.book_service.creation_common import (
    PLATFORM_DURATION_BAND,
    RATE_FAST,
    RATE_SLOW,
    SPEECH_RATE,
    validate_script,
    word_band,
)
from app.services.book_service.passes import word_band_ok

_LABELS = ("【0-28秒｜钩子】", "【28-56秒｜回顾+引入】", "【56-117秒｜核心概念拆解（一）】",
           "【117-178秒｜核心概念拆解（二）】", "【178-233秒｜核心概念拆解（三）】",
           "【233-300秒｜总结+下期预告】")


def _script(chars: int, title: str = "值得信赖的顾问：成为客户心中无可替代的人") -> str:
    """六标签 + 均分填充到指定净字数 (标签不计字数)。"""
    body = "字" * chars
    per = chars // 6 + 1
    parts, i = [], 0
    for lab in _LABELS:
        parts.append(lab)
        parts.append(body[i:i + per])
        i += per
    return title.join(["", ""]) + "\n".join(parts)


# ── word_band: 相对带 ∩ 平台包络 ──

def test_band_300s():
    """300s 集: cap=1500 → 软带 [1350,1620]∩包络[1434,2024] = [1434,1620]。"""
    assert word_band(300) == (1434, 1620, 1434, 1800)


def test_band_platform_envelope_floor_lifts_short_targets():
    """短目标集 (275s): 相对下限 1237 被包络抬到 1434 — 最快语速 5.31 下仍 >4:30。"""
    lo, hi, hlo, hhi = word_band(275)
    assert lo == 1434 and hlo == 1434
    assert lo / RATE_FAST > 270 and hhi / RATE_SLOW < 420


def test_band_long_target_ceiling_capped_by_envelope():
    """长目标集 (420s): 相对上限 2268 被包络压到 2024 — 最慢语速 4.82 下仍 <7:00。"""
    lo, hi, hlo, hhi = word_band(420)
    assert hi == 2024 and hhi == 2024
    assert lo / RATE_FAST > 270 and hhi / RATE_SLOW < 420


def test_band_nonempty_across_platform_band():
    """平台带内任意目标, 修复带必非空 (270s 与 420s 两端点也成立)。"""
    for t in (270.5, 300, 360, 419.5):
        lo, hi, _, _ = word_band(t)
        assert lo <= hi, f"target={t} 带子为空"


def test_speech_rate_constants_match_measurement():
    """实测锚: median 4.974≈5.0, 区间 [4.82, 5.31]; 平台带 (270, 420)s。"""
    assert SPEECH_RATE == 5.0 and RATE_FAST == 5.31 and RATE_SLOW == 4.82
    assert PLATFORM_DURATION_BAND == (270.0, 420.0)


# ── validate_script: 三档判定 ──

def test_validate_soft_over_band_1765():
    """ep1 实锤回放: 1765 字在 300s 软带外 (1620) 硬线内 (1800) → 软带 issue, 无"硬线"字样。"""
    issues = validate_script(_script(1765), 300)[1]
    wc = [i for i in issues if i.startswith("字数")]
    assert len(wc) == 1 and "超出 1620" in wc[0] and "硬线" not in wc[0]


def test_validate_hard_line_over_1800():
    """1810 字超 300s 硬线上限 1800 → "超硬线" issue (打回重写级)。"""
    issues = validate_script(_script(1810), 300)[1]
    assert any(i.startswith("字数") and "超硬线" in i for i in issues)


def test_validate_in_band_clean():
    """1500 字 (300s cap) 落带内 → 无字数 issue。"""
    issues = validate_script(_script(1500), 300)[1]
    assert not [i for i in issues if i.startswith("字数")]


def test_validate_under_envelope_floor():
    """1300 字破包络下限 1434 (最快语速下不足 4:30) → "不足" issue。"""
    issues = validate_script(_script(1300), 300)[1]
    wc = [i for i in issues if i.startswith("字数")]
    assert len(wc) == 1 and "不足" in wc[0]


# ── word_band_ok: 距带比较用新带 ──

def test_word_band_ok_uses_new_band():
    """1600 (带内) 距带近于 1765 (带外) → True; 反向 False。"""
    base = ["字数 1765 超出 1620 (目标300s → 1434-1620)"]
    closer = ["字数 1600 超出 1620 (目标300s → 1434-1620)"]  # 假设文案, 只取数字
    assert word_band_ok(closer, base, 300) is True
    assert word_band_ok(base, closer, 300) is False
