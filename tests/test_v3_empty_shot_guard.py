# -*- coding: utf-8 -*-
"""1109 令53 空镜冒人三连根修 — 测试钉死.

s3 实锤: 空镜画面句写「模糊人影」+「一支笔搁在」→ H3 把剪影实体化成 2D 路人
执笔书写 (beats "blurred figures flicker" = 正向人物动画指令 + 人物锁全撤)。
三件: ①③/④/redo 提示词同尺禁人物残留/搁笔 ②H3 空镜 EN 剥词
③人物判定源并入 v3 cast+背影 (乔伊盲区)。
"""
from app.services.anim_pipeline import h3, k2
from app.services.anim_pipeline.v3_pipeline import direct as v3direct
from app.services.anim_pipeline.v3_pipeline import redo as v3redo
from app.services.anim_pipeline.v3_pipeline import shots as v3shots

# s3 原句形 (空镜 + 人影 + 搁笔 三毒俱全)
S3_KF = ("空无一人的会议室一角，空白白板与一张起伏的波形图静物并置，波形图纸面没有任何文字；"
         "一支笔搁在波形图下方，背景磨砂玻璃隔断后透出模糊人影与冷白灯光，"
         "前景桌面反光里映出波形轮廓，画面采用大场景俯视，主体波形纸只占一角。")


# ── ① 提示词三处同尺 ────────────────────────────────────────────────

def test_rule16_empty_shot_no_person_residue():
    """③ _SYS 规则16: 空镜禁人影/剪影/背影/路人 + 禁搁笔 + 环境暗示替代。"""
    assert "空镜禁人物残留与搁笔" in v3shots._SYS
    for w in ("人影", "剪影", "背影", "路人", "行人"):
        assert w in v3shots._SYS
    assert "搁笔" in v3shots._SYS and "书写邀请" in v3shots._SYS
    assert "空着的椅子" in v3shots._SYS          # 环境暗示替代示例
    assert "17. 每条只输出画面句" in v3shots._SYS  # 格式行顺移


def test_redo_sys_same_ruler():
    """♻️ _REDO_SYS 规则11 与 ③ 规则16 同尺。"""
    assert "空镜禁人物残留与搁笔" in v3redo._REDO_SYS
    assert "人影" in v3redo._REDO_SYS and "搁笔" in v3redo._REDO_SYS
    assert "空椅" in v3redo._REDO_SYS


def test_direct_motion_no_person_injection():
    """④ _SYS 规则6: 画面句没人的镜 motion 禁添人物 (人影晃过/剪影移动/路人走过)。"""
    assert "禁添人物" in v3direct._SYS
    assert "人影晃过" in v3direct._SYS


# ── ② 确定性执法 ────────────────────────────────────────────────────

def test_text_layer_issues_flags_s3_signature():
    """s3 原句形 → 空镜残留人物词 + 空镜搁笔 双命中。"""
    iss = v3shots.text_layer_issues(
        [{"sid": "s1", "arc_id": "A1", "est_s": 6, "keyframe_zh": S3_KF}])
    joined = " ".join(iss.get("s1") or [])
    assert "空镜残留人物词 (人影)" in joined
    assert "空镜搁笔" in joined and "书写邀请" in joined


def test_text_layer_issues_person_shot_exempt():
    """人物镜 (宪法人物词在场) 不触发空镜残留检查。"""
    kf = ("穿深灰色西装的男子背影立在巨大的落地窗前，双手扶着窗框，窗外城市天际线"
          "在暮色里亮起点点灯火，室内只有一盏台灯的暖光打在他的侧脸上，中景构图。")
    iss = v3shots.text_layer_issues(
        [{"sid": "s2", "arc_id": "A1", "est_s": 6, "keyframe_zh": kf}])
    joined = " ".join(iss.get("s2") or [])
    assert "空镜残留人物词" not in joined
    assert "空镜搁笔" not in joined


def test_empty_shot_issues_wired_with_book_title():
    """调用方带 book_title (圣经角色名进判定) — redo/③ 两处接线。"""
    src_shots = open("app/services/anim_pipeline/v3_pipeline/shots.py",
                     encoding="utf-8").read()
    assert 'text_layer_issues(shots, book_title=str(job.get("book_title")' in src_shots
    src_redo = open("app/services/anim_pipeline/v3_pipeline/redo.py",
                    encoding="utf-8").read()
    assert 'text_layer_issues(shots, book_title=str(job.get("book_title")' in src_redo


