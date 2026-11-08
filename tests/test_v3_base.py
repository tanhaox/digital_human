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
    """占位相位抛「建设中」, 已上线相位不抛 — 自动识别, 免得每批手改本测试 (实锤三次)。"""
    import inspect
    import pytest

    def _is_stub(fn):
        return "_stub(" in inspect.getsource(fn)

    stubs = [p for p, h in v3_pipeline._HANDLERS.items() if _is_stub(h)]
    for phase in stubs:
        with pytest.raises(SystemExit, match="建设中"):
            v3_pipeline.run_phase({"phase": f"v3_{phase}"})
    assert "triage" not in stubs and "arcs" not in stubs and "shots" not in stubs
    with pytest.raises(SystemExit, match="v3 相位"):
        v3_pipeline.run_phase({"phase": "v3_unknown"})


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
    # 1109 用户令: 🎬 动画v3 = 每集默认入口 (直吃 script_text), 双轨分流/四钮全撤
    assert "v3-anim" in bs and "/web/v3/index.html" in bs
    assert "🎬 动画v3 ↗" in bs
    assert "async function applyV3Line" not in bs              # 分流实现退役 (注释提及不算)
    assert "_v3lineCache = {}" not in bs and "v3-added" not in bs
    assert "🎬 动画 ↗" not in bs and "音频页 ↗" not in bs      # v2 动画/音频页钮撤
    assert "async function rerunEp" not in bs                 # 级联重跑函数退役 (注释提及不算)
    assert "async function ttsPreview" not in bs              # TTS 预览函数退役
    assert ">🔊 TTS音频<" not in bs and ">级联重跑" not in bs  # 钮本体撤 (注释提及不算)
    bj = Path("web/books.js").read_text(encoding="utf-8")
    assert "applyV3Line" in bj                                 # 渲染后挂载 (typeof 守卫 no-op)
    # CSP 家规 (实锤事故: inline module 被 script-src 'self' 静默拦, 页面卡加载中):
    # /web/v3/ 全部页面零内联 script/style, 逻辑与样式一律外置
    for pg in Path("web/v3").glob("*.html"):
        h = pg.read_text(encoding="utf-8")
        assert '<script type="module">' not in h, f"{pg} 含内联 module (CSP 必拦)"
        assert "<style>" not in h, f"{pg} 含内联 style 块 (CSP 必拦)"
        assert ' style="' not in h, f"{pg} 含 style= 属性 (CSP style-src 必拦, 走类名)"
    # JS 侧同样禁内联 style 属性 (Playwright 实锤: timeline innerHTML style= 被拦 10 处)
    for js in Path("web/v3").glob("*.js"):
        src = js.read_text(encoding="utf-8")
        assert ' style="' not in src, f"{js} 含 style= 属性 (CSP 必拦, 改 CSSOM)"
        assert "cssText" not in src, f"{js} 用 cssText 写内联样式 (CSP 必拦, 改类名)"


def test_rail_gate_keys_match_backend():
    """回归哨 (1105 实锤): 轨上步骤 id 与闸态键不同名 (concept vs arcs) → ② 永不亮 ✓,
    入口页看起来像从头开始; 且未建页面的步骤必须有 gate/null 归类, 不许漏。"""
    import re
    from pathlib import Path
    from app.services.anim_pipeline.v3_pipeline import GATE_CHAIN
    src = Path("web/v3/rail.js").read_text(encoding="utf-8")
    rows = re.findall(
        r"\{\s*n:\s*'[^']+',\s*id:\s*'([^']+)',\s*gate:\s*(null|'[^']+')", src)
    assert len(rows) == 8, rows
    by_id = {pid: (None if g == "null" else g.strip("'")) for pid, g in rows}
    for g in by_id.values():
        assert g is None or g in GATE_CHAIN, f"未知闸键: {g}"
    assert by_id["concept"] == "arcs"      # 立意页的闸键叫 arcs (相位名), 不叫 concept
    assert by_id["shots"] == "shots" and by_id["triage"] == "triage"
