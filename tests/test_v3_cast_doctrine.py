# -*- coding: utf-8 -*-
"""1109 选角机制 (用户令: 书中人物优先): ② 裁决序 + 卡名闸反向条款。

s1 实锤: A1 (hook) cast=[] 而 画面句写了 老谭+冲锋衣 look — ③ writer 跨弧
偷看同批 cast 列泄漏主角, 闸只拦正向 (cast 非空缺名) 不拦冒名。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import arcs as v3arcs
from app.services.anim_pipeline.v3_pipeline import shots as v3shots

# 圣经卡目录 (真集九卡名, look 略)
CARD_NAMES = ["老谭", "大卫·梅斯特", "阿特金森", "观众替身·技术专家"]


def _doc(kf: str, cast: list[str]):
    """单弧两镜, 弧 cast 可调; bible 目录 monkeypatch 用真卡名。"""
    lines = [{"no": 1, "mode": "comm", "text": "第一句口播。"},
             {"no": 2, "mode": "comm", "text": "第二句口播。"}]
    arcs = [{"arc_id": "A1", "type": "narr", "no_start": 1, "no_end": 2,
             "world": "现代办公", "narrative": "讲信任", "cast": cast}]
    shots = [{"sid": "s1", "arc_id": "A1", "arc_type": "narr",
              "line_refs": [1], "est_s": 5.0, "narration": "",
              "keyframe_zh": kf, "in_image_text": [], "text_layer": []}]
    return {"book_title": "值得信赖的顾问", "v3": {"lines": lines, "arcs": arcs,
                                                   "shots": shots}}


def _cards(monkeypatch):
    monkeypatch.setattr(v3shots, "_look_catalog",
                        lambda bt: {n: f"{n}的look摘要" for n in CARD_NAMES})


# ── 卡名闸反向条款 ────────────────────────────────────────────────

def test_reverse_gate_flags_unbound_card_name(monkeypatch):
    """s1 实弹形: 空绑弧冒卡名 → 拦 (正向无报)。"""
    _cards(monkeypatch)
    doc = _doc("深夜卧室床沿，老谭穿着深红色冲锋衣侧身坐着，手机屏呈熄光暗面，"
               "幽蓝冷光映在他半张脸上，景深感深远。", cast=[])
    iss = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"],
                                   "值得信赖的顾问")
    assert "s1" in iss
    assert any("未绑定卡名" in t and "老谭" in t for t in iss["s1"])
    assert not any("未以卡名入画面句" in t for t in iss["s1"])   # 正向无报


def test_reverse_gate_flags_cross_arc_leak(monkeypatch):
    """绑了 A 卡却写 B 卡 (跨弧泄漏) → 反向拦; A 卡在场正向也过。"""
    _cards(monkeypatch)
    doc = _doc("阿特金森与老谭同立清单板前，两人身形一深一浅。", cast=["阿特金森"])
    iss = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"],
                                   "值得信赖的顾问")
    assert any("未绑定卡名" in t and "老谭" in t for t in iss.get("s1", []))
    assert not any("未以卡名入画面句" in t for t in iss.get("s1", []))


def test_reverse_gate_clean_when_bound(monkeypatch):
    """绑谁写谁 → 零报; 无圣经 (book_title 空/查无此书) → 反向不执法。"""
    _cards(monkeypatch)
    doc = _doc("老谭穿深红色冲锋衣坐在床沿，冷光映面。", cast=["老谭"])
    iss = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"],
                                   "值得信赖的顾问")
    assert iss == {}
    iss2 = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"], "")
    assert iss2 == {}


def test_reverse_feedback_reaches_llm_and_fixes(monkeypatch):
    """一键修宇宙: 冒名问题进 LLM 反馈; 改身份词 (去卡名) 复检过闸。"""
    _cards(monkeypatch)
    doc = _doc("深夜卧室床沿，老谭穿着深红色冲锋衣侧身坐着，冷光映面。", cast=[])
    from app.services import llm_service
    seen = {}

    def fake_chat(sys_p, prompt, **kw):
        seen["prompt"] = prompt
        import json
        return json.dumps({"shots": [
            {"sid": "s1", "keyframe_zh":
             "深夜卧室床沿，一位穿深红色冲锋衣的男子侧身坐着，右手握着的手机屏"
             "呈熄光暗面，只有一层幽蓝冷光映在他半张脸与下巴上，前景是被褥的"
             "褶皱，中景是肩背轮廓，后景窗外城市微光从窗帘缝漏进来，景深深远。"}]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    monkeypatch.setattr(v3shots, "text_layer_issues",
                        lambda sh, book_title="": {})
    r = v3shots.fix_shot_text(doc, ["s1"], "值得信赖的顾问")
    assert "未绑定卡名" in seen["prompt"]          # 真问题逐字上桌
    assert "s1" in r["fixed"] and "s1" not in r["unfixed"]


# ── ② 裁决序 + ③ 规则 10 (源级断言) ──────────────────────────────

def test_arcs_cast_doctrine_in_prompt():
    """裁决序入 ② _SYS: 书中人物优先 / 老谭只绑人设+cta/brand。"""
    src = open("app/services/anim_pipeline/v3_pipeline/arcs.py",
               encoding="utf-8").read()
    assert "书中人物优先" in src and "讲谁的书谁上镜" in src
    assert "老谭只绑人设弧" in src and "cta/brand" in src


def test_shots_rule10_no_pretend_casting():
    """③ 规则 10 扩: 未绑本弧的卡名不许冒名 (选角走 ② 正门)。"""
    src = open("app/services/anim_pipeline/v3_pipeline/shots.py",
               encoding="utf-8").read()
    assert "画面句只许用本弧 cast 内的卡名" in src
    assert "不许冒名" in src


def test_standins_rule_in_derive_and_arcs():
    """1109 用户令: 角色台必含观众替身一男一女 (临演=口播的'你'); ② 裁决序同源。"""
    src = open("app/services/anim_pipeline/character_sheets.py",
               encoding="utf-8").read()
    assert "观众替身一男一女" in src and "观众替身·男" in src
    assert "服装色系" in src                     # 男女撞款=服装定角歧义
    arcs_src = open("app/services/anim_pipeline/v3_pipeline/arcs.py",
                    encoding="utf-8").read()
    assert "男/女临演" in arcs_src
