"""本书专用中国案例库 (一案一库) 测试 (2026-10-19 用户令):
大纲落库后产 12-15 条与总纲概念对齐的中国案例 → data/l0/{书}/案例库-本书.json;
幂等/校验 (region=global 滤除, 无 facts/concepts 滤除, <8 条不落盘)/总纲概念为空跳过。
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from app.services.book_service import creation_steps as cs


def _mk_book():
    eps = [
        NS(ep_index=i, title=f"第{i}集主题", roadmap_json={"概念": ["信任等式", "倾听"]})
        for i in (1, 2, 3)
    ]
    return NS(book_title="测试一书", core_claim="信任先于技术", input_json={}, episodes=eps)


def _llm_rows(extra):
    rows = [
        {"name": f"案例{i}", "industry": "x", "concepts": ["信任等式"], "facts": ["动作a", "动作b"],
         "region": "cn"} for i in range(10)
    ] + extra
    return NS(chat=lambda s, u, **k: json.dumps(rows, ensure_ascii=False))


def _setup(monkeypatch, tmp_path, extra=()):
    monkeypatch.setattr(cs, "_llm", lambda: _llm_rows(list(extra)))
    monkeypatch.setattr("app.services.book_service.l0._l0_dir", lambda t: tmp_path / t)
    return tmp_path / "测试一书" / "案例库-本书.json"


def test_generates_and_filters(tmp_path, monkeypatch):
    bp = _setup(monkeypatch, tmp_path, extra=[
        {"name": "海外案", "concepts": ["倾听"], "facts": ["f"], "region": "global"},   # 海外滤除
        {"name": "无事实案", "concepts": ["倾听"], "facts": [], "region": "cn"},          # 无facts滤除
        {"name": "某某品牌（早期）", "concepts": ["信任等式"], "facts": ["f1"], "region": "cn"},  # 括号后缀剥离
        {"name": "测试一书", "concepts": ["信任等式"], "facts": ["f1", "f2"], "region": "cn"},
        {"name": "测试一书集团", "concepts": ["倾听"], "facts": ["f"], "region": "cn"},       # 同企变体去重
        {"name": "IBM", "concepts": ["倾听"], "facts": ["f"], "region": "cn"},                # 纯ASCII滤除
    ])
    issues = cs.build_book_cases(_mk_book())
    assert issues == [] or all("对齐率" in i for i in issues)  # 只允许对齐率提示
    d = json.loads(bp.read_text(encoding="utf-8"))
    names = [c["name"] for c in d["cases"]]
    assert len(names) == 12 and "海外案" not in names and "无事实案" not in names
    assert "某某品牌" in names and "（" not in "".join(names)  # 括号后缀已剥
    assert "IBM" not in names and "测试一书集团" not in names  # ASCII/同企变体滤除
    assert all(c["status"] == "可用" for c in d["cases"])


def test_idempotent_skip(tmp_path, monkeypatch):
    bp = _setup(monkeypatch, tmp_path)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text('{"cases": []}', encoding="utf-8")
    called = []
    monkeypatch.setattr(cs, "_llm", lambda: called.append(1) or _llm_rows([]))
    assert cs.build_book_cases(_mk_book()) == []
    assert not called  # 已存在不调 LLM


def test_skip_when_no_concepts(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    book = NS(book_title="测试二书", core_claim="", input_json={},
              episodes=[NS(ep_index=1, title="t", roadmap_json={})])
    assert cs.build_book_cases(book) == ["本书案例库跳过: 总纲概念为空 (先出总纲)"]


def test_too_few_no_write(tmp_path, monkeypatch):
    bp = _setup(monkeypatch, tmp_path)
    rows = [{"name": "孤案", "concepts": ["信任等式"], "facts": ["f"], "region": "cn"}]
    monkeypatch.setattr(cs, "_llm",
                        lambda: NS(chat=lambda s, u, **k: json.dumps(rows, ensure_ascii=False)))
    issues = cs.build_book_cases(_mk_book())
    assert issues and "不合格" in issues[0]
    assert not bp.exists()
