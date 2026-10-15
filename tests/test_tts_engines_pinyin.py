"""拼音标注协议引擎边界转换测试 (2026-09-03): IndexTTS2 只认裸内联拼音 —
<蛤|HA2> 原样发会被 BPE 切成 [蛤, |, HA2] 字拼音双读 (蛤蟆先生 PPT 实听实锤,
离线 bpe.model token 探针: <蛤|HA2><蟆|MA2> → ['<','蛤','|','HA2','><','蟆','|','MA2','>'];
ha2 ma2 → ['HA2','MA2',...])。"""
from __future__ import annotations

from scripts.tts_lib.engines import _marks_to_bare_pinyin, _strip_pinyin_marks


def test_marks_to_bare_pinyin():
    # 词表标注 → 裸拼音 + 尾空格 (相邻标注不粘连成一个 ASCII 串)
    assert _marks_to_bare_pinyin("<蛤|HA2><蟆|MA2>住在庄园里") == "HA2 MA2 住在庄园里"
    assert _marks_to_bare_pinyin("磷化<铟|YIN1>材料") == "磷化YIN1 材料"
    # 手写临时标注同协议
    assert _marks_to_bare_pinyin("银<行|HANG2>门口") == "银HANG2 门口"
    # 无标注原样
    assert _marks_to_bare_pinyin("普通文本, 2026!") == "普通文本, 2026!"


def test_strip_pinyin_marks_for_fish_f5():
    """fish/f5 无拼音能力 → 退化为裸字 (拼音会被当英文读)。"""
    assert _strip_pinyin_marks("<蛤|HA2><蟆|MA2>住在庄园里") == "蛤蟆住在庄园里"
    assert _strip_pinyin_marks("磷化<铟|YIN1>材料") == "磷化铟材料"
    assert _strip_pinyin_marks("普通文本") == "普通文本"
