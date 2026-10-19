# -*- coding: utf-8 -*-
"""1019 scene_writer JSON 解析容错测试 — Extra data 连坐救回 (ep6 A6 两实锤)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.llm_service import _parse_json


def test_extra_data_rescued():
    """完整 JSON 后跟总结文字 → 首个对象救回 (char 15626/24848 实锤形态)."""
    payload = json.dumps({"groups": [{"g": 1}], "arcs": [{"arc_id": "A6"}]},
                         ensure_ascii=False)
    out = _parse_json(payload + "\n\n以上是本场分镜, 如需调整请告知。")
    assert out["groups"] == [{"g": 1}]


def test_double_json_rescued():
    """第二个 JSON 追加 → 取首个."""
    a = json.dumps({"ep_summary": "x", "groups": [{"g": 1}]}, ensure_ascii=False)
    b = json.dumps({"note": "重复输出的补充"}, ensure_ascii=False)
    out = _parse_json(a + "\n" + b)
    assert out["ep_summary"] == "x" and "note" not in out


def test_markdown_fence_still_works():
    out = _parse_json("```json\n{\"groups\": [{\"g\": 2}]}\n```")
    assert out == {"groups": [{"g": 2}]}


def test_leading_prose_still_works():
    out = _parse_json("好的, 以下是分镜:\n{\"groups\": []}")
    assert out == {"groups": []}


def test_truly_broken_json_still_raises():
    """真·截断/坏 JSON 不吞错 — 该炸还是炸 (上游两把重试网接住)."""
    with pytest.raises(json.JSONDecodeError):
        _parse_json('{"groups": [{"g": 1, "broken": ')


def test_empty_raises_as_before():
    """空输入维持原行为抛错 (改它超本批范围)."""
    with pytest.raises(json.JSONDecodeError):
        _parse_json("")
    with pytest.raises(json.JSONDecodeError):
        _parse_json(None)
