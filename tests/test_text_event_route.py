# -*- coding: utf-8 -*-
"""1101 U6 批4 事件字统一出口: station 戳判归属 / 装配金字 / 不双渲 (零 GPU)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services import anim_draft
from app.services.anim_pipeline import h3, r2v
from app.services.anim_pipeline.law_blocks import TEXT_ROUTE_CHANNEL
from app.services.anim_pipeline.scene_writer import WRITER_SYS


def _shot(sid, **kw):
    s = {"shot_id": sid, "t_start": 10.0, "t_end": 15.0,
         "text_layer": [], "h3_text_event": {}}
    s.update(kw)
    return s


def test_collect_h3_event_for_h3_station():
    s = _shot("s1", video_station="h3",
              h3_text_event={"text": "太棒了", "at_s": 1.2, "zone": "头顶上方"})
    caps, heroes = anim_draft._collect_text_events([s])
    ev = [e for e in heroes if e["kind"] == "h3_event"]
    assert len(ev) == 1 and ev[0]["text"] == "太棒了"
    assert abs(ev[0]["a0"] - 11.2) < 0.01          # 镜起点 + at_s
    assert ev[0]["a1"] <= 15.0                      # 不越镜尾


def test_r2v_station_now_renders_gold_text():
    # 1107 ID-138 拍板: r2v 停止视频内渲字, 金字无条件覆盖双工位
    # (r2v 删渲 + 装配条件放开两处同改 — 单改任一侧 = 丢字)
    s = _shot("s1", video_station="r2v",
              h3_text_event={"text": "太棒了", "at_s": 1.0})
    caps, heroes = anim_draft._collect_text_events([s])
    assert [e for e in heroes if e["kind"] == "h3_event"]


def test_legacy_shot_defaults_to_h3_station():
    # 存量 doc 无 video_station 戳 → 按 h3 工位渲 (幂等兼容)
    s = _shot("s1", h3_text_event={"text": "破局", "at_s": 0.5})
    caps, heroes = anim_draft._collect_text_events([s])
    assert [e for e in heroes if e["kind"] == "h3_event"]


def test_brand_shot_never_renders_event_text():
    s = _shot("s1", brand_card=True, video_station="h3",
              h3_text_event={"text": "我是老谭", "at_s": 0.5})
    caps, heroes = anim_draft._collect_text_events([s])
    assert not [e for e in heroes if e["kind"] == "h3_event"]


class _Script:
    def __init__(self):
        self.segs = []

    def add_segment(self, seg, track):
        self.segs.append((track, seg))


def test_render_hero_h3_event_gold_big_text():
    script = _Script()
    n = anim_draft._render_hero(script, [
        {"kind": "h3_event", "a0": 10.0, "a1": 12.5, "text": "太棒了",
         "sid": "s1", "zone": "头顶上方"}])
    assert n == 1 and script.segs and script.segs[0][0] == "hero"


def test_zone_y_mapping():
    assert anim_draft._h3_event_y("头顶上方") == -0.45
    assert anim_draft._h3_event_y("画面中部") == 0.0
    assert anim_draft._h3_event_y("") == -0.4


def test_writer_prompt_channel_unified():
    # WRITER_SYS 不再自相矛盾: "H3 渲染它" 已废, 通道句与 law_blocks 同源
    assert "H3 渲染它" not in WRITER_SYS
    assert TEXT_ROUTE_CHANNEL in WRITER_SYS
    assert "@@" not in WRITER_SYS


def test_station_stamp_source():
    # 源码级断言: h3 路由点与 r2v 直调都落 video_station 戳 (防回归)
    import inspect
    assert 'shot["video_station"] = station' in inspect.getsource(h3)
    assert 'shot["video_station"] = "r2v"' in inspect.getsource(r2v)
