# -*- coding: utf-8 -*-
"""1108 批A: K2 角色外观锚 — look 正向前置 (EN 路) + cast 反外观负词派生.

用户令: 眼镜/胡子跨镜随机漂移, 弧内不连续。根因=EN 路"look 已内联"假设在 v3
断裂 (writer keyframe_en 零外观词) + 负词通道无角色级条目 (s6 K2 图胡茬实锤)。"""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline import k2, k2_lang

# ── 测试圣经 (临时缓存桩: 不落盘, 直写 _CHAR_LOOKS/_BIBLE_NEG 进程缓存) ──
_BOOK = "测书-外观锚"
_CHARS = [
    ("老谭", "中年男性，短发37分头，穿深红色冲锋衣，表情沉稳自信，下巴光洁无胡须"),
    ("大卫·梅斯特", "中年白人男性，金丝眼镜，藏青色西装，下巴光洁无胡须"),
    ("无面人", "穿灰色斗篷的神秘身影"),  # look 无胡须条款也无眼镜 — 不派生任何负词
]


def _setup(monkeypatch):
    monkeypatch.setitem(k2._CHAR_LOOKS, _BOOK,
                        [(n, lk, []) for n, lk in _CHARS])
    monkeypatch.setattr(k2, "bible_negative", lambda bt: "")


def test_cast_negative_rules(monkeypatch):
    _setup(monkeypatch)
    # 无胡须 + 无眼镜角色 (老谭): 全派生
    assert k2._cast_negative("老谭站在山脊上", _BOOK) == \
        "beard, mustache, stubble, facial hair, glasses"
    # 戴镜角色 (大卫·梅斯特): 只派生胡子负词, 不派生 glasses (不误伤)
    assert k2._cast_negative("大卫·梅斯特递过文件夹", _BOOK) == \
        "beard, mustache, stubble, facial hair"
    # 双角色同镜: 合并去重
    assert k2._cast_negative("老谭与大卫·梅斯特对视", _BOOK) == \
        "beard, mustache, stubble, facial hair, glasses"
    # look 无两条款的角色: "不提眼镜=默认无眼镜" 仍派生 glasses (无害默认,
    # look 是唯一事实源, 要戴镜必须写进 look)
    assert k2._cast_negative("无面人走过", _BOOK) == "glasses"
    # 未出场角色 / 空源: 零派生
    assert k2._cast_negative("一条山路", _BOOK) == ""
    assert k2._cast_negative("", _BOOK) == ""


def test_negative_prompt_cast_append_and_byte_stability(monkeypatch):
    _setup(monkeypatch)
    base = k2._negative_prompt(_BOOK, "k2", "a man on a ridge")
    # zh_scene 空 = 与旧版字节一致 (不扰动既有渲染指纹)
    assert k2._negative_prompt(_BOOK, "k2", "a man on a ridge", zh_scene="") == base
    # 出场老谭 → cast 负词追加在尾部
    with_cast = k2._negative_prompt(_BOOK, "k2", "a man on a ridge",
                                    zh_scene="老谭站在山脊上")
    assert with_cast.startswith(base)
    assert with_cast.endswith("beard, mustache, stubble, facial hair, glasses")


