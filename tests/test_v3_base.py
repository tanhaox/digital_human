# -*- coding: utf-8 -*-
"""1105 v3 双轨 V0: 选线开关/相位注册/闸链视图/路由接线 (零 LLM, 零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_service
from app.services.anim_pipeline import config as anim_cfg
from app.services.anim_pipeline import v3_pipeline


def test_pipeline_line_default_v2(monkeypatch, tmp_path):
    monkeypatch.setattr(anim_cfg, "load",
                        lambda: type("C", (), {"output_root": str(tmp_path)})())
    assert anim_cfg.pipeline_line("任何书") == "v2"          # 无绑定 = v2


def test_pipeline_line_binding_v3(monkeypatch, tmp_path):
    monkeypatch.setattr(anim_cfg, "load",
                        lambda: type("C", (), {"output_root": str(tmp_path)})())
    d = tmp_path / "_资产"
    d.mkdir()
    (d / "产线绑定_测试书.json").write_text('{"line": "v3"}', encoding="utf-8")
    assert anim_cfg.pipeline_line("测试书") == "v3"
    # 坏绑定/非法值 → v2
    (d / "产线绑定_坏书.json").write_text('{"line": "v4"}', encoding="utf-8")
    assert anim_cfg.pipeline_line("坏书") == "v2"
    (d / "产线绑定_烂书.json").write_text("not json", encoding="utf-8")
    assert anim_cfg.pipeline_line("烂书") == "v2"


def test_v3_phases_registered():
    for ph in ("v3_triage", "v3_arcs", "v3_shots", "v3_direct", "v3_stamp"):
        assert ph in anim_service.PHASES, ph


def test_gates_view_chain():
    # 全空 → 全锁
    assert v3_pipeline.gates_view({}) == {
        "triage": False, "arcs": False, "shots": False, "direct": False, "stamp": False}
    # 跳级放行不生效: arcs 开但 triage 未开 → arcs 仍锁 (闸链)
    g = v3_pipeline.gates_view({"v3": {"gates": {"arcs": True}}})
    assert g["arcs"] is False and g["triage"] is False
    # 顺序放行: triage ✓ → triage 开; arcs ✓ → 双开
    g2 = v3_pipeline.gates_view({"v3": {"gates": {"triage": True}}})
    assert g2["triage"] is True and g2["arcs"] is False
    g3 = v3_pipeline.gates_view({"v3": {"gates": {"triage": True, "arcs": True}}})
    assert g3["arcs"] is True
    # stamp 链外 (等音频): 独立读持久位
    g4 = v3_pipeline.gates_view({"v3": {"gates": {"stamp": True}}})
    assert g4["stamp"] is True and g4["triage"] is False


def test_run_phase_stub_and_dispatch():
    import pytest
    # V1 起 triage 是真相位 (不再"建设中"); 其余 V2+ 仍占位
    with pytest.raises(SystemExit, match="建设中"):
        v3_pipeline.run_phase({"phase": "v3_arcs"})
    with pytest.raises(SystemExit, match="v3 相位"):
        v3_pipeline.run_phase({"phase": "v3_unknown"})
    assert v3_pipeline._HANDLERS["triage"] is v3_pipeline._h_triage


def test_wiring_source_asserts():
    import inspect
    from app.routers import anim_v3
    from pathlib import Path
    src = inspect.getsource(anim_v3)
    assert 'pipeline_line(book_title) != "v3"' in src      # 选线闸
    assert "/api/anim/v3" in src and "anim_service.start_phase" in src  # 复用机制
    main = Path("app/main.py").read_text(encoding="utf-8")
    assert "anim_v3.router" in main and '"/web/v3/")' in main  # 注册 + CSP
    assert Path("web/v3/timeline.js").exists() and Path("web/v3/rail.js").exists()
    src2 = inspect.getsource(anim_v3)
    assert '/line/' in src2                                    # 书级选线端点
    bs = Path("web/books_story.js").read_text(encoding="utf-8")
    assert "v3-anim" in bs and "applyV3Line" in bs             # 入口分流按钮+实现
    # 双轨并行 (用户令): 不得吞掉 v2 橙钮 — v3 绿钮是并排新增 (a.after + v3-added), 原钮零改动
    assert "v3-added" in bs and "a.after(b3)" in bs
    assert "a.textContent = '🎬 动画v3" not in bs              # 禁换链式覆盖 (旧 bug 回归哨)
    bj = Path("web/books.js").read_text(encoding="utf-8")
    assert "applyV3Line" in bj                                 # 渲染后挂载
    # CSP 家规 (实锤事故: inline module 被 script-src 'self' 静默拦, 页面卡加载中):
    # /web/v3/ 全部页面零内联 script/style, 逻辑与样式一律外置
    for pg in Path("web/v3").glob("*.html"):
        h = pg.read_text(encoding="utf-8")
        assert '<script type="module">' not in h, f"{pg} 含内联 module (CSP 必拦)"
        assert "<style>" not in h, f"{pg} 含内联 style (CSP 必拦)"
