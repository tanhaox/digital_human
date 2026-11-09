# -*- coding: utf-8 -*-
"""1109 用户令: ③ 页问题给解法 — 一键修 (fix_shot_text) / 一键拆行 (split_slot_line)."""
from app.services.anim_pipeline.v3_pipeline import shots as v3shots

# s3 原句形 (搁笔+人影) — 一键修的靶
PEN_KF = ("空无一人的会议室一角，空白白板与一张起伏的波形图静物并置，波形图纸面没有任何文字；"
          "一支笔搁在波形图下方，背景磨砂玻璃隔断后透出模糊人影与冷白灯光，"
          "前景桌面反光里映出波形轮廓，画面采用大场景俯视，主体波形纸只占一角。")
CLEAN_KF = ("空无一人的会议室一角，一张起伏的波形图静物摆在长桌中央，图纸边缘微微卷起；"
            "背景磨砂玻璃隔断透出冷白灯光，前景桌面反光里映出波形轮廓，"
            "画面采用大场景俯视，主体波形纸只占一角，桌面尽头立着一只没人的高脚凳。")


def _doc(kf: str = PEN_KF, line_text: str | None = None):
    """合成 doc: 单弧两行, 一镜装包。line_text 超长可造超限槽。"""
    lines = [{"no": 1, "mode": "comm", "text": line_text or "第一句口播。"},
             {"no": 2, "mode": "comm", "text": "第二句口播。"}]
    arcs = [{"arc_id": "A1", "type": "narr", "no_start": 1, "no_end": 2,
             "world": "现代办公", "narrative": "讲信任"}]
    shots = [{"sid": "s1", "arc_id": "A1", "arc_type": "narr",
              "line_refs": [1, 2], "est_s": 3.5, "narration": "",
              "keyframe_zh": kf, "in_image_text": [], "text_layer": []},
             {"sid": "s2", "arc_id": "A1", "arc_type": "narr",
              "line_refs": [3], "est_s": 4.0, "narration": "",
              "keyframe_zh": CLEAN_KF, "in_image_text": [], "text_layer": []}]
    return {"book_title": "测试书", "v3": {"lines": lines, "arcs": arcs,
                                           "shots": shots}}


# ── 一键修 ─────────────────────────────────────────────────────────

def _cast_doc():
    """弧绑 cast 而画面句零卡名 — 纯卡名闸问题镜 (s13 实锤形)。
    s1 预填「男子」: 人物配额不双报 (1109 令54 补后配额同入修复宇宙, 夹具要隔离)。"""
    doc = _doc(kf=CLEAN_KF + "一位男子立于长桌旁。")
    doc["v3"]["arcs"][0]["cast"] = ["老谭"]
    return doc


