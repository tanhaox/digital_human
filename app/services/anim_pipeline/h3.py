# -*- coding: utf-8 -*-
"""H3 批生视频 — 节拍式提示词壳 (用户最爱视频 f8050007 配方, 2026-09-11 用户画检认证).

图 = fl2va pruned int8 + v4_step600 turbo 4步 sigma, 1024 最长边, 无音频, 热 ~102s/10s.
提示词壳 = 用户终版胜出结构 (比我 0911 单拍实证版高一档):
  首帧对齐行 + Identity lock + 屏显例外 + Physical lock 全程段
  + 多拍时间轴 (每拍 "continuing from the previous beat" 衔接, 每拍句尾无人声否定)
  + soundscape 静音底噪 (不是 N/A) + music N/A
拍结构由规划器产出 (beats), 壳由代码拼 — LLM 只填内容不发明结构.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from . import brand as brand_mod
from . import (
    comfy,
    video_qc,  # 0927 P1-B 视频产物机检 (观察模式)
)
from . import ledger as _ledger
from . import shots as shots_mod
from .config import load, resolve_style

logger = logging.getLogger(__name__)

# 全程生命条款 (0915 三层模型定稿: 第2层动画层 = 已有物品尽管动, 纹理稳定防抖)
# 旧版"无风无粒子"是吉普力水彩全屏抖动的补丁, 误伤赛璐璐物品动效 — 改为动效+纹理分离
# 1012 修: 旧尾句硬编码"flat cel-shaded / clean line art"赛璐璐质感 — 对流体/3D 等
# 非赛璐璐书反着说 (逼模型把流体帧拗成平涂), 改为 Picture 1 中性锚; 各书质感由
# h3_style (风格锚) + motion_style (动画律) 承担
# 1012 修2 (国旗实锤): 旧示例清单里的 "flags waving" 是教模型加旗的邀请函 —
# 城市场景中国旗/美国国旗全是它招来的 (0915 起每条视频都带); 换中性示例 + 禁新增物件
# 1016 修 (s02/s04/s06/s07 全员被框进家居环境实锤): 旧示例清单里 curtains
# swaying 等具象名词 = 画物邀请函 (同 1012 修2 flags waving 家族) — 模型为了
# "让窗帘摆" 会把外景隐喻场景重建成有窗帘的室内。改纯中性表述, 零示例枚举。
AMBIENT_LIFE = (
    "Objects already visible in Picture 1 may move naturally in place, consistent "
    "with what they are. Colors, shading and surface textures remain exactly as in "
    "Picture 1; no texture shimmer. The video adds NOTHING that is not visible in "
    "Picture 1: no new object, scenery element, decoration or written mark of any "
    "kind appears at any moment — only what is already visible in the first frame "
    "may move."
)
# 文字层承接 (0915 定稿: 中文优先 + 字体纪律): 首帧全灭字不变; 动画中文字出场 —
# 关键词用中文短词(2-4字), 数字/年份/标志保持数字与拉丁字母; 每镜一种艺术字体风格
# (书法/手写/复古装饰/描边立体), 禁平淡黑体; 落点避让主体不压字。
# 0915 s02 招牌幻觉首例已记录观察 (复发率采样中, 用户令暂不修 — docs 观察 ledger);
# 若复发率坐实再上反幻觉条款。
TEXT_POLICY = (
    "The first frame contains no text. AT MOST ONE text element may appear in the whole "
    "video, only as specified in the beats: render exactly the specified words, large and "
    "bold. Chinese text is limited to very short words (up to 4 characters); longer Chinese "
    "phrases are NEVER rendered in-video (they are added in post-production). Digits, "
    "years and brand logos stay as digits/Latin. Two text elements NEVER coexist or "
    "overlap in the same shot. "
    "Picture-1 text freeze (1015, s07 实锤: 合成文字在动画中劣化): any text or characters "
    "already visible in Picture 1 stay pixel-frozen and completely unchanged for the whole "
    "video — never redraw, morph, wobble or degrade them; they are locked artwork, not "
    "animation targets. "
    "Use ONE consistent artistic typography style (calligraphy, brush, retro decorative, "
    "or outlined dimensional lettering) — never plain sans-serif/heiti. "
    "Position the text event inside the blank zone reserved for it in Picture 1 "
    "(e.g. the clean space above the character's head), never covering faces or key objects. "
    "All in-video Chinese characters MUST be Simplified Chinese (简体字) exactly as "
    "specified in the beats; Traditional Chinese glyphs (繁體) are FORBIDDEN — they "
    "deform and break during animation. "
    "Anti-hallucination: surfaces that would normally carry text — signboards, door "
    "plates, billboards, screens, cards, newspapers — must remain exactly as blank as in "
    "Picture 1; never invent, add, or fill in any text, characters, numbers, or glyphs "
    "anywhere. No captions, watermarks, or UI."
)
def _de_traditional(s: str) -> str:
    """traditional → hand-drawn (0919 隐形bug: 风格词被视频模型读成繁体中文)。

    1016 修3: 净化点从"拼装完的整段出口"上收到各动态注入行 — 出口全文扫曾误伤
    TEXT_POLICY 反繁体禁令本体 ("Traditional Chinese glyphs ... FORBIDDEN")。"""
    return re.sub(r"(?i)\btraditional\b", "hand-drawn", s)


# 1016 修 (ep4 全镜大量乱码中文实锤): 无上屏字的镜 (h3_text_event/in_image_text
# 皆无) 走整段硬禁字 — 旧版对这类镜也枚举 20~29 个动作散文汉字当
# "permitted glyphs, copied stroke-for-stroke", 等于请模型画字。
NO_TEXT_POLICY = (
    "Hard no-text rule: this video contains NO text of any kind — no Chinese "
    "characters, no letters, no digits, no logos, no captions, no subtitles, no "
    "UI, no writing-like marks on any surface. Surfaces that would normally "
    "carry text — signboards, door plates, billboards, screens, cards, "
    "newspapers, phone or computer displays — stay completely blank or purely "
    "pictorial, exactly as in Picture 1; never invent, add or fill in any "
    "glyph. If a text-like mark happens to exist in Picture 1 it stays "
    "pixel-frozen and unchanged for the whole video. The Chinese words in this "
    "description are scene directions only, never on-screen text."
)


SOUNDSCAPE = (
    "Silent video, ambient room tone only at a barely audible level. "
    "No human voice of any kind."
)


def style_h3(book_title: str = "", page: str = "") -> str:
    """H3 风格锚 — 从书级风格配方读 (风格随书变, 单一事实源同 K2).

    0919 隐形bug根治: "Traditional ... style" 会被模型读成 Traditional Chinese —
    中文文字出场漂成繁体, 繁体笔画动画易错 (用户实锤)。出口统一净化为 hand-drawn。
    1007 修 (s22 首帧跳+风格变实锤): page="k2" 时读 page_overrides 的 h3_style —
    旧版读 base "Abstract fluid neon" → H3 用抽象霓虹风格动画, K2 用卡通风格出图,
    两层风格割裂 = 视频完全跳离首帧。"""
    st = resolve_style(book_title)
    if page and isinstance(st.get("page_overrides"), dict):
        ov = st["page_overrides"].get(page) or {}
        if ov.get("h3_style"):
            return re.sub(r"(?i)\btraditional\b", "hand-drawn", str(ov["h3_style"]))
    s = st.get("h3_style") or (
        "1988 hand-drawn cel animation style, clean line art, flat vivid colors, sharp cel-shading")
    return re.sub(r"(?i)\btraditional\b", "hand-drawn", s)


def _ts(t: float) -> str:
    """秒 → [00:02.5] 节拍时间戳格式."""
    return f"[00:{t:04.1f}]"


_BIBLE_MOTION: dict[str, tuple[float, str]] = {}  # book_title → (圣经mtime, 动画律)
_BIBLE_ERA: dict[str, tuple[float, str]] = {}    # book_title → (圣经mtime, 时代锚)


def parse_era_anchor(raw: str, segment_after_colon: bool = False) -> str:
    """era_anchor 单一解析口 (1017 W2 归一)。

    默认整串截 300 (H3 场景时代锁语义); segment_after_colon=True 返回冒号后
    服饰段 (assembler 替身 look 派生语义 — 旧版 assembler 本地切, 两处解析
    语义并存实锤, 收口于此)。"""
    raw = str(raw or "").strip()
    if not segment_after_colon:
        return raw[:300]
    for sep in ("：", ":"):
        if sep in raw:
            return raw.split(sep, 1)[1].strip()
    return raw


def era_anchor(book_title: str = "") -> str:
    """圣经时代锚 (1012 场景时代锁): 圣经 era_anchor 字段直读, mtime 守卫。

    治 H3 场景漂移 (国风风格卡带 "ancient Chinese palace setting" → 当代职场书
    视频变古装宫殿+红旗实锤): 风格锚管画法, 时代锚管场景 — 两权分立。"""
    if not book_title:
        return ""
    try:
        from .director2 import _bible_cache_path
        p = _bible_cache_path(book_title)
        mt = p.stat().st_mtime
        hit = _BIBLE_ERA.get(book_title)
        if hit and hit[0] == mt:
            return hit[1]
        raw = str(json.loads(p.read_text(encoding="utf-8"))
                  .get("era_anchor") or "").strip()
        out = raw[:300]
        _BIBLE_ERA[book_title] = (mt, out)
        return out
    except Exception:  # noqa: BLE001 — 无圣经/无字段 = 不注入
        return ""


def motion_style(book_title: str = "") -> str:
    """圣经动画律 (1012 书级运动风格通道, 治 H3 运动随机 — 卓有实锤"液态/雾气"
    即 K2 首帧只锁长相、运动语言无锚, 模型自由发挥): 全程运动语言一句军规,
    进 h3/r2v 双工位提示词。无圣经/空字段 = 原行为零扰动。
    mtime 守卫 — 圣经手填/保存后免重启即生效 (同 k2.bible_negative 口径)。"""
    if not book_title:
        return ""
    try:
        from .director2 import _bible_cache_path
        p = _bible_cache_path(book_title)
        mt = p.stat().st_mtime
        hit = _BIBLE_MOTION.get(book_title)
        if hit and hit[0] == mt:
            return hit[1]
        raw = str(json.loads(p.read_text(encoding="utf-8"))
                  .get("motion_style") or "").strip()
        out = raw[:400]
        _BIBLE_MOTION[book_title] = (mt, out)
        return out
    except Exception:  # noqa: BLE001 — 无圣经/无字段 = 不注入
        return ""


# 1016 修 (s16/17/18/21【王冠宝石】+ s32 无意义中文实锤, 58/62 镜中招): fallback
# 模板把中文场景名写进英文首帧锚 ("scene: 王冠宝石 机械臂") — 视频模型当片头
# 大字/字幕画出来。组装时剥离中文标签段, 场景完全由 Picture 1 承担 (ID-095 军规)。
_SCENE_LABEL_RE = re.compile(r"scene:\s*[一-鿿][^,，]*")


def _strip_scene_label(s: str) -> str:
    return _SCENE_LABEL_RE.sub("scene: exactly as shown in Picture 1", s)


_BIBLE_POLICY: dict[str, tuple[float, str]] = {}  # book_title → (mtime, 角色纪律)


def character_policy(book_title: str = "") -> str:
    """圣经 character_policy 单读口 (1017 无脸根治: 旧版只喂规划 LLM 不进渲染链).

    mtime 守卫同 era_anchor/motion_style — 圣经手改保存即生效。"""
    if not book_title:
        return ""
    try:
        from .director2 import _bible_cache_path
        p = _bible_cache_path(book_title)
        mt = p.stat().st_mtime
        hit = _BIBLE_POLICY.get(book_title)
        if hit and hit[0] == mt:
            return hit[1]
        raw = str(json.loads(p.read_text(encoding="utf-8"))
                  .get("character_policy") or "").strip()
        out = raw[:400]
        _BIBLE_POLICY[book_title] = (mt, out)
        return out
    except Exception:  # noqa: BLE001 — 无圣经/无字段 = 不注入
        return ""


def arc_thesis(doc: dict[str, Any], arc_id: str = "") -> str:
    """弧主旨单读口 (1017 W3, 立意单一源): doc.arcs 的 metaphor_core 优先,
    visual_metaphor 降级 — K2/H3/r2v 各拼一行主旨锚, 组装器不推导。"""
    if not arc_id:
        return ""
    for a in doc.get("arcs") or []:
        if str(a.get("arc_id") or "") == str(arc_id):
            return str(a.get("metaphor_core") or a.get("visual_metaphor") or "").strip()[:120]
    return ""


def build_prompt(anim: dict[str, Any], book_title: str = "", page: str = "",
                 text_event: dict | None = None, in_image_text: list | None = None,
                 thesis: str = "") -> str:
    # 1016 修2: text_event/in_image_text 从镜级直传 — 旧版读 anim.get(...) 永远为空
    # (两字段在镜级, 调用方却只传 shot["anim"]), 事件字/合成字从未真正进过提示词.
    beats = anim["beats"]
    lines = [
        "integrated_multimodal_description:",
        f"[Shot 1] {style_h3(book_title, page=page)}. At 0.00 seconds the frame fully matches Picture 1: "
        f"{_strip_scene_label(_de_traditional(anim['opening_desc'].strip()))}. Identity lock: keep all characters' faces, hairstyles and "
        "outfits consistent with Picture 1 — outfit COLORS stay pixel-faithful to Picture 1 for "
        "every frame: no recoloring, no hue shift, no costume redesign.",
        "",
        f"Physical lock for the whole video: {_de_traditional(anim['physical_lock'].strip())} — "
        "the camera viewpoint stays locked to Picture 1; only a very slight push-in or "
        "pull-back is allowed; strictly no lateral panning or tracking; no large-area "
        "repaint of the scene (no costumes or objects morphing into different shapes).",
        "",
    ]
    # 1017 W3: 弧主旨锚 (立意=圣经+弧主旨 单一源, 组装器只拼不推导)
    if thesis:
        lines.append(f"Narrative anchor: {thesis} — the scene, props and motion all serve this thesis.")
        lines.append("")
    # 1017 无脸根治: 圣经角色纪律进渲染链 (逐字取书, 组装器不推导)
    _cp = character_policy(book_title)
    if _cp:
        lines.append(f"Character law (book bible, mandatory): {_cp}")
        lines.append("")
    # 1012 场景时代锁 (治风格卡场景越权): 风格锚只管画法, 场景/服饰/环境归时代锚
    # 与 Picture 1。**纯正向锚定** — 禁句里点名禁物 (红旗/灯笼/宫殿), 视频模型会把
    # 否定句里的具象词当正向提示用 (negation-induced hallucination, 串旗实锤:
    # 越禁越长)。只描述"该是什么", 永不枚举"不许出现什么"。
    _era = _de_traditional(era_anchor(book_title))
    if _era:
        lines.append(
            f"Scene era lock (book-level, overrides any style wording): {_era} "
            "— costumes, props and environments obey this and Picture 1. The art "
            "style governs rendering texture ONLY; it never re-stages the scene. "
            "Every part of Picture 1 stays itself for the whole video: the same "
            "setting, the same objects and the same background as the first "
            "frame; nothing new is added at any moment.")
        lines.append("")
    # 1012 书级动画律 (治运动随机): 全程运动语言锚, 优先级高于单拍措辞 —
    # 规划器 beats 逐镜具体动作不变, 但运动"气质" (液态/雾气/弹性/节奏) 由它钉死
    _motion = _de_traditional(motion_style(book_title))
    if _motion:
        lines.append(
            f"Motion style law for the whole video (book-level animation style): {_motion} "
            "— every beat obeys this motion language; when a beat's wording conflicts "
            "with this law, the law wins.")
        lines.append("")
    lines += [AMBIENT_LIFE, ""]
    # 1016 修 (ep4 全镜大量乱码中文实锤): 文字政策三分支, 白名单只收真正上屏的字。
    # 旧版无分支扫 beats 动作散文, 每镜枚举 20+ 汉字当 "permitted glyphs, copied
    # stroke-for-stroke" — 对模型是画字邀请函 → 每条视频长满乱码中文。
    # ①h3_text_event 有字 → TEXT_POLICY + 事件字规格行 (整条视频唯一文字事件)
    # ②in_image_text 有字 → 只许首帧合成字全程冻结, 不邀请新文字
    # ③两者皆无 (ep4 62/62 镜如此) → 整段硬禁字 NO_TEXT_POLICY
    _ev = text_event if isinstance(text_event, dict) else {}
    _ev_text = str(_ev.get("text") or "").strip()
    _iit = [str(x).strip() for x in (in_image_text or []) if str(x).strip()]
    if _ev_text:
        lines += [TEXT_POLICY, ""]
        _at = _ev.get("at_s")
        _at_txt = f"at {float(_at):.1f}s" if isinstance(_at, (int, float)) else "mid-video"
        _zone = str(_ev.get("zone") or "").strip() or "blank reserved"
        lines.append(
            f"Text event spec: {_at_txt}, in the {_zone} zone of the frame, the exact "
            f"words 「{_ev_text}」 appear large and bold in the artistic typography "
            "described above, then hold steady — this is the one and only text "
            "element of the video.")
        lines.append("")
    elif _iit:
        lines.append(
            "The ONLY text in this video is the text already composed into Picture 1 ("
            + "".join(f"「{x}」" for x in _iit)
            + ") — it stays pixel-frozen and completely unchanged for the whole video: "
            "never redrawn, morphed, wobbled or degraded; locked artwork, not an "
            "animation target. No other text or writing-like mark appears anywhere.")
        lines.append("")
    else:
        lines.append(NO_TEXT_POLICY)
        lines.append("")
    # 0919 字级钉死 (s66 实锤: 简体令后仍漂「誰」): 真正上屏的字逐个圈出点名照抄 —
    # 1016: 来源 = 事件字 + 合成字 (镜级直传); beats 动作散文汉字一律不进白名单
    _cjk: list[str] = []
    for _src in [_ev_text, *_iit]:
        for _ch in _src:
            if "一" <= _ch <= "鿿" and _ch not in _cjk:
                _cjk.append(_ch)
    if _cjk:
        lines.append(
            "Glyph lock: the ONLY Chinese glyphs permitted in this video are these exact "
            f"simplified characters, copied stroke-for-stroke: {'、'.join(_cjk[:64])}. "
            "Write 谁 with the simplified speech radical 讠 (never 誰), 买 not 買, "
            "钱 not 錢, 书 not 書, 门 not 門. A single traditional glyph ruins the frame.")
        lines.append("")
    # 1017 W2 S1 (K2/H3 视觉统一加强): 冻结瞬间军规 — 全镜通用锚, 不依赖
    # beats_enrich 覆盖。治 beats 与图冻结瞬间错位 (s09 实锤: 图里卡已举在
    # 手上, beats 写"伸手捏起" → 模型重排画面换构图)。
    lines.append(
        "Continuity law: all motion continues from the exact frozen pose of "
        "Picture 1 — never rewind the action, never pre-stage a pose or object "
        "that is not already visible in the first frame.")
    lines.append("")
    for i, b in enumerate(beats):
        motion = _de_traditional(b["motion"].strip())
        ending = _de_traditional((anim.get("ending") or "").strip())
        if i == len(beats) - 1 and ending:
            # 规划器常把结尾复述进末拍, 已含则不重复追加
            if ending[:60].lower() not in motion.lower():
                motion = f"{motion} {ending}"
        chain = "" if i == 0 else "continuing from the previous beat — "
        lines.append(
            f"From {_ts(float(b['t_start']))} to {_ts(float(b['t_end']))}, "
            f"{chain}{motion} No human voice of any kind."
        )
    lines += [
        "",
        "overall_soundscape:",
        SOUNDSCAPE,
        "",
        "non_diegetic_music:",
        "N/A",
    ]
    # 1016 修3: traditional 净化已上收到各动态注入行 (_de_traditional) — 旧版出口
    # 全文扫误伤 TEXT_POLICY 本体 ("Traditional Chinese glyphs ... FORBIDDEN" 被改成
    # "hand-drawn ... FORBIDDEN", 反繁体禁令自废)
    return "\n".join(lines)


def build_workflow(image_name: str, anim: dict[str, Any], seed: int, prefix: str,
                   book_title: str = "", qc_history: list | None = None,
                   text_event: dict | None = None, in_image_text: list | None = None,
                   thesis: str = "") -> dict[str, Any]:
    """H3 I2V 工作流 (API 格式), 节点 ID 对齐最爱视频真相图 (102.4s/10s).

    qc_history (1004 修): 该镜既往机检判决 (乱码/崩坏史) → 重 roll 时注入
    对症禁令锚 (glyph lock 之上的第二道伪字防线 — 判决回流防复发)。"""
    _prompt = build_prompt(anim, book_title, page="k2",  # 1007: H3 与 K2 同风格 (page 隔离)
                           text_event=text_event, in_image_text=in_image_text,  # 1016 修2: 镜级直传
                           thesis=thesis)  # 1017 W3: 弧主旨锚
    if qc_history:
        _kinds = {str(r.get("kind") or r.get("what") or "") for r in qc_history}
        if any("乱码" in k for k in _kinds):
            _prompt += (
                "\n\nPseudo-text ban (this shot previously generated garbled pseudo-glyphs — "
                "hard rule): the frame must contain NO "
                "writing-like marks: no invented characters, no smeared or broken glyph shapes, "
                "no unreadable script on any sign, board, screen, or surface. Render every "
                "sign/board/book/screen as a blank or purely pictorial surface.")
        if any("崩坏" in k for k in _kinds):
            _prompt += (
                "\n\nCorruption ban (this shot previously produced corrupted frames — hard rule): "
                "keep every frame fully coherent; no black screens, no noise mosaics, no torn or "
                "smeared regions, no melted limbs; hold the exact art style of Picture 1.")
        if any("红旗" in k for k in _kinds):
            _prompt += (
                "\n\nObject ban (this video previously added objects that do not exist in the first frame — hard rule): "
                "rooftops, buildings, windows and skylines contain NO flags, NO banners, NO red "
                "streamers or pennants of any kind; they stay exactly as in Picture 1. "
                "Character clothing colors are not flags and stay as Picture 1.")
        if any("衣色" in k or "漂移" in k for k in _kinds):
            _prompt += (
                "\n\nCostume color lock (this video previously recolored the protagonist's "
                "clothes — hard rule): every character's outfit color stays pixel-faithful to "
                "Picture 1 for every frame; no recoloring, no hue shift, no redesign.")
    return {
        "129": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "minimax_it2v\\minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "weight_dtype": "default"}},
        "348": {"class_type": "ModelAttentionBackend", "inputs": {
            "attention": "comfy kitchen attention", "model": ["129", 0]}},
        "244": {"class_type": "MiniMaxLowVRAMAttention", "inputs": {
            "head_chunks": 10, "model": ["348", 0]}},
        "243": {"class_type": "MiniMaxChunkFeedForward", "inputs": {
            "chunks": 2, "seq_threshold": 4096, "model": ["244", 0]}},
        "341": {"class_type": "Power Lora Loader (rgthree)", "inputs": {
            "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
            "lora_1": {"on": False, "lora": "H3\\minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "strength": 1},
            "lora_2": {"on": False, "lora": "H3\\NaughtyTimes_pruned_r128_v2.safetensors", "strength": 0.8},
            "lora_3": {"on": True, "lora": "H3\\minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors", "strength": 1},
            "lora_4": {"on": False, "lora": "H3\\MiniMax H3  AI Girl Fictional Women Series30.safetensors", "strength": 1},
            "lora_5": {"on": False, "lora": "H3\\H3_Motion_Booster.safetensors", "strength": 0.2},
            "lora_6": {"on": False, "lora": "H3\\HMNSFW_AIO_V2.safetensors", "strength": 0.3},
            "lora_7": {"on": False, "lora": "H3\\VBVR_H3_attn_only.safetensors", "strength": 0.5},
            "➕ Add Lora": "", "model": ["243", 0]}},
        "130": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "minimax\\qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "type": "minimax", "device": "default"}},
        "121": {"class_type": "VAELoader", "inputs": {
            "vae_name": "minimax\\minimax_h3_video_vae_fp16.safetensors"}},
        "122": {"class_type": "VAELoader", "inputs": {
            "vae_name": "minimax\\minimax_h3_audio_vae_fp32.safetensors"}},
        "114": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "136": {"class_type": "LayerUtility: ImageScaleByAspectRatio V2", "inputs": {
            "aspect_ratio": "original", "proportional_width": 1, "proportional_height": 1,
            "fit": "crop", "method": "lanczos", "round_to_multiple": "32",
            "scale_to_side": "longest", "scale_to_length": 1024,
            "background_color": "#000000", "image": ["114", 0]}},
        "187": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": _prompt}},
        "135": {"class_type": "PrimitiveFloat", "inputs": {"value": float(anim["duration_s"])}},
        "134": {"class_type": "ComfyMathExpression", "inputs": {
            "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
            "values.a": ["135", 0]}},
        "133": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["130", 0], "vae": ["121", 0], "prompt": ["187", 0],
            "first_frame": ["136", 0], "width": ["136", 3], "height": ["136", 4],
            "length": ["134", 1]}},
        "128": {"class_type": "BasicGuider", "inputs": {
            "model": ["341", 0], "conditioning": ["133", 0]}},
        "131": {"class_type": "RandomNoise", "inputs": {
            "noise_seed": seed, "control_after_generate": "fixed"}},
        "221": {"class_type": "MiniMaxH3TurboSampler", "inputs": {}},
        # 1016 修 (s17 中段漂移+抖动实锤): v4-600 turbo 官方步档 4-8, 低步+大动态
        # 产拖影/鬼影 (Larryvrh README: "Using 6-8 steps largely removes it") —
        # 旧 7 条目手写表实为 6 步, 机械臂高速旋转镜中段漂移+高频抖动实锤。
        # 换 BasicScheduler simple 8 步 (官方推荐档), 时长 +~30%/镜。
        "361": {"class_type": "BasicScheduler", "inputs": {
            "model": ["341", 0], "scheduler": "simple", "steps": 8,
            "denoise": 1.0}},  # 本机 ComfyUI 必填 (漏参 HTTP 400 实锤)
        "127": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["131", 0], "guider": ["128", 0], "sampler": ["221", 0],
            "sigmas": ["361", 0], "latent_image": ["133", 1]}},
        "124": {"class_type": "VAEDecode", "inputs": {"samples": ["127", 0], "vae": ["121", 0]}},
        "138": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["124", 0], "frame_rate": 24, "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4", "pix_fmt": "yuv420p",
            "crf": 19, "save_metadata": True, "trim_to_audio": False,
            "pingpong": False, "save_output": True}},
    }


def run_shot(shot: dict[str, Any], doc: dict[str, Any]) -> Path:
    """跑单镜 H3: 上传 K2 图 → I2V → mp4 move 到 anim/{shot_id}.mp4, 状态 → anim_done.

    0916 生成时长=语音长度铁律 (用户实锤: 旧 beats 出长片 → 装配截前段 →
    尾部文字/视觉事件陪葬 + 白烧 GPU): 开跑前强制 beats=槽 (漂移自动拉齐).
    1007 用户令(首帧压不住): shot.skip_first_frame=True 时跳 K2 图锚定,
    H3 纯 t2v 从 motion 描述自由生成 — 适用于 opening_desc 与 K2 图严重
    失配的镜 (s24 金球 vs 书籍实锤)。"""
    import time as _t
    _t0 = _t.monotonic()
    base_dir = shots_mod.ep_dir(doc["book_title"], doc["ep"])
    _span = float(shot["t_end"]) - float(shot["t_start"])
    _anim = shot.get("anim") or {}
    if isinstance(_anim, dict) and _span > 0 and abs(
            float(_anim.get("duration_s") or 0) - _span) > 0.05:
        shots_mod.stretch_beats(shot, min(round(_span, 2), 10.0))
        logger.warning("[h3] %s beats 漂移已拉齐到槽 %.2fs (生成时长=语音长度)",
                       shot["shot_id"], min(round(_span, 2), 10.0))
    dest = base_dir / "anim" / f"{shot['shot_id']}.mp4"
    prefix = f"video/anim/{shots_mod._safe(doc['book_title'])}/ep{doc['ep']}/{shot['shot_id']}"

    if shot.get("skip_first_frame"):
        # t2v 模式: 生成中性灰底图代替 K2 图 — i2v 管线兼容, H3 从 prompt 自由生成
        from PIL import Image as _PILImage
        _placeholder = base_dir / "img" / f"{shot['shot_id']}_t2v_anchor.png"
        if not _placeholder.exists():
            _PILImage.new("RGB", (1280, 720), (64, 64, 72)).save(_placeholder)
        image_name = comfy.upload_image(_placeholder)
        logger.info("[h3] %s 跳首帧锚定 (中性底图, H3 从 prompt 自生成)", shot["shot_id"])
        _thesis = arc_thesis(doc, str(shot.get("arc_id") or ""))
        wf = build_workflow(image_name, shot["anim"], shot["h3_seed"], prefix,
                            book_title=doc["book_title"],
                            qc_history=(shot.get("qc_rejects") or None),
                            text_event=shot.get("h3_text_event"),
                            in_image_text=shot.get("in_image_text"), thesis=_thesis)
    else:
        src_img = base_dir / shot["image_file"]
        if not src_img.exists():
            raise comfy.ComfyRunError(f"首帧图不存在: {src_img}")
        image_name = comfy.upload_image(src_img)
        _thesis = arc_thesis(doc, str(shot.get("arc_id") or ""))
        wf = build_workflow(image_name, shot["anim"], shot["h3_seed"], prefix,
                            book_title=doc["book_title"],
                            qc_history=(shot.get("qc_rejects") or None),
                            text_event=shot.get("h3_text_event"),
                            in_image_text=shot.get("in_image_text"), thesis=_thesis)
    # 10s 热机 ~102s, 冷机翻倍, 留余量; 0919 实锤: 长镜 (6.9-8.8s) 在低显存节点
    # 帧数放大超线性, 600s 定额连杀 s11/s12/s21 两轮 (ComfyUI 实际都跑完了,
    # 产物躺 output 成孤儿) — 超时按时长伸缩
    # 0920 ep5 s41 实锤: 同尺寸 latent 一次显存布局病爬到 29min (1740s) 后自愈,
    # 后续同尺寸 52s — 爬行是暂态不是固有; 90/s 系数 (1069s) 必杀暂态爬行,
    # 提到 240/s (5.2s 镜 → 1852s) 让一次性爬行能自完成, 少一轮重试孤儿
    _cfg = load()
    # 0926 修: 软超时按时长伸缩 — 硬超时 0920 起 240/s 伸缩, 软超时漏同款; 长镜 (6.5s
    # ≈150+帧) 合法生成即超 180s, 被平额软超时当"卡住"掐了 (ep2 s15/s38 二连败实锤,
    # 3.5min 处 execution_interrupted); 短镜仍守 180s 判卡看门狗 (0921 用户令不弱化)
    comfy.run_workflow(wf, dest=dest, timeout_sec=600 + int(240 * min(_span, 10.0)),
                       label=f"h3 {shot['shot_id']}",
                       soft_timeout_sec=max(_cfg.comfy.soft_timeout_sec,
                                            int(90 * min(_span, 10.0)) + 120),
                       interrupt_grace_sec=_cfg.comfy.interrupt_grace_sec)
    shot["video_file"] = f"anim/{shot['shot_id']}.mp4"
    # 0916 错配溯源: 生成时文案指纹 (归真/重规划改写 narration 后, 旧视频=内容错配
    # — 拆镜父镜继承实锤; 草稿装配对不上指纹即亮灯)
    import hashlib as _h
    shot["gen_narr_sha"] = _h.md5(
        shots_mod.norm_for_match(shot.get("narration") or "").encode("utf-8")).hexdigest()[:10]
    # 0927 P1-B 视频产物机检 (观察模式, video_qc): 补 H3 出片后零画面验证盲区 —
    # 只标记 video_suspect 交人检, 不阻断 anim_done (text_suspect 同构)
    # 1004 修 (ep5 s03 实锤): ①新片先清旧标记 (重 roll 干净后 🎥 残留误导人检)
    # ②判决进 qc_rejects 历史 — 重 roll 时 build_workflow 注入对症禁令锚 (沉淀防复发)
    shot.pop("video_suspect", None)
    _sus = video_qc.check(dest, shot, doc)
    if _sus:
        shot["video_suspect"] = _sus
        # 1017 W5: 视频闸判决 → 书级负词沉淀回路 (fail-open)
        from . import neg_learn
        neg_learn.record_verdict(str(doc.get("book_title") or ""),
                                  ",".join(_sus.get("kinds") or []))
        _h = shot.setdefault("qc_rejects", [])
        _h.append({"kind": ",".join(_sus.get("kinds") or []),
                   "what": str(_sus.get("what") or "")[:60],
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        del _h[:-8]
        logger.warning("[video_qc] %s %s %s", shot["shot_id"],
                       _sus.get("kinds"), _sus.get("what"))
    shot["attempts"]["h3"] += 1
    shot["error"] = None
    shots_mod.transition(shot, "anim_done")
    _ledger.record(doc["book_title"], doc["ep"], "h3", shot["shot_id"],
                   trigger=str(doc.get("_ledger_trigger") or ""),
                   ms=int((_t.monotonic() - _t0) * 1000))
    return dest


def _pick_video_station(shot: dict[str, Any]) -> str:
    """路由: 按镜选 r2v 或 h3 (1009 工位矩阵路由表).

    人物镜 (keyframe 含圣经角色名) → r2v (身份零漂+成片率高+中文稳)
    人形道具镜 (keyframe 含雕塑/人偶/画像) → h3 (r2v会赋活)
    空镜 → h3 (身份无关, h3快)
    """
    from . import r2v
    kf = str(shot.get("keyframe_zh") or "")

    # 人形道具优先判死 → h3
    if any(w in kf for w in ("雕塑", "人偶", "画像", "雕像", "模型人", "镜中影")):
        return "h3"

    # 人物镜: keyframe 命中圣经角色名
    char_map = r2v._char_sheet_map(shot.get("_book_title") or "")
    if any(name in kf for name in char_map):
        return "r2v"

    return "h3"


def run_batch(doc: dict[str, Any], only: set[str] | None = None, retry_failed: bool = False,
              on_shot_done: Any = None, stop_check: Any = None) -> dict[str, int]:
    """批跑 approved 镜 (--retry-failed 连 anim_fail 一起重试, 换 h3_seed).

    on_shot_done/stop_check 同 k2.run_batch (服务层进度 + 协作取消).
    品牌卡镜零 GPU 直通: video_file=定稿品牌卡 mp4, 状态直接 anim_done.
    """
    ok = fail = 0
    pending = sum(
        1 for s in doc["shots"]
        if (only is None or s["shot_id"] in only)
        and (s["status"] == "approved" or (retry_failed and s["status"] == "anim_fail"))
    )
    done = 0
    for shot in doc["shots"]:
        if only is not None and shot["shot_id"] not in only:
            continue
        runnable = shot["status"] == "approved" or (retry_failed and shot["status"] == "anim_fail")
        if not runnable:
            continue
        if stop_check and stop_check():
            logger.info("[h3] 协作取消: 已处理 %d/%d, 停止", done, pending)
            break
        if shot["status"] == "anim_fail":
            shots_mod.audit_reroll(doc, shot, "anim_fail重试")
            shots_mod.retry_anim(shot)
        try:
            if shot.get("brand_card"):
                # 0920 定稿: 品牌卡镜一律零 GPU 直通插定稿资产 (用户令修"品牌卡
                # 被对齐冲掉"死循环). 旧 0916 山脊骨架例外 (骨架注入→逐镜生成) 已废:
                # 槽长差额由草稿端仪式句锚定+首尾定格吸收, 品牌镜不再烧 GPU.
                shot["video_file"] = brand_mod.resolve(doc["book_title"], doc["ep"], "video")
                shot["error"] = None
                shots_mod.transition(shot, "anim_done")
                logger.info("[h3] %s 品牌卡直通 (零 GPU, 插定稿卡)", shot["shot_id"])
            else:
                # 1009 工位路由: 人物镜→r2v(身份零漂), 空镜/道具镜→h3
                from . import r2v as _r2v_mod
                for s in doc["shots"]:
                    s["_book_title"] = doc["book_title"]
                station = _pick_video_station(shot)
                if station == "r2v":
                    _r2v_mod.run_shot(shot, doc)
                    logger.info("[r2v] %s done (工位路由→r2v)", shot["shot_id"])
                else:
                    run_shot(shot, doc)
                    logger.info("[h3] %s done (工位路由→h3)", shot["shot_id"])
                # 1006 用户令(爬行根治): 镜间释放显存 — H3 模型栈 ~46GB/49GB,
                # 连跑 2-3 镜后碎片化 → 换页爬行(70s/步 vs 正常10s/步);
                # 镜间 /free 清干净再重载(~60s), 每镜总时 ~3min vs 爬行 ~12min = 4x 提速
                from . import comfy as _comfy_mod
                _comfy_mod.free_memory()
                logger.info("[h3] %s 镜间显存已清 (防碎片爬行)", shot["shot_id"])
            ok += 1
        except Exception as exc:  # noqa: BLE001 — 单镜失败不断批, 降级=静态图已在位
            fail += 1
            shot["error"] = f"h3: {exc}"[:500]
            shots_mod.transition(shot, "anim_fail")
            logger.error("[h3] %s 失败(降级用静态图): %s", shot["shot_id"], exc)
        shots_mod.save(doc)
        done += 1
        if on_shot_done:
            try:
                on_shot_done(shot["shot_id"], done, pending)
            except Exception:
                logger.warning("[h3] 进度回调异常", exc_info=True)
    return {"ok": ok, "fail": fail}