def test_scene_eff_en_look_prefix(monkeypatch):
    """v3 形状镜 (keyframe_zh 含卡名 + keyframe_en 无外观词) → look_en 前置。"""
    _setup(monkeypatch)
    monkeypatch.setattr(k2_lang, "char_look_en",
                        lambda nm, lk: ("Lao Tan" if nm == "老谭" else nm,
                                        "middle-aged man, short side-parted hair, "
                                        "red hiking jacket, clean-shaven face, "
                                        "no glasses"))
    monkeypatch.setattr(k2, "main_color_tint", lambda hue, lang="zh": "")
    monkeypatch.setattr(k2, "_bible_hue", lambda bt: "")
    shot = {
        "keyframe_zh": "大场景：山脊，老谭坐在巨岩边缘眺望云海",
        "keyframe_en": "wide shot: a rocky ridge above a sea of clouds",
    }
    out = k2._scene_eff(shot, _BOOK)
    assert out.startswith("Lao Tan (")           # look 前置
    # 1108 批A-2: 面部锚必须领括号内第一位 (胡茬残留实锤: 埋尾部权重不足)
    assert out.startswith("Lao Tan (clean-shaven face, no beard, "
                          "no mustache, no stubble; no glasses;")
    assert "clean-shaven" in out                 # 无胡须锚进 EN
    assert "no glasses" in out                   # 无眼镜锚进 EN
    assert "wide shot: a rocky ridge" in out     # 原句不丢
    # 戴镜角色: 面部锚=clean-shaven + wearing glasses (不含 no glasses 误伤)
    shot2 = {
        "keyframe_zh": "大卫·梅斯特递过文件夹",
        "keyframe_en": "a man hands over a folder",
    }
    out2 = k2._scene_eff(shot2, _BOOK)
    assert out2.startswith("David Meister (clean-shaven face, no beard, "
                           "no mustache, no stubble; wearing glasses;") \
        or out2.startswith("大卫·梅斯特 (")  # fail-open 退中文时 name 保留
    # 无卡名镜: 不前置 (字节不扰动)
    out2 = k2._scene_eff({"keyframe_zh": "纯山路空镜", "keyframe_en": "an empty trail"},
                         _BOOK)
    assert out2 == "an empty trail"


def test_char_look_en_failopen(monkeypatch):
    """LLM 翻译失败 → fail-open 退 (中文名, 中文 look), 不炸渲染。"""
    def _boom(*a, **kw):
        raise RuntimeError("llm down")
    import app.services.anim_pipeline.llm as _llm
    monkeypatch.setattr(_llm, "chat_json", _boom)
    ne, le = k2_lang.char_look_en("老谭", "短发，无胡须")
    assert ne == "老谭" and le == "短发，无胡须"


# ── 1108 批A-3: 空镜防人 (风格头正向召唤 chibi 人物实锤, 9/9 空镜冒人) ──

def _setup_bible(monkeypatch, tmp_path):
    """圣经桩: _person_in 的卡名判定走 _bible_cache_path 磁盘读 — 指向临时文件。"""
    import json as _json
    from app.services.anim_pipeline import director2 as d2
    bb = tmp_path / "bible.json"
    bb.write_text(_json.dumps(
        {"characters": [{"name": c[0]} for c in _CHARS]}), encoding="utf-8")
    monkeypatch.setattr(d2, "_bible_cache_path", lambda bt: bb)


def test_scene_has_person_hand_strip(monkeypatch, tmp_path):
    """手部剥词判据: 只写手≠人物在场; 背影人物/卡名不受剥词影响。"""
    _setup_bible(monkeypatch, tmp_path)
    # 手部镜 (s51/s52/s54/s55 型): 剥后无人物 → False
    assert k2._scene_has_person("一只男性的手把便签纸摊开在桌面上", _BOOK) is False
    assert k2._scene_has_person("那只手执笔悬在笔记本上方", _BOOK) is False
    assert k2._scene_has_person("一只手正把行动册插回书架", _BOOK) is False
    # 背影人物 (s53 型): 人物词不在手部短语内 → True
    assert k2._scene_has_person(
        "一个短发中年男性的背影立在落地窗前", _BOOK) is True
    # 卡名 (s1/s30 型): 剥手部短语不剥卡名 → True (走 cast 通道)
    assert k2._scene_has_person("老谭的手握着笔", _BOOK) is True
    # 纯空镜 / 空串
    assert k2._scene_has_person("特写：摊开的厚重书籍占据画面中心", _BOOK) is False
    assert k2._scene_has_person("", _BOOK) is False