def test_cast_gate_feedback_reaches_llm(monkeypatch):
    """卡名闸消息必须进 LLM 反馈 (s13 实锤: 不进则退化「画面不满意」,
    LLM 化妆式微调, 页面条目永不消失)。"""
    from app.services import llm_service
    doc = _cast_doc()
    seen = {}

    def fake_chat(sys_p, prompt, **kw):
        seen["prompt"] = prompt
        import json
        import re
        return json.dumps({"shots": [
            {"sid": s, "keyframe_zh": CLEAN_KF + "老谭立于桌旁。"}
            for s in re.findall(r"^(s\d+)", prompt, flags=re.M)]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3shots.fix_shot_text(doc, ["s1"])
    assert "未以卡名入画面句" in seen["prompt"]      # 真问题逐字上桌
    assert "画面不满意" not in seen["prompt"]        # 不再退化兜底语


def test_cast_gate_fixed_only_when_name_in(monkeypatch):
    """复检同宇宙: 卡名入句才报 fixed; 名字不进句 → unfixed 带卡名消息。"""
    from app.services import llm_service

    # 修好版: 老谭入句 → fixed, unfixed 空
    doc = _cast_doc()

    def ok_chat(sys_p, prompt, **kw):
        import json
        import re
        # 卡名 (老谭) + 人物词 (男子, 配额同过 — LLM 真实行为: 人物镜双达标)
        return json.dumps({"shots": [
            {"sid": s, "keyframe_zh": CLEAN_KF + "老谭与一位男子同立长桌尽头。"}
            for s in re.findall(r"^(s\d+)", prompt, flags=re.M)]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(ok_chat), raising=False)
    r = v3shots.fix_shot_text(doc, ["s1"])
    assert "s1" in r["fixed"] and "s1" not in r["unfixed"]

    # 修不好版: 仍零卡名 → 不许报修好
    doc2 = _cast_doc()

    def bad_chat(sys_p, prompt, **kw):
        import json
        import re
        return json.dumps({"shots": [
            {"sid": s, "keyframe_zh": CLEAN_KF + " 光影更亮。" * 3}
            for s in re.findall(r"^(s\d+)", prompt, flags=re.M)]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(bad_chat), raising=False)
    r2 = v3shots.fix_shot_text(doc2, ["s1"])
    assert "s1" in r2["unfixed"]
    assert any("未以卡名入画面句" in t for t in r2["unfixed"]["s1"])


def test_fix_shot_text_rewrites_only_target(monkeypatch):
    """问题镜定点重写, 干净镜不动; 补投一轮后清零。"""
    doc = _doc()
    calls = []

    def fake_chat(sys_p, prompt, **kw):
        calls.append(prompt)
        import json
        return json.dumps({"shots": [
            {"sid": "s1", "keyframe_zh": CLEAN_KF}]})

    from app.services import llm_service
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    r = v3shots.fix_shot_text(doc, ["s1", "s2"])
    assert r["fixed"] == ["s1"]
    assert doc["v3"]["shots"][0]["keyframe_zh"] == CLEAN_KF
    assert doc["v3"]["shots"][1]["keyframe_zh"] == CLEAN_KF  # s2 本来就干净未动
    assert not any("搁笔" in t or "残留人物词" in t
                   for t in r["issues"].get("s1") or [])


def test_fix_shot_text_feedback_reaches_llm(monkeypatch):
    """问题清单要进 LLM 输入行 (解法不只列出 — 反馈驱动重写)。"""
    doc = _doc()
    seen = {}

    def fake_chat(sys_p, prompt, **kw):
        seen["rows"] = prompt
        seen["sys"] = sys_p
        import json
        return json.dumps({"shots": [
            {"sid": "s1", "keyframe_zh": CLEAN_KF}]})

    from app.services import llm_service
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3shots.fix_shot_text(doc, ["s1"])
    assert "旧画面句" in seen["rows"] and "质检问题" in seen["rows"]
    assert "空镜搁笔" in seen["rows"]              # 反馈逐字上桌
    assert v3shots._FIX_SYS_ADDENDUM.strip()[:4] in seen["sys"]
    assert "最小改动" in seen["sys"]


def test_router_fix_endpoints_wired():
    """路由接线: fix_text / split_slot / slot_overs 三件上桌。"""
    src = open("app/routers/anim_v3.py", encoding="utf-8").read()
    assert "shots/fix_text" in src and "fix_shot_text" in src
    assert "shots/split_slot" in src and "split_slot_line" in src
    assert "slot_overs" in src
    assert 'text_layer_issues(shots, book_title=book_title)' in src  # 令53 补丁
    js = open("web/v3/shots.js", encoding="utf-8").read()
    assert "fix_text" in js and "split_slot" in js and "一键拆行" in js
    assert "一键修全部" in js and "data-fix" in js


def test_arc_quota_feedback_reaches_llm(monkeypatch):
    """弧级配额问题 (分区人物配额「补 sN」) 也要给解法 — 点名镜反馈进 LLM。"""
    from app.services import llm_service
    doc = _cast_doc()
    seen = {}

    def fake_tli(shots, book_title=""):
        # 单镜探针 / 已有人物的全集 → 干净; 否则弧级配额点名 s1
        if len(shots) == 1 or any("老谭" in str(s.get("keyframe_zh") or "") for s in shots):
            return {}
        return {"弧级": ["分区人物配额: 场A1[hook] 4镜仅2镜有人物 (需≥3) — 补 s1/s2"]}

    monkeypatch.setattr(v3shots, "text_layer_issues", fake_tli)

    def fake_chat(sys_p, prompt, **kw):
        seen["prompt"] = prompt
        import json
        import re
        return json.dumps({"shots": [
            {"sid": s, "keyframe_zh": CLEAN_KF + "老谭立在长桌尽头。"}
            for s in re.findall(r"^(s\d+)", prompt, flags=re.M)]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    r = v3shots.fix_shot_text(doc, ["s1"])
    assert "分区人物配额不足" in seen["prompt"]       # 弧级问题分发到点名镜
    assert "人物镜" in seen["prompt"]
    assert "s1" in r["fixed"] and "s1" not in r["unfixed"]   # 人物入画 → 配额过


def test_arc_quota_parse_and_wiring():
    """解析: 补列表提取 (含 >6 截断 …); 接线: 前端弧级条目挂 🔧。"""
    t = v3shots._arc_quota_targets(
        {"弧级": ["分区人物配额: 场A1[hook] 8镜仅3镜有人物 (需≥5) — 补 s3/s4/s5/s6/s7",
                 "另一条无补指令的弧级问题"]})
    assert sorted(t) == ["s3", "s4", "s5", "s6", "s7"]
    assert all("人物镜" in v[0] for v in t.values())
    assert v3shots._arc_quota_targets({"弧级": ["无补指令"]}) == {}
    js = open("web/v3/shots.js", encoding="utf-8").read()
    assert "data-fix" in js and "b.dataset.fix.split('/')" in js   # 弧级多镜合投
    assert "弧级配额点名镜" in js                                    # 一键修全部并入


# ── 一键拆行 ───────────────────────────────────────────────────────

def _over_doc():
    """L1 = 78 字超长行 → 单行槽 17.3s 超限 (ep7 s24 原文)。"""
    long_text = ("十一项打勾：会做什么，不会做什么，客户需做，不需做，分析边界，"
                 "不希望介入领域，工作计划，沟通方式周期，费用结构，违约条款，"
                 "交付节点，验收标准，保密范围")
    doc = _doc(line_text=long_text)
    # 重装包出真实超限槽
    shots, warns = v3shots.pack_shots(doc["v3"]["arcs"], doc["v3"]["lines"])
    doc["v3"]["shots"] = shots
    doc["v3"]["shots"][0]["keyframe_zh"] = PEN_KF
    return doc, warns


def test_pack_single_long_line_over_limit():
    doc, warns = _over_doc()
    assert any("超 H3" in w for w in warns)


def test_split_slot_line_surgically(monkeypatch):
    """拆行: 祸源行对半 → 行重编号 → 弧段平移 → 未变镜原句保留 → 新槽 LLM 补。"""
    doc, _ = _over_doc()
    # LLM 补句 mock (新槽画面句)
    from app.services import llm_service

    def fake_chat(sys_p, prompt, **kw):
        import json
        import re
        sids = re.findall(r"^(s\d+)", prompt, flags=re.M)
        return json.dumps({"shots": [{"sid": s, "keyframe_zh": CLEAN_KF}
                                     for s in sids]})
    monkeypatch.setattr(llm_service.get_llm_service().__class__, "chat",
                        staticmethod(fake_chat), raising=False)
    v3 = doc["v3"]
    n_lines_before = len(v3["lines"])
    r = v3shots.split_slot_line(doc, "s1")
    # 行数 +1, 重编号连续
    assert len(v3["lines"]) == n_lines_before + 1
    assert [ln["no"] for ln in v3["lines"]] == list(range(1, n_lines_before + 2))
    # 弧段平移: A1 含祸源行 → end+1
    assert v3["arcs"][0]["no_end"] == 3 and v3["arcs"][0]["no_start"] == 1
    # 超限槽消失
    assert not any(float(s["est_s"]) > v3shots.SLOT_MAX_S
                   for s in v3["shots"])
    assert not any("超 H3" in w for w in r["warnings"])
    # 新槽画面句已补 (LLM mock)
    assert all(s.get("keyframe_zh") for s in v3["shots"])
    assert r["split"]["halves"][0].endswith(("，", "。", "；", "："))


def test_split_halves_rules():
    h = v3shots._split_halves("前半句有八个字以上。后半句也有八个字以上")
    assert h and len(h[0]) >= 8 and len(h[1]) >= 8
    assert v3shots._split_halves("无标点文本一二三四五六七八九十") is None
    assert v3shots._split_halves("短。") is None


def test_arc_quota_hint_split_by_cast():
    """1109 修: 点名镜所在弧无绑定 cast → hint 改指「泛称人物, 禁用卡名」
    (旧 hint 教 LLM 写卡名 = 反向闸拦 ↔ 配额再拦, 永久乒乓无解)。"""
    t = v3shots._arc_quota_targets(
        {"弧级": ["分区人物配额: 场A1[mid] 8镜仅3镜有人物 (需≥5) — 补 s3/s4"]},
        {"s3": ["老谭"], "s4": []})
    assert "卡名+定妆锚" in t["s3"][0]
    assert "禁用任何圣经卡名" in t["s4"][0]
    # 缺省 (不传 cast 表) = 旧调用面兼容, 仍给可执行出路
    t2 = v3shots._arc_quota_targets(
        {"弧级": ["分区人物配额: 场A1[mid] 8镜仅3镜有人物 (需≥5) — 补 s9"]})
    assert "泛称人物" in t2["s9"][0]