# ── ③ H3 空镜 EN 剥词 (s3 兜底) ─────────────────────────────────────

def _anim(figures: bool = True) -> dict:
    od = ("A corner of an empty meeting room, a blank whiteboard and a still life "
          "of a waveform chart are juxtaposed"
          + (", blurred figures and cold white light show through behind a frosted "
             "glass partition" if figures else "")
          + ".")
    beats = [{"t_start": 0, "t_end": 6,
              "motion": ("The empty meeting room corner remains still, the undulating "
                         "lines on the waveform paper slowly settle"
                         + (", and blurred figures flicker behind the frosted glass."
                            if figures else "."))}]
    return {"opening_desc": od, "physical_lock": "still-life tabletop scene",
            "beats": beats, "duration_s": 6}


def test_h3_prompt_strips_figure_words_for_empty_scene():
    """空镜判定 → opening_desc/beats 里 figures 剥成 shapes (不上桌)。"""
    p = h3.build_prompt(_anim(), zh_scene=S3_KF)   # S3_KF 无人物词 → 空镜
    assert "figures" not in p and "silhouette" not in p.lower()
    assert "blurred shapes" in p


def test_h3_prompt_keeps_figures_for_person_scene():
    """人物镜 (背影在 extra_names) → 剥词不生效, 人物版 Identity lock 恢复。"""
    p = h3.build_prompt(_anim(), zh_scene="乔伊的背影立在屏幕间隙后，",
                        cast_names=("背影", "乔伊"))
    assert "figures" in p                        # 人物镜原文保留
    assert "keep all characters' faces" in p     # 人物版 identity lock (非中性版)
    p2 = h3.build_prompt(_anim(), zh_scene=S3_KF)  # 对照: 空镜 = 中性版
    assert "keep all characters' faces" not in p2


def test_hand_seed_still_stripped_chained():
    """旧 _HAND_SEED_RE 剥词与令53 figures 剥词链式共存。"""
    a = _anim()
    a["opening_desc"] = ("A corner of an empty meeting room, a man's hand rests "
                         "beside blurred figures on the table.")
    p = h3.build_prompt(a, zh_scene=S3_KF)
    assert "a man's hand" not in p and "the hand" in p
    assert "figures" not in p


# ── ④ 判定源并入 v3 cast + 背影 ─────────────────────────────────────

def test_v3_person_extras_union():
    """v3 doc → (背影 + cast 名单 str/dict 双形态); v2 doc → ()。"""
    v3doc = {"v3": {"arcs": [{"cast": ["老谭", {"zh": "乔伊"}]},
                             {"cast": []}]}}
    assert k2._v3_person_extras(v3doc) == ("背影", "老谭", "乔伊")
    assert k2._v3_person_extras({"book_title": "x"}) == ()
    assert k2._v3_person_extras(None) == ()


def test_scene_has_person_with_extras():
    """extra_names 命中即人物 (乔伊的背影 不再判空镜)。"""
    assert k2._scene_has_person("乔伊的背影立于屏幕间隙后", "", ("背影", "乔伊"))
    assert not k2._scene_has_person("空无一人的会议室一角", "", ("背影", "乔伊"))


def test_render_chain_passes_extras():
    """run_shot 双分支接线 (k2/H3 两渲染器): extras 从 doc 提取注入。"""
    src_h3 = open("app/services/anim_pipeline/h3.py", encoding="utf-8").read()
    assert "_v3_person_extras(doc)" in src_h3
    assert src_h3.count("cast_names=_extras") == 2      # skip_first_frame + 常规两分支
    src_k2 = open("app/services/anim_pipeline/k2.py", encoding="utf-8").read()
    assert "_extras = _v3_person_extras(doc)" in src_k2
    assert "zh_scene=_zh_src, cast_names=_extras" in src_k2


def test_build_prompt_bible_name_still_primary():
    """圣经角色名 (v2 老路) 不受 extras 影响 — 有 title 时照常判人物。"""
    assert k2._scene_has_person("老谭把扳手搁在工具箱上", "值得信赖的顾问", ())