_STYLE = {"style_prompt_head": "FULLHEAD", "style_prompt_tail": "TAIL",
          "style_prompt_head_empty": "EMPTYHEAD"}


def test_empty_head_swap_and_byte_stability(monkeypatch, tmp_path):
    """空镜换头: 字段存在+零人物 → 换空镜变体头; 人物镜/字段缺 = 全头。"""
    _setup_bible(monkeypatch, tmp_path)
    out_empty = k2.build_prompt("特写：摊开的书籍，泛黄书页", _BOOK,
                                style=dict(_STYLE), page="k2")
    assert out_empty.startswith("EMPTYHEAD") and "FULLHEAD" not in out_empty
    # 人物镜 (卡名 / 背影): 不换头
    assert k2.build_prompt("老谭站在山脊上", _BOOK,
                           style=dict(_STYLE), page="k2").startswith("FULLHEAD")
    assert k2.build_prompt("中年男性的背影立在窗前", _BOOK,
                           style=dict(_STYLE), page="k2").startswith("FULLHEAD")
    # 字段缺失: 空镜也回退全头 (他书字节零扰动)
    _noempty = {k: v for k, v in _STYLE.items() if k != "style_prompt_head_empty"}
    assert k2.build_prompt("特写：摊开的书籍，泛黄书页", _BOOK,
                           style=dict(_noempty), page="k2").startswith("FULLHEAD")
    # EN 路 + zh_scene 中文源 (v3 路形状: scene_zh=EN eff) — 判据吃中文源
    monkeypatch.setattr(k2_lang, "style_head_en", lambda bt: "FULLHEAD-EN")
    monkeypatch.setattr(k2_lang, "style_head_empty_en", lambda bt: "EMPTYHEAD-EN")
    monkeypatch.setattr(k2_lang, "style_tail_en", lambda bt: "TAIL-EN")
    assert k2.build_prompt("close-up of an open old book", _BOOK,
                           style=dict(_STYLE), page="k2", lang="en",
                           zh_scene="特写：一本边缘磨损的旧书摊开"
                           ).startswith("EMPTYHEAD-EN")
    # EN 路人物镜 (背影): 全头
    assert k2.build_prompt("a back view of a man at the window", _BOOK,
                           style=dict(_STYLE), page="k2", lang="en",
                           zh_scene="中年男性的背影立在窗前"
                           ).startswith("FULLHEAD-EN")


def test_negative_prompt_empty_guard(monkeypatch, tmp_path):
    """空镜防人负词: 书级风格带空镜变体头才执法 (数据闸); 手部镜不追加。"""
    _setup(monkeypatch)
    _setup_bible(monkeypatch, tmp_path)
    monkeypatch.setattr(k2, "resolve_style",
                        lambda bt: {"style_prompt_head_empty": "空头"})
    # 纯空镜 → 追加防人负词
    out = k2._negative_prompt(_BOOK, "k2", "an open book close-up",
                              zh_scene="特写：摊开的厚重书籍")
    assert out.endswith(k2._EMPTY_NEG)
    # 手部镜 (剥词后无人物) → 同样防整人
    out_hand = k2._negative_prompt(_BOOK, "k2", "a hand over a notebook",
                                   zh_scene="那只手执笔悬在笔记本上方")
    assert out_hand.endswith(k2._EMPTY_NEG)
    # 人物镜 (卡名) → 不追加; cast 负词基线仍在
    out_p = k2._negative_prompt(_BOOK, "k2", "a man on a ridge",
                                zh_scene="老谭站在山脊上")
    assert "human silhouette" not in out_p
    assert "glasses" in out_p
    # 数据闸: 风格无空镜变体头字段 → 不追加 (他书字节零扰动)
    monkeypatch.setattr(k2, "resolve_style", lambda bt: {})
    out_gate = k2._negative_prompt(_BOOK, "k2", "an open book close-up",
                                   zh_scene="特写：摊开的厚重书籍")
    assert "human silhouette" not in out_gate
