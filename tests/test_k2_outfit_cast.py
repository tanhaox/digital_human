# -*- coding: utf-8 -*-
"""1109 演员列表感知 (用户令: K2 写提示词时不知道有演员列表):
③ writer 局部镜惯用服装色指代在场者 (s31「深红色冲锋衣袖口的手」=老谭) —
旧机制只认卡名, 服装指代零锚 → 模型自由造人/复制主角。
服装签名定角 (圣经 look 提签名 → 撞型替身优先) + 人物镜选角律。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import k2, k2_lang

# 圣经九卡精缩 (真实 look 的服装签名段)
CARDS = [
    ("老谭", "中年男性，穿深红色冲锋衣，蓝灰色防风裤，配背绿色旅行双肩背包", ["深红色冲锋衣"]),
    ("大卫·梅斯特", "中年白人男性，金丝眼镜，藏青色西装配深蓝领带", ["藏青色西装"]),
    ("阿特金森", "中年白人男性，深炭色正装西装配暗红色领带", ["深炭色正装西装"]),
    ("外科医生", "中年白人男性，白大褂配手术帽", ["白大褂"]),
    ("查理·格林", "青年白人男性，浅灰西装配白衬衫，右手握笔", ["浅灰西装"]),
    ("观众替身·技术专家", "青年男性，穿浅灰西装配白衬衫不打领带", ["浅灰西装"]),
]

KF_S31 = ("一副素净卡片整齐堆叠成扇形，一双穿深红色冲锋衣袖口的手从画面左下侧伸入，"
          "指尖悬在卡片边缘前；另一双穿浅灰色西装袖口的手从右上侧探入画面边缘。")


# ── 服装签名定角 ──────────────────────────────────────────────────

def test_outfit_cast_s31_two_hands_two_actors():
    """s31 实弹: 红冲锋衣手→老谭; 浅灰西装撞型 (查理/替身) → 替身优先。"""
    out = k2._outfit_cast(KF_S31, CARDS)
    assert [n for n, _ in out] == ["老谭", "观众替身·技术专家"]


def test_outfit_cast_unique_and_no_false_positive():
    # 唯一签名命中
    assert [n for n, _ in k2._outfit_cast("一位穿藏青色西装的男子立在门边", CARDS)] \
        == ["大卫·梅斯特"]
    assert [n for n, _ in k2._outfit_cast("白大褂身影匆匆穿过走廊", CARDS)] == ["外科医生"]
    # 服装不在演员列表 (黑色外套无卡) → 不猜
    assert k2._outfit_cast("桌面上摊着一件黑色外套", CARDS) == []
    assert k2._outfit_cast("", CARDS) == []


def test_outfit_cast_excludes_already_named():
    """卡名已锚定 (s5: 老谭在句) → 服装定角不重复补同名卡。"""
    out = k2._outfit_cast("老谭深红色冲锋衣袖口与前臂从画面下方伸入", CARDS,
                          already=("老谭",))
    assert [n for n, _ in out] == []


def test_outfit_cast_ambiguous_without_standin_skips():
    """撞型且无替身候选 → 不猜 (UNKNOWN 原则)。"""
    cards = [("甲", "穿藏青色西装", ["藏青色西装"]), ("乙", "藏青色西装", ["藏青色西装"])]
    assert k2._outfit_cast("穿藏青色西装的身影立在窗前", cards) == []


# ── 端到端: _scene_eff 锚定 + 选角律 ──────────────────────────────

_STYLE = {"style_prompt_head": "FULLHEAD", "style_prompt_tail": "TAIL"}


def test_scene_eff_outfit_anchors_with_form_qualifier(monkeypatch):
    """s31 手部镜: 两只手各挂所属演员锚 + 局部形态限定 (锚管身份不管形态)。"""
    monkeypatch.setattr(k2, "_char_looks", lambda bt: CARDS)
    monkeypatch.setattr(k2_lang, "char_look_en",
                        lambda nm, lk: (nm, f"look of {nm}"))
    monkeypatch.setattr(k2, "_bible_hue", lambda bt: "")
    monkeypatch.setattr(k2, "main_color_tint", lambda h, lang="zh": "")
    shot = {"keyframe_zh": KF_S31, "keyframe_en": "two hands entering the frame"}
    out = k2._scene_eff(shot, "测试书")
    assert "老谭 (" in out and "观众替身·技术专家 (" in out
    assert out.count("appears only as the described body parts") == 2
    # 空镜 (无人物) 不定角
    shot2 = {"keyframe_zh": "桌面上摊开一本厚重手册", "keyframe_en": "a manual on a desk"}
    out2 = k2._scene_eff(shot2, "测试书")
    assert "老谭 (" not in out2


def test_casting_law_line(monkeypatch):
    monkeypatch.setattr(k2_lang, "style_head_en", lambda bt: "HEAD-EN")
    monkeypatch.setattr(k2_lang, "style_tail_en", lambda bt: "TAIL-EN")
    # 人物镜: 选角律上桌 (EN + zh 两路)
    out_en = k2.build_prompt("a man at a desk", "测试书", style=dict(_STYLE),
                             page="k2", lang="en",
                             zh_scene="穿深红色冲锋衣的身影立在桌旁",
                             cast_names=("老谭",))
    assert "no people beyond the characters described" in out_en
    out_zh = k2.build_prompt("穿深红色冲锋衣的身影立在桌旁", "测试书",
                             style=dict(_STYLE), page="k2", cast_names=("老谭",))
    assert "选角" in out_zh
    # 空镜不挂
    out_empty = k2.build_prompt("an open book close-up", "测试书",
                                style=dict(_STYLE), page="k2", lang="en",
                                zh_scene="特写：摊开的厚重书籍")
    assert "no people beyond" not in out_empty


def test_outfit_cast_hooded_jacket_1109_look():
    """1109 老谭形象定稿: 酒红色连帽冲锋衣 (含连帽修饰) 也要提到签名;
    开衫入类目 (观众替身·女 derive 版曾用针织开衫)。"""
    cards = [("老谭", "中年男性，深棕带灰挑染蓬松短发，穿酒红色连帽冲锋衣"
                      "（拉链半开），灰蓝色宽松工装长裤，背军绿色大容量户外登山双肩包",
              []),
             ("观众替身·女", "青年女性，齐肩直发，穿米色针织开衫内搭白色衬衫", [])]
    assert [n for n, _ in k2._outfit_cast(
        "一只穿酒红色连帽冲锋衣袖口的手从画面边缘探入", cards)] == ["老谭"]
    assert [n for n, _ in k2._outfit_cast("穿米色针织开衫的身影立在窗前", cards)] \
        == ["观众替身·女"]


# ── 1109 子串修: 卡名命中长名优先+掩蔽 ────────────────────────────
def test_name_hits_longest_first_masks_substring():
    """「大卫」是「大卫·梅斯特」的子串 — 同一处提及只计长名一次; 独立短名仍命中。"""
    cards = [("大卫", "中年男性，穿棕色皮夹克", []),
             ("大卫·梅斯特", "中年白人男性，金丝眼镜，藏青色西装配深蓝领带", []),
             ("大卫·福克", "中年白人男性，深棕色西装配金色领带", [])]
    names = [c[0] for c in k2._name_hits(
        "大卫·梅斯特立在白板前，大卫翻开笔记本。", cards)]
    assert names == ["大卫·梅斯特", "大卫"]
    assert [c[0] for c in k2._name_hits("大卫·梅斯特立在白板前。", cards)] \
        == ["大卫·梅斯特"]


def test_scene_eff_no_double_anchor_on_substring_names(monkeypatch):
    """K2 双锚防护: 句写「大卫·梅斯特」时短卡「大卫」不得再挂一份 look 锚
    (旧裸子串匹配 = 一人两锚, 双入境新源头)。"""
    cards = [("大卫", "中年男性，穿棕色皮夹克", []),
             ("大卫·梅斯特", "中年白人男性，金丝眼镜，藏青色西装配深蓝领带", [])]
    monkeypatch.setattr(k2, "_char_looks", lambda bt: cards)
    monkeypatch.setattr(k2_lang, "char_look_en",
                        lambda nm, lk: (nm, f"look of {nm}"))
    monkeypatch.setattr(k2, "_bible_hue", lambda bt: "")
    monkeypatch.setattr(k2, "main_color_tint", lambda h, lang="zh": "")
    shot = {"keyframe_zh": "大卫·梅斯特站在会议室白板前讲解",
            "keyframe_en": "a man at a whiteboard"}
    out = k2._scene_eff(shot, "测试书")
    assert out.count("look of ") == 1 and "大卫·梅斯特" in out
