"""ppt_timing 节奏上限 + watermark 台标 单元测试 (2026-09-03).

目录页白屏修复: 元素少而页长时, 块级/宫格/未匹配铺开受节奏上限钳制,
不再均匀铺满整页时长. watermark: 间歇段数学 + 白底转透明幂等.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.jy_draft_service.ppt_timing import (
    _MAX_BLOCK_SEC,
    compute_block_timing,
    compute_page_timing,
)


def _toc_layers() -> list[dict]:
    """目录页还原 (job 19cc5080 p02): 页头 + 引言 + 01/02/03 标记行."""
    layers = []
    def T(text, top, left, pt=18, bold=False):
        layers.append({"kind": "text", "file": "x.png", "text": text,
                       "left": left, "top": top, "pt": pt, "bold": bold})
    T("Contents", 500_000, 7_000_000, pt=40)
    T("系列导读", 2_600_000, 7_000_000, pt=24)
    T("《某本书》六集精读", 2_600_000, 1_200_000, pt=16)
    for i in range(3):
        y = 3_600_000 + i * 1_000_000
        T(f"0{i + 1}", y, 1_500_000, pt=20, bold=True)
        T(f"第{'一二三'[i]}部分内容标题", y, 2_400_000, pt=20)
    return layers


def test_block_timing_capped_on_sparse_long_page():
    """目录页 63.7s: 最晚入场 ≤ first_delay + 块数×节奏上限 (不再 ~58s)."""
    out = compute_block_timing(_toc_layers(), 0.0, 63.67)
    assert out
    n_blocks = 3  # 页头 + 标记合并列 + 右侧装饰列 (系列导读独立列)
    latest = max(l["start_sec"] for l in out)
    assert latest <= 0.5 + n_blocks * _MAX_BLOCK_SEC + 0.01


def test_block_timing_dense_page_uncapped():
    """正常信息页 (4 块 12 元素 20s): 节奏上限不生效, 仍铺满安全窗."""
    layers = []
    for i in range(12):
        layers.append({"kind": "text", "file": "x.png", "text": f"要点{i}" * 3,
                       "left": 1_000_000 + (i % 3) * 2_500_000,
                       "top": 1_500_000 + (i // 3) * 1_500_000, "pt": 16})
    out = compute_block_timing(layers, 0.0, 20.0)
    latest = max(l["start_sec"] for l in out)
    assert latest > 10.0  # 仍随页长铺开 (旧行为保留)


def test_page_timing_toc_all_visible_early():
    """统一分发入口: 目录页 60s 内全部元素 ≤ 12s 入场."""
    out = compute_page_timing(_toc_layers(), "口播讲别的，与屏上文字无关。", 0.0, 60.0)
    assert out
    assert max(l["start_sec"] for l in out) <= 12.0


def test_add_watermark_segments():
    """446s 视频: 跳过 15s 后每 50s 亮 8s, 末段不贴尾."""
    pytest.importorskip("pyJianYingDraft")
    from app.services.jy_draft_service.watermark import add_watermark

    class _Script:
        def __init__(self):
            self.rows = []
        def add_segment(self, seg, track):
            self.rows.append((track, seg.target_timerange))

    import pyJianYingDraft as d  # noqa: F401  # 依赖守卫: 缺库时此测试应 error 而非静默通过
    logo = Path(__file__).parent / "artifacts" / "_wm_test.png"
    logo.parent.mkdir(exist_ok=True)
    try:
        from PIL import Image
        Image.new("RGBA", (100, 110), (200, 90, 120, 255)).save(logo)
    except ImportError:
        pytest.skip("PIL unavailable")

    script = _Script()
    n = add_watermark(script, int(446.37 * 1e6), logo)
    assert n == 9  # 15..415s 每 50s, 465s 越界
    us = 1_000_000
    starts = [t.start / us for _, t in script.rows]
    assert starts[0] == 15.0
    assert all(t.duration / us == 8.0 for _, t in script.rows)
    assert all(b - a == 50.0 for a, b in zip(starts, starts[1:]))
    assert starts[-1] + 8.0 <= 446.37 - 0.5
    # 素材缺失 → 0 段不抛
    assert add_watermark(_Script(), int(300 * us), Path("N:/nope.png")) == 0
    logo.unlink()
