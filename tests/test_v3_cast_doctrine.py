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


def test_derive_protects_structural_cards(monkeypatch, tmp_path):
    """1109 🎲 实锤: 重提炼不许洗结构卡 — 观众替身/作者卡 LLM 漏了 → 从旧圣经
    确定性补回 (look 原样); 撞名变体 (查理 ⊃ 查理·格林) 视作已覆盖; 非结构旧卡
    (书内人物) 不保护 (重提炼的正当职责)。1112 修: 老谭基准=代码定稿常量
    _LAOTAN_CARD (旧圣经干瘪版/首书无旧圣经都不再是老谭的基准)。"""
    import json
    from app.services.anim_pipeline import character_sheets as cs
    bb = tmp_path / "bb.json"
    bb.write_text(json.dumps({"characters": [
        {"name": "老谭", "role": "账号主角", "look": "深红色冲锋衣look", "linked_props": []},
        {"name": "观众替身·男", "role": "临演", "look": "浅灰西装look", "linked_props": []},
        {"name": "大卫·梅斯特", "role": "本书作者·信任公式", "look": "藏青西装look", "linked_props": []},
        {"name": "汤玛斯", "role": "书中人物", "look": "浅驼西装look", "linked_props": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cs, "_bible_cache_path", lambda bt: bb)
    # LLM 只吐了新书内卡 + 查理 (撞 查理·格林 型不在本夹具, 另测)
    got = [{"name": "柯波兰德", "role": "书中人物", "look": "x"},
           {"name": "查理·格林", "role": "书中人物", "look": "y"}]
    out = cs._protect_structural("测试书", got, author="大卫·梅斯特")
    names = [c["name"] for c in out]
    assert names[:3] == ["老谭", "观众替身·男", "大卫·梅斯特"]   # 结构卡前置补回
    assert "汤玛斯" not in names                                 # 非结构卡不保护
    assert out[0]["look"] == cs._LAOTAN_CARD["look"]             # 老谭=定稿常量 (非旧圣经短版)
    # 撞名变体: 新表已有 查理 → 旧 查理·格林 不重复补
    bb.write_text(json.dumps({"characters": [
        {"name": "查理·格林", "role": "书中人物", "look": "old"}]}, ensure_ascii=False),
        encoding="utf-8")
    out2 = cs._protect_structural("测试书", [{"name": "查理", "role": "r", "look": "n"}])
    assert [c["name"] for c in out2] == ["老谭", "查理"]          # 视作已覆盖 + 定稿注入
    # 无旧圣经 (首书) → 其余结构卡无可保护, 但老谭定稿仍注入
    bb.unlink()
    got3 = [{"name": "柯波兰德", "role": "r", "look": "x"}]
    out3 = cs._protect_structural("测试书", got3)
    assert [c["name"] for c in out3] == ["老谭", "柯波兰德"]
    assert out3[0]["look"] == cs._LAOTAN_CARD["look"]


def test_derive_prompt_and_wiring():
    """derive prompt 必含卡三件 (作者/替身男女) + 老谭定稿常量接线 (1112: LLM 不产
    老谭, 确定性注入) + 保护接线。"""
    src = open("app/services/anim_pipeline/character_sheets.py",
               encoding="utf-8").read()
    assert "不由你产" in src                       # 老谭定稿不由 LLM 产 (1112 修)
    assert "_LAOTAN_CARD" in src                   # 账号级定稿常量存在且被接线
    assert "chars = _protect_structural(book_title, chars, author)" in src


def test_derive_rolls_back_rewritten_structural(monkeypatch, tmp_path):
    """1109 二次实锤: 🎲 把老谭**重写**新脸 (在场≠没被动过) → 回滚。1112 修:
    老谭基准=代码定稿常量 (旧圣经干瘪版不再是对标物); 观众替身/作者 在场被重写
    走**只升不降** — 变厚=丰厚标准重产放行, 变薄=疑似洗卡回滚旧卡。"""
    import json
    from app.services.anim_pipeline import character_sheets as cs
    bb = tmp_path / "bb.json"
    bb.write_text(json.dumps({"characters": [
        {"name": "老谭", "role": "账号主角", "look": "旧圣经干瘪look", "linked_props": []},
        {"name": "观众替身·男", "role": "临演", "look": "旧版" * 25, "linked_props": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cs, "_bible_cache_path", lambda bt: bb)
    out = cs._protect_structural("测试书", [
        {"name": "老谭", "role": "r", "look": "LLM重写的新脸"},   # 老谭被重写
        {"name": "观众替身·男", "role": "r", "look": "厚" * 130},  # 替身被重写 (更厚)
        {"name": "柯波兰德", "role": "r", "look": "x"},           # 书内卡照常
    ])
    assert out[0]["look"] == cs._LAOTAN_CARD["look"]             # 老谭回滚到定稿常量
    assert out[1]["look"] == "厚" * 130                          # 只升不降: 变厚放行
    assert out[-1]["name"] == "柯波兰德"                         # 非结构卡不动
    out2 = cs._protect_structural("测试书", [
        {"name": "观众替身·男", "role": "r", "look": "薄"}])       # 替身被洗薄
    assert out2[0]["name"] == "老谭"                             # 定稿常驻注入
    assert out2[1]["look"] == "旧版" * 25                        # 变薄 → 回滚旧卡


# ── 1109 子串修 (s7 实锤「几次都修不过」) ─────────────────────────
def test_reverse_gate_substring_of_bound_name(monkeypatch):
    """短卡「大卫」是绑定名「大卫·梅斯特」的子串 — 裸 `n in kf` 把写对全名的
    句子误判冒名, 与正向闸 (卡名必须入画) 永久乒乓, LLM 无解。掩蔽后零报。"""
    monkeypatch.setattr(v3shots, "_look_catalog", lambda bt: {
        n: f"{n}的look" for n in ("大卫", "大卫·梅斯特", "大卫·福克", "老谭")})
    doc = _doc("大卫·梅斯特站在白板前，金丝眼镜反着冷光。", cast=["大卫·梅斯特"])
    iss = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"],
                                   "值得信赖的顾问")
    assert iss == {}          # 写的就是绑定全名 → 正反向都干净


def test_reverse_gate_alien_reports_longest_only(monkeypatch):
    """独立提及的未绑定名 (大卫·福克) → 拦; 与其子串的短卡「大卫」不再并列报。"""
    monkeypatch.setattr(v3shots, "_look_catalog", lambda bt: {
        n: f"{n}的look" for n in ("大卫", "大卫·梅斯特", "大卫·福克")})
    doc = _doc("大卫·福克与大卫·梅斯特在会议室对坐。", cast=["大卫·梅斯特"])
    iss = v3shots.cast_gate_issues(doc["v3"]["shots"], doc["v3"]["arcs"],
                                   "值得信赖的顾问")
    msgs = iss.get("s1", [])
    assert any("未绑定卡名 大卫·福克" in t for t in msgs)
    assert not any("未绑定卡名 大卫/" in t or "/大卫 " in t for t in msgs)
    assert not any("未以卡名入画面句" in t for t in msgs)   # 梅斯特在场, 正向过
