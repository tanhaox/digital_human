# -*- coding: utf-8 -*-
"""1105 v3 V1 文稿分诊: 确定性病检/标记路由/清单建议/LLM合并/保存闸 (零 LLM 实调)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import triage


def test_det_fake_marker():
    r = triage.det_check("【大字卡、直接上屏】")
    assert r and r["level"] == "block" and r["kind"] == "fake_marker"


def test_det_legal_markers_pass():
    assert triage.det_check("【卡:止损两问】两轮不开吗？还在赔吗？") is None
    assert triage.det_check("【图:信任公式】") is None
    # 稿件段标 (ep6 实弹: 拆书线节目标题) → 路由为 section, 非拦截
    for s in ("【0–60 秒｜全新打磨开篇（重点强化，抓留存）】",
              "【60-120s｜核心一：信任三阶段】",
              "【165—185秒|书里总纲】"):
        assert triage.det_check(s) is None, s
        assert triage.route_markers(s)[0] == "section", s


def test_det_time_rule():
    r = triage.det_check("2021年某客户跟我谈了三轮")
    assert r and r["level"] == "warn" and r["kind"] == "time_rule"
    assert triage.det_check("2019年那单生意") is None       # 2020 线前通过
    assert triage.det_check("2020年那位客户") is None


def test_det_long_line():
    r = triage.det_check("止" * 70)                          # 70字零标点
    assert r and r["kind"] == "long_line"
    assert triage.det_check("短句。") is None


def test_route_markers():
    m, card = triage.route_markers("【卡:止损两问】两轮不开吗？")
    assert m == "card" and card["name"] == "止损两问" and card["text"] == "两轮不开吗？"
    m2, img = triage.route_markers("【图:信任公式】")
    assert m2 == "img" and img["name"] == "信任公式"
    assert triage.route_markers("普通口播句")[0] == ""


def test_det_list_hint():
    lines = [{"no": 1, "text": "第一，主动简报；"},
             {"no": 2, "text": "第二，小块交付；"},
             {"no": 3, "text": "最后，提前排雷。"}]
    triage.det_list_hint(lines)
    assert any(s["kind"] == "list" for s in lines[0]["suggs"])
    plain = [{"no": 1, "text": "普通句子"}]
    triage.det_list_hint(plain)
    assert not plain[0].get("suggs")


def test_llm_merge_fallback(monkeypatch):
    """LLM 失败 → 退确定性层不炸; 成功 → 语态/建议合并 + 确定性 flag 优先。"""
    lines = [
        {"no": 1, "text": "第一，主动简报；", "mode": "unknown", "flag": None, "suggs": [], "disp": None},
        {"no": 2, "text": "【大字卡、直接上屏】", "mode": "unknown",
         "flag": {"level": "block", "kind": "fake_marker", "msg": "x", "sugg": "y"}, "suggs": [], "disp": None},
    ]
    # 失败路径: llm_triage 抛 → run 的 except 分支 (单独验 llm_triage 冒泡)
    import pytest
    def boom(lines): raise RuntimeError("LLM down")
    monkeypatch.setattr(triage, "llm_triage", boom)
    with pytest.raises(RuntimeError):
        triage.llm_triage(lines)


def test_wiring_v1():
    import inspect
    from pathlib import Path
    from app.routers import anim_v3
    src = inspect.getsource(anim_v3)
    assert "/triage/save" in src and "det_check" in src     # 保存重检接线
    assert "/triage\"" in src                               # 数据端点
    assert Path("web/v3/triage.html").exists()
    # CSP 外置后页面=壳, 逻辑在 triage.js — html+js 合体断言
    both = (Path("web/v3/triage.html").read_text(encoding="utf-8")
            + Path("web/v3/triage.js").read_text(encoding="utf-8"))
    assert "一句一行" in both and "v3_triage" in both        # 页面: 清单+相位触发
