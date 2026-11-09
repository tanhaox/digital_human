# -*- coding: utf-8 -*-
"""1109 双老谭根修: 在场形态感知 — ③ 局部在场语法 (袖口/背影/塔尖小点) 与
FACE_LAW "正面+全身+头顶不裁" 冲突 → 模型两边都画 = 同角色双入境
(ep7 s11/s13/s23/s26/s27/s28/s31 实锤); s31 型手部镜被判空镜 → person 负词
硬补幻影人。形态律替换 + 局部在场计入人物 + 锚句挂限定 + 兜底替身跳过。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import k2, k2_lang

# ep7 实弹 keyframe 精缩 (形态判据的目标形状)
KF_S11 = ("老谭深红色冲锋衣的袖口与前臂垂在画面一侧，浅灰西装的观众替身·技术专家"
          "停在脚印旁，微微侧身低头。")
KF_S31 = ("一副素净卡片整齐堆叠成扇形，一双穿深红色冲锋衣袖口的手从画面左下侧伸入，"
          "指尖悬在卡片边缘前；另一双穿浅灰色西装袖口的手从右上侧探入画面边缘。")
KF_S26 = "老谭穿深红色冲锋衣的背影贴着塔壁，一只手正搭在砖缝边缘，绿色旅行双肩背包垂在身后。"
KF_S23 = "阿特金森深炭色西装身影背对画面驻足板前，老谭深红色冲锋衣身影在远端侧立。"
KF_S27 = "塔顶的老谭穿深红色冲锋衣、背绿色旅行双肩背包，身形只是塔尖上的一个小点。"
KF_S28 = "塔顶老谭穿深红色冲锋衣的短发身影小如黑点，稳稳立在最高一层砖沿上。"
KF_S3 = "书桌桌面近景静物，六张空白无字的厚卡纸呈扇形摊开，一条银灰金属链条从卡片边角孔洞中穿过。"
KF_FULL = "老谭穿深红色冲锋衣站在长桌尽头，正脸看向镜头，全身入画。"

_STYLE = {"style_prompt_head": "FULLHEAD", "style_prompt_tail": "TAIL"}


def _en(monkeypatch, kf_zh: str, scene: str = "an EN scene") -> str:
    monkeypatch.setattr(k2_lang, "style_head_en", lambda bt: "HEAD-EN")
    monkeypatch.setattr(k2_lang, "style_tail_en", lambda bt: "TAIL-EN")
    return k2.build_prompt(scene, "测试书", style=dict(_STYLE), page="k2",
                           lang="en", zh_scene=kf_zh,
                           cast_names=("背影", "老谭", "阿特金森"))


# ── 形态检测 ───────────────────────────────────────────────────────

def test_presence_form_classifies_ep7():
    assert k2._presence_form(KF_S11) == "partial"      # 袖口+前臂
    assert k2._presence_form(KF_S31) == "partial"      # 袖口的手 (零卡名)
    assert k2._presence_form(KF_S26) == "back"         # 背影 (带手词, back 优先)
    assert k2._presence_form(KF_S23) == "back"         # 背对画面
    assert k2._presence_form(KF_S27) == "distant"      # 塔尖小点
    assert k2._presence_form(KF_S28) == "distant"      # 小如黑点
    assert k2._presence_form(KF_S3) == ""              # 纯空镜 → 无形态
    assert k2._presence_form(KF_FULL) == ""            # 全身在场面 → FACE_LAW 原路


def test_partial_presence_counts_as_person():
    """s31 实锤: 局部在场=有人 (曾判空镜 → person 负词逼出幻影人);
    孤立手部特写 (批A-3 判据) 不受扰动。"""
    assert k2._scene_has_person(KF_S31, "测试书") is True
    assert k2._scene_has_person("一只男性的手把便签纸摊开在桌面上", "测试书") is False
    assert k2._scene_has_person(KF_S3, "测试书") is False


# ── 律替换 (EN 产线路) ────────────────────────────────────────────

def test_en_law_swapped_by_form(monkeypatch):
    out_partial = _en(monkeypatch, KF_S11)
    assert "Presence form" in out_partial
    assert "exactly once" in out_partial
    assert "full figure stays in frame" not in out_partial   # FACE_LAW 已替换

    out_back = _en(monkeypatch, KF_S26)
    assert "painted from behind" in out_back
    assert "never a pure back view" not in out_back          # 正面条款撤下

    out_distant = _en(monkeypatch, KF_S27)
    assert "small scale" in out_distant
    assert "full figure stays in frame" not in out_distant

    # 全身在场面: FACE_LAW 原路不动
    out_full = _en(monkeypatch, KF_FULL)
    assert "full figure stays in frame" in out_full
    assert "Presence form" not in out_full


def test_zh_law_swapped_by_form():
    _cast = ("背影", "老谭", "阿特金森")
    out = k2.build_prompt(KF_S26, "测试书", style=dict(_STYLE), page="k2",
                          cast_names=_cast)
    assert "在场形态" in out and "按背影呈现" in out
    assert "不画纯背影" not in out                             # zh FACE_LAW 撤下
    out_full = k2.build_prompt(KF_FULL, "测试书", style=dict(_STYLE), page="k2",
                               cast_names=_cast)
    assert "不画纯背影" in out_full                            # 全身镜原路


# ── look 锚形态限定 + 兜底替身跳过 ─────────────────────────────────

def test_anchor_carries_form_qualifier(monkeypatch):
    """形态镜: look 锚句尾挂限定 (锚管服装身份, 不管在场形态 —
    锚自带「表情沉稳自信」全身语义 = 双入境副因)。"""
    monkeypatch.setattr(k2, "_char_looks",
                        lambda bt: [("老谭", "中年男性，穿深红色冲锋衣，表情沉稳自信", [])])
    monkeypatch.setattr(k2_lang, "char_look_en",
                        lambda nm, lk: ("Lao Tan", "a middle-aged man in a red windbreaker"))
    monkeypatch.setattr(k2, "_bible_hue", lambda bt: "")
    monkeypatch.setattr(k2, "main_color_tint", lambda h, lang="zh": "")
    shot = {"keyframe_zh": KF_S11, "keyframe_en": "a close view of footprints"}
    out = k2._scene_eff(shot, "测试书")
    assert "appears only as the described body parts" in out
    # 全身镜锚不带限定
    shot2 = {"keyframe_zh": KF_FULL, "keyframe_en": "a man stands by the table"}
    out2 = k2._scene_eff(shot2, "测试书")
    assert "appears only as the described body parts" not in out2


def test_standin_not_prepended_for_partial(monkeypatch, tmp_path):
    """s31 型 (零卡名+局部在场): 不前置全身替身 look — 全身锚召唤完整人形。"""
    monkeypatch.setattr(k2, "_char_looks", lambda bt: [])
    monkeypatch.setattr(k2, "_bible_hue", lambda bt: "")
    monkeypatch.setattr(k2, "main_color_tint", lambda h, lang="zh": "")
    from app.services.anim_pipeline import assembler, director2
    bb = tmp_path / "bb.json"
    bb.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(director2, "_bible_cache_path", lambda bt: bb)
    monkeypatch.setattr(assembler, "_standin",
                        lambda b: ("观众替身·技术专家", "皮肤词 look"))
    monkeypatch.setattr(k2_lang, "standin_look_en", lambda sl: "standin look en")
    shot = {"keyframe_zh": KF_S31, "keyframe_en": "two hands entering the frame"}
    out = k2._scene_eff(shot, "测试书")
    assert "standin look en" not in out                      # 兜底跳过
    # 对照: 无形态的零身份人群镜, 兜底照旧 (1026 判据不动)
    shot2 = {"keyframe_zh": "职场人物在办公桌前讨论", "keyframe_en": "people at desks"}
    out2 = k2._scene_eff(shot2, "测试书")
    assert "standin look en" in out2
