# -*- coding: utf-8 -*-
"""K2 批生图 — 2ST 两段式高清配方 (0911 用户 A/B 裁决定稿 B 档: 纯最爱配方).

配方来源 = 用户最爱图 ComfyUI history (prompt_id 0be3129d, 唯一真相, 保存版
工作流已被后续改动污染不可复刻):
  段1 1280×720 euler/simple 7步 cfg=1 带残留噪声停 → bislerp ×1.5 (1920×1080)
  → 段2 10步表 start_at_step=4 精修 → ColorMatchV2 mkl@0.5 拉回段1 防漂色
B 档 (0911 下午 A/B 实测用户画检胜出): 纯中文场景 + 三关键词风格锚
(日系二次元动漫风格/赛璐璐平涂/清晰线稿), 默认挂白鲸记 NSW LoRA @1.0 (0911 用户令).
负面通道无效 (cfg=1 + ConditioningZeroOut), 防文字靠场景空白化设计 + 中文防字尾 + 人检.
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
    prompt_gate,  # 0927 终串机检 (观察模式)
)
from . import ledger as _ledger
from . import shots as shots_mod
from .config import main_color_tint, resolve_style

logger = logging.getLogger(__name__)

# 防字尾 (引擎级禁忌, 不随风格变): K2 全面灭字 — 文字由 H3 视频层/text_layer 补
# 0917 政治敏感画面禁区 (用户令): 地图/旗帜/国徽/领导人画像 — 引擎级物理防尾,
# 词库闸门之外的最后一道, 不依赖 doc 状态
# 0927 换词法 (prompt_gate 反哺, 老李 V6.9.5 实锤: 图像模型对否定不敏感、对词根
# 敏感 — "没有旗帜"的"旗帜"会被当画面词渲染, 三条否定式禁令两轮全被无视):
# 政治类高关联可画概念 → 词根零出现 + 正向民用锚; 灭字句保留 (OCR 守门在, 且
# "空白图形区域"已是正向混合式, 0922 实测有效). 旧否定版存 PROTECT_TAIL_LEGACY.
# 1013 P0: 灭字律迁宪法 text_law.k2_engine_tail (冲突12: 域=k2生成帧, 封面书墙豁免);
# 常量版退为 fallback (宪法缺失时保命), 行为等价。
from . import constitution as CST
from .scene_writer import (
    TEXT_CARRIER_NOUN_RE,  # 1015 H 修复③: 载体名词表同源 (改词表只改 scene_writer)
)

PROTECT_TAIL = CST.text_law("k2_engine_tail") or (
    "。画面完全无文字、无数字、无标识，屏幕只保留纯净的空白图形区域，"
    "招牌牌匾一律空白无字。建筑、器物与服饰均为日常民用样式，"
    "人物着普通便装或故事年代的平民衣着。")
PROTECT_TAIL_LEGACY = ("。画面完全无文字、无数字、无标识，屏幕只保留纯净的空白图形区域，"
                       "招牌牌匾一律空白无字。画面中没有地图、没有旗帜、没有国徽、"
                       "没有军徽警徽党徽帽徽领章，没有军装警服制式服装，"
                       "没有任何政治人物或国家领导人的形象。")
# 风格皮肤随书绑定 (0914 风格随书变令): LoRA/风格锚从 _资产/风格库/ 读, 单一事实源
STYLE = resolve_style  # 延迟到 build 时按书解析


# 构图升级 (0923×3: 英文 BOOST "subject small in frame" 反而缩主体 — 改中文, 对齐角色定妆照成功范式)
COMPOSITION_BOOST = (
    "。画面呈现完整的环境空间，主体人物或物体在画面中占据合适比例，"
    "背景延伸至远方展现广阔视野，前景中景远景三层纵深感清晰可见，"
    "光线从画面深处照射营造空间氛围，整体构图开阔大气"
)

# 皮肤/头发军规 (1009 大坑修, backlog ID-080): 1006 皮肤药"实色哑光"没区分皮肤/
# 头发, 无差别罩住 → K2 整体光滑壳 (塑料感/无发丝, ep2 A1/A2 批实锤)。药限定
# 皮肤, 头发反向锚定"分组雕塑感" (对齐用户认可 style1_2: Pixar 式分组块面高光)。
# 全局注入 page="k2" — 单一事实源, 六张库卡/未来新提案自动生效, 不再逐卡手抄。
# 1013 P0: SSOT=bible.style_prefix (冲突14 域界), 本常量退为宪法 skin_law.k2_fallback。
SKIN_HAIR_LAW = CST.skin_fallback() or (
    "。人物皮肤为实色哑光卡通皮肤（不透明、无镜面高光），"
    "头发为分组雕塑感块面造型，发组之间有明确的高光分区与缝隙，"
    "发丝走向清晰可辨（禁整体光滑塑料壳、禁无细节糊状头发）"
)

# 1014 五官完整性军规 (ep3 批图实锤: s28 无眼/s34 空白脸/s12 缺嘴鼻/s14 小脸):
# 3D C4D 圆润风是残缺脸高发先验, 画面句的面部交代被风格锚压制 — 人物在场时
# 正向锚 + k2 页负词防线双管 (确定性注入, 零 LLM; 跳过逻辑纯 status 制,
# 只影响未来渲染, 存量图零扰动)。
# 1017 无脸根治③: 补朝向 — 旧版只锚完整性, 完整侧脸/背身照样过闸
# (A1/A3/A4 整弧避脸实锤: krea2 互动姿势默认背镜看道具)
FACE_LAW = ("。人物面部五官完整清晰：明确的眼睛、鼻子和嘴巴，表情可辨"
            "（面部绝不空白、不缺五官、不无嘴无眼）；面部朝向镜头方向可见"
            "（正脸或四分之三侧脸, 不画纯背影/背身剪影）；人物完整入画，头顶不被画幅裁切")


def _person_in(scene: str, book_title: str = "") -> bool:
    try:
        if any(w in (scene or "") for w in CST.person_words()):
            return True
    except Exception:  # noqa: BLE001 — 宪法不可读退不锚 (宽进)
        pass
    # 1015 修 (s33/s49/s51 修脸静默跳过实锤): 圣经角色名 (林肯/老谭...) 不在宪法词表 —
    # 与 final_gate 同源补齐 (bible characters 并入)
    if book_title:
        try:
            import json as _json

            from .director2 import _bible_cache_path
            _bb = _json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
            for c in _bb.get("characters") or []:
                nm = str(c.get("name") or "").strip()
                if nm and nm in (scene or ""):
                    return True
        except Exception:  # noqa: BLE001 — 无圣经退词形
            pass
    # 1015 EN 路: 英文画面句的人物词形 (Lincoln/manager/figure...)
    return bool(_re.search(
        r"\b(person|people|man|woman|male|female|figure|character|manager|worker|"
        r"Lincoln|Laotan|colleague|audience|team)\w*", scene or "", _re.I))


import re as _re


def _clean_scene(scene: str) -> str:
    """K2 提示词清洁工 (0923 用户实锤: 产线图与选型板完全不一致):

    ① 剥英文风格前缀 — LLM 在 keyframe 加 "claymation stop-motion style," 与 K2 风格头
      双重声明, 模型被拉向英文黏土微缩解读 (选型板无此前缀, 画面正常);
    ② 删 hex 色值码 — K2 不解析 #E67E50 等技术色号, 纯噪声稀释;
    ③ 删 "1988赛璐璐" 残留 — 旧风格硬编码漏出。
    """
    s = str(scene or "")
    s = _re.sub(r'^(claymation\s+stop-motion\s+style[,，\s]*)', '', s, flags=_re.I)
    s = _re.sub(r'[（(][^)）]*#[0-9A-Fa-f]{6}[^)）]*[)）]', '', s)  # (陶土暖橘#E67E50色) → 删
    s = _re.sub(r'#[0-9A-Fa-f]{6}', '', s)  # 裸 hex
    s = s.replace('1988赛璐璐', '').replace('赛璐璐卡通', '卡通')
    # 0923 用户实锤: "指纹"致微缩感 (K2 真画出手印 = 沙盘模型), 只删词不删句
    for _fp in ('清晰的手工指纹纹理', '手工指纹纹理', '指纹纹理', '指纹痕迹',
                '手工指纹', '可见指纹', '留着清晰的指纹', '表面有指纹'):
        s = s.replace(_fp, '')
    s = s.replace('指纹', '')  # 兜底裸"指纹"
    # 0923: "手工捏制"风格头已有, 画面句不重复 (双重出现强化微缩感)
    s = s.replace('手工捏制的', '').replace('手工捏制', '')
    # 清悬空句: "…留着，" / "…表面有，" (指纹删后残留)
    for _dang in ('留着，', '表面有，', '表面留着，', '留着清晰，'):
        s = s.replace(_dang, '')
    s = _re.sub(r'[，,][\s，,]*[，,]', '，', s)  # 清理连续逗号
    s = _re.sub(r'^[，,]\s*', '', s)  # 行首逗号
    s = _re.sub(r'[。][。]+', '。', s)  # 连续句号
    return s.strip()


def strip_negative(scene: str, negatives: list[str],
                  protect: list[str] | None = None) -> tuple[str, int]:
    """负面词子句剔除 (0927 用户令: 打回直接填负面词, 确定性应用, 不走 LLM).

    K2 负面通道物理无效 (cfg=1 + ConditioningZeroOut) — "应用"= 从画面句里
    删掉含负面词根的子句。换词法逻辑 (模型对否定不敏感、对词根敏感 — 写
    "没有墙"照画墙, 唯一可靠是把"墙"的描述从提示词里拿掉): 子句级删除而非
    词级, 避免残词碎片 (删"墙"留"砖") 反向诱导。
    返回 (新句, 剔除子句数); 负面词覆盖全句时返回 (原句, 0) — 空提示词比
    墙更糟, 此时该手填新描述。
    """
    words = [w.strip() for w in negatives if w and w.strip()]
    if not scene or not words:
        return scene, 0
    parts = [p for p in _re.split(r"[，。；,;、]", scene) if p.strip()]
    # 1013 P0 冲突9 (锚保护): 判决回流删子句不看锚 = 抛光闸被绕过 — protect 集
    # (look 括段片段等) 命中的子句只剔词根不整删, 防把角色/道具锚连根拔。
    prot = [p for p in (protect or []) if p]
    kept = []
    for p in parts:
        if not any(w in p for w in words):
            kept.append(p)
            continue
        if prot and any(a and a in p for a in prot):
            q = p
            for w in words:
                q = q.replace(w, "")
            if len(_re.sub(r"[\s，。；,、]", "", q)) >= 4:
                kept.append(q)  # 词根剔除后子句仍成话 — 保留锚子句
                continue
        # 未受保护/剔除后不成话 → 整子句删除
    removed = len(parts) - len(kept)
    if not kept:
        return scene, 0  # 全覆盖: 不应用 (提示词不能删空)
    out = "，".join(kept).strip("，。 ") + "。"
    return out, removed


def build_prompt(scene_zh: str, book_title: str = "", style: dict | None = None,
                 page: str = "", thesis: str = "", lang: str = "zh") -> str:
    """K2 提示词 — Krea2 官方指南范式 (0914 全网学习定稿):

    [风格定调句(仅头一次)] + [画面自然语言长句] + [构图升级] + [防字句] + [轻量收尾锚]
    - 官方: natural language prompts, long detailed prompts 效果最好; 禁标签堆叠
    - 旧结构头尾双风格块把画面夹中间, 同一风格词出现两次稀释主体权重 — 已废
    - 尾部只留构图/质感轻锚, 不再重复风格描述
    - style (0922 风格选择板): 显式配方覆写, 不走书级绑定 (试镜轮询用)
    - COMPOSITION_BOOST (0923): 三层景深+环境空间感 — 治"物体特写幻灯片"
    - page (1006 用户令: 拆东墙补西墙根治): "k2"/"character"/"" (空=base 原样)
      风格库 page_overrides 按页面覆写 head/tail/LoRA — K2 批要具象场景,
      角色设计台要白底全身, 试渲用 base 原样, 互不污染。"""
    st = dict(style or resolve_style(book_title))
    if page and isinstance(st.get("page_overrides"), dict):
        ov = st["page_overrides"].get(page) or {}
        for k, v in ov.items():
            if v is not None:
                st[k] = v
    # 1017 W7 语言架构 (用户令): K2 产线纯英 — lang='en' 时军规/头尾全走 EN 投影
    # (k2_lang: 翻译层+镜级缓存; 中文单一源字段不动, EN 是渲染投影)
    if lang == "en":
        from . import k2_lang as _kl
        _head = _kl.style_head_en(book_title) or st["style_prompt_head"]
        _scene = _clean_scene((scene_zh or "").strip().rstrip("。"))
        if thesis:
            _scene = _scene + f". Theme anchor: {_kl.thesis_en(book_title, thesis)}"
        _law = _kl.SKIN_HAIR_LAW_EN if page == "k2" else ""
        if page == "k2" and _person_in(_scene, book_title):
            _law += _kl.FACE_LAW_EN
            _cp = _kl.character_policy_en(book_title)
            if _cp:
                _law = _kl.CHAR_POLICY_PREFIX_EN + _cp + _law
        _boost = "" if page == "character" else _kl.COMPOSITION_BOOST_EN
        return (_head + _scene + _boost + _kl.PROTECT_TAIL_EN + _law
                + _kl.style_tail_en(book_title))
    scene = _clean_scene((scene_zh or "").strip().rstrip("。"))
    # 1017 W3: 弧主旨锚 (立意单一源; 组装器只拼不推导)
    if thesis:
        scene = f"{scene}。(主题锚: {thesis} — 画面元素与构图服务此主旨)"
    # 1009 大坑修 (backlog ID-080): 皮肤/头发军规全局化 — 只在 k2 产线配方注入,
    # 单一事实源 (此前散在风格卡 k2 tail, 六卡各抄一份且漏头发)。药限定皮肤,
    # 头发反向锚"分组雕塑感"治塑料感 (1006 皮肤药无差别罩住头发 = 光滑壳实锤)。
    law = SKIN_HAIR_LAW if page == "k2" else ""
    # 1014 五官军规: 人物在场才锚 (纯物件镜锚五官会诱导模型幻觉出人脸)
    if page == "k2" and _person_in(scene, book_title):
        law += FACE_LAW
    # 1013 双人根治: character 页不吃产线景深配方 — "完整的环境空间/背景延伸至
    # 远方/三层纵深感"与定妆照纯白立绘背景对冲, 且给画面塞人塞物的空间理由
    boost = "" if page == "character" else COMPOSITION_BOOST
    return (st["style_prompt_head"] + scene + boost
            + PROTECT_TAIL + law + st["style_prompt_tail"])


# 基础负面词 (1006 蓝皮肤/黑虚空配方, 定稿勿动)
_BASE_NEGATIVE = ("blue skin, glowing skin, transparent skin, black background, "
                  "empty background, flat 2D, blurry, low quality, text, watermark, deformed")

# 1015 多人治理条件化 (ep4 无脸战役定罪): 1006 多人禁令否定词会"渗"进注意力最重的
# 脸部区 (社区已知: genur/dskjal 案例; 本地实验矩阵: LoRA=0+摘禁令=出脸唯一组合) —
# 多人物分镜下模型被迫"去人化"妥协成无脸人偶。改为: 检出多人物镜自动豁免, 单人镜
# (默认) 才挂禁令防 1006 时代的多人漂移。
_CROWD_NEG = ("multiple people, crowd, group, extra person, second person, child, bystander")
_MULTI_PERSON_RE = _re.compile(
    r"两[人个位名]|三[人个位名]|四[人个位名]|几[人位]|众|人群|观众|群像|队伍|团队|同事们|"
    r"员工们|人们|两人|俩|每个隔间|一排.*人|两侧.*人|身旁另|身旁站|旁边站|对面站|围观|"
    # 1015 EN 路: 英文多人词形
    r"\b(two|three|four|several) (men|women|people|persons|figures)|crowd|group of|"
    r"audience|team of|colleagues|onlookers|a row of \w+ figures?", _re.I)
_PLATFORM_NEG: str | None = None

# 角色设定表负词 (1013 双人根治, character 页追加): 基座的 character sheet
# 先验 = 同角色多姿势/多表情网格纵向排列 (林肯x2复制/董事长x3表情变体/
# 霍普金斯x3姿势变体实锤, 全是"同一角色多版本"非两个不同人)。正向侧已改纯
# 单人锚定 (character_sheets._scene), 此处负通道压先验词族 — cfg=2.0 真负通道
# (1006 定档), 只挂 character 页不扰产线/试渲。
_SHEET_NEG = ("character sheet, model sheet, turnaround sheet, character turnaround, "
              "multiple views of same character, same character repeated, duplicate character, "
              "character lineup, several poses, pose variations, expression sheet, expression variants")

# 定妆照比例负词 (1013 二追): 风格头消毒剥尽 Q版/大头词后仍渲 3 头身 — 卡通语境
# 本身向大头漂, 1:7 律 (0928 用户令) 靠负通道补压
_PORTRAIT_NEG = ("big head, oversized head, huge head, chibi proportions, "
                 "super deformed proportions, child proportions, 2 head ratio, 3 head ratio")

# 1014 五官完整性负词 (ep3 s28/s34/s12 实锤: 风格先验渲空白脸/缺嘴脸) — 只挂
# k2 产线页; character/试渲页字节不变。
# 1017 朝向负词 (s01/s02 仰面被翻成背朝下实锤, 用户词单): 完整性之外补朝向 —
# cfg=2.0 真负通道 (1006 用户令), 词直接压失败模式 (背身/后脑勺/180°翻转/解剖扭)。
_FACE_NEG = ("faceless, blank face, featureless face, empty face, missing facial features, "
             "no eyes, eyeless, no mouth, missing mouth, missing nose, "
             "character back only, back of head only, character fully facing away from camera, "
             "head rotated 180, twisted anatomy, deformed limbs, jagged edges")


def _platform_negative() -> str:
    """平台违禁词表 (1008 规层 R6): compliance_visual.json 单一事实源, 进程缓存."""
    global _PLATFORM_NEG
    if _PLATFORM_NEG is None:
        try:
            from app.services.compliance import platform_bans_negative
            _PLATFORM_NEG = ", ".join(platform_bans_negative())
        except Exception:  # noqa: BLE001 — 词库坏不炸渲染, 回落基础配方
            _PLATFORM_NEG = ""
    return _PLATFORM_NEG


_BIBLE_NEG: dict[str, tuple[float, str]] = {}  # book_title → (圣经mtime, 负面词)


def bible_negative(book_title: str) -> str:
    """圣经负面词 (1010 书级美学负通道): negative_keywords 英文逗号表直取.

    逗号切分去空去重截断; mtime 守卫 — 圣经手填/重掷后免重启即生效
    (主色 _BIBLE_HUE 无守卫同款坑, 主色变更仍需重启, 此处不续犯)。"""
    if not book_title:
        return ""
    try:
        from .director2 import _bible_cache_path
        p = _bible_cache_path(book_title)
        mt = p.stat().st_mtime
        hit = _BIBLE_NEG.get(book_title)
        if hit and hit[0] == mt:
            return hit[1]
        _b = json.loads(p.read_text(encoding="utf-8"))
        # 1017 W5: 手填区 (用户主权) + auto 区 (判决沉淀) 合并注入
        raw = str(_b.get("negative_keywords") or "").strip()
        raw_auto = str(_b.get("negative_keywords_auto") or "").strip()
        parts = [w.strip() for w in
                 (raw + "," + raw_auto).replace("，", ",").split(",") if w.strip()]
        neg = ", ".join(dict.fromkeys(parts))[:420]
        _BIBLE_NEG[book_title] = (mt, neg)
        return neg
    except Exception:  # noqa: BLE001 — 无圣经/无字段 = 不注入
        return ""


def _negative_prompt(book_title: str = "", page: str = "", scene: str = "") -> str:
    """负面提示词 = 基础配方 + 平台违禁 + 圣经负面词 (1010 书级美学).

    圣经负面词按书注入 (cfg=2.0 负通道确定性归宿, 零 LLM); 无圣经/空字段 =
    输出与旧版字节一致 — 不扰动既有渲染指纹。
    page (1013 双人根治): "character" = 定妆照页追加 _SHEET_NEG 压角色设定表先验;
    其余页不追加, 字节与旧版一致。
    scene (1015 多人治理条件化): k2 产线页传入画面句 — 检出多人物镜豁免多人禁令
    (无脸战役定罪), 单人镜(默认)挂 _CROWD_NEG 防 1006 多人漂移。"""
    extra = _platform_negative()
    neg = bible_negative(book_title)
    out = _BASE_NEGATIVE + (", " + extra if extra else "")
    out += (", " + neg if neg else "")
    if page == "character":
        out += ", " + _SHEET_NEG + ", " + _PORTRAIT_NEG
    elif page == "k2":
        out += ", " + _FACE_NEG
        if not (scene and _MULTI_PERSON_RE.search(scene)):
            out += ", " + _CROWD_NEG
    return out


def build_workflow(scene_zh: str, seed: int, prefix: str, extra_loras: list[dict[str, Any]] | None = None,
                   book_title: str = "", style: dict | None = None,
                   portrait: bool = False, page: str = "",
                   thesis: str = "", lang: str = "zh") -> dict[str, Any]:
    """2ST 工作流 (API 格式), 节点 ID 对齐 history 真相图. 默认链 = 书级风格配方的 LoRA.

    style (0922): 显式配方覆写 (风格选择板试镜 — 库成员/Kimi 提案逐个出样张).
    portrait (0923 用户令·定妆照): 竖版 9:16 全身像 — 横版 16:9 必裁人物道具.
    page (1006): 页面覆写 ("k2"/"character"/"" = base) — 风格库 page_overrides."""
    st = dict(style or resolve_style(book_title))
    if page and isinstance(st.get("page_overrides"), dict):
        _ov = st["page_overrides"].get(page) or {}
        for _k, _v in _ov.items():
            st[_k] = _v  # 1006: None = 清除 (lora: null → 去 LoRA), 非 None = 覆写
    if portrait:  # 竖版: 潜空间 720×1280 → bislerp×1.5 → 1080×1920; 配方尾 16:9 同步换竖
        st["style_prompt_tail"] = str(st.get("style_prompt_tail") or "").replace("16:9", "9:16竖幅")
    # 无 LoRA 风格 (库 json lora 显式空): 跳过风格 LoRA 节点, 不回落默认 NSW (0922)
    loras = ([{"name": st["lora"], "strength": float(st.get("lora_strength") or 0.8)}]
             if st.get("lora") else []) + list(extra_loras or [])
    prev = ["262", 0]
    lora_nodes: dict[str, Any] = {}
    for i, lora in enumerate(loras):
        nid = "263" if i == 0 else f"263x{i}"
        lora_nodes[nid] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": lora["name"], "strength_model": float(lora["strength"]), "model": prev},
        }
        prev = [nid, 0]

    wf: dict[str, Any] = {
        "262": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        **lora_nodes,
        "258": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "266": {"class_type": "VAELoader", "inputs": {"vae_name": "krea2RealVae_v10.safetensors"}},
        "261": {"class_type": "EmptyLatentImage", "inputs": {
            "width": 720 if portrait else 1280, "height": 1280 if portrait else 720,
            "batch_size": 1}},
        "264": {"class_type": "CLIPTextEncode", "inputs": {"text": build_prompt(scene_zh, book_title, style=st, page=page, thesis=thesis, lang=lang), "clip": ["258", 0]}},
        # 1006 用户令(蓝色皮肤根治): cfg 1.0→2.0 + 真负面提示词替代 ConditioningZeroOut
        # (cfg=1.0 数学上负面通道=零影响力; 2.0 实证蓝→白且伪影少于2.5)
        # 1008 规层 R6: 拼接 platform_bans_negative (compliance_visual.json 单一事实源,
        # 零 LLM 成本 — cfg=2.0 负面通道直接吃)
        # 1010 书级美学: 圣经 negative_keywords 按书追加 (手填即生效, mtime 守卫)
        # 1013 双人根治: character 页追加 _SHEET_NEG (角色设定表先验压制)
        "259": {"class_type": "CLIPTextEncode", "inputs": {
            "text": _negative_prompt(book_title, page, scene_zh), "clip": ["258", 0]}},
        # 段1: 7 步停在带残留噪声的中间态
        "202": {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "enable", "noise_seed": seed, "steps": 7, "cfg": 2.0,
            "sampler_name": "euler", "scheduler": "simple",
            "start_at_step": 0, "end_at_step": 7, "return_with_leftover_noise": "enable",
            "model": prev, "positive": ["264", 0], "negative": ["259", 0], "latent_image": ["261", 0]}},
        "204": {"class_type": "VAEDecode", "inputs": {"samples": ["202", 0], "vae": ["266", 0]}},
        # bislerp ×1.5 潜空间升格 → 1920×1080
        "203": {"class_type": "LatentUpscaleBy", "inputs": {"upscale_method": "bislerp", "scale_by": 1.5, "samples": ["202", 0]}},
        # 段2: 10 步表从 step4 起精修
        "211": {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "enable", "noise_seed": seed, "steps": 10, "cfg": 2.0,
            "sampler_name": "euler", "scheduler": "simple",
            "start_at_step": 4, "end_at_step": 999, "return_with_leftover_noise": "disable",
            "model": prev, "positive": ["264", 0], "negative": ["259", 0], "latent_image": ["203", 0]}},
        "212": {"class_type": "VAEDecode", "inputs": {"samples": ["211", 0], "vae": ["266", 0]}},
        # 段2 高清颜色向段1 锚定 50%
        "253": {"class_type": "ColorMatchV2", "inputs": {
            "method": "mkl", "strength": 0.5, "multithread": True,
            "image_target": ["212", 0], "image_ref": ["204", 0]}},
        "271": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["253", 0]}},
    }
    return wf


def build_workflow_i2i(scene_zh: str, seed: int, prefix: str, ref_image_name: str,
                       *, start_step: int = 2, book_title: str = "",
                       style: dict | None = None, page: str = "",
                       extra_loras: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """i2i 旁路 (0927 反哺 P2-A A1 · opt-in, 主链不接线): 定妆照垫底图.

    复用 build_workflow 全骨架 (2ST/LoRA/ColorMatch 不动), 只替换 latent 源:
    261 EmptyLatentImage → 267 LoadImage(定妆照) + 268 VAEEncode。
    段1 start_at_step=start_step (默认 2/7 ≈ denoise 0.71): 保留定妆照部分
    结构 (角色五官/服饰), 构图让位给画面句。
    extra_loras (1008 工位弹匣槽): 能力弹匣挂载 (如 krea2_identity_edit) —
    与书级风格 LoRA 共存, 逐工位配置。

    ⚠️ 已知矛盾 (试点要验的正是它): 定妆照是竖版 9:16 全身像, VAEEncode 会
    把构图带成竖版 — 泛场景镜构图被锁 = 降质。适用面预期 = 角色特写/近景镜。
    定妆照变更 → 需重出 (stale 由试点结论定, 未入 strip_media)。"""
    wf = build_workflow(scene_zh, seed, prefix, extra_loras=extra_loras,
                        book_title=book_title, style=style, page=page)
    del wf["261"]  # EmptyLatentImage 退位
    wf["267"] = {"class_type": "LoadImage", "inputs": {"image": ref_image_name}}
    wf["268"] = {"class_type": "VAEEncode", "inputs": {
        "pixels": ["267", 0], "vae": ["266", 0]}}
    wf["202"]["inputs"]["latent_image"] = ["268", 0]
    wf["202"]["inputs"]["start_at_step"] = int(start_step)
    wf["202"]["inputs"]["add_noise"] = "enable"  # 仍加噪 (cfg=1 turbo 链路要求)
    return wf


_CHAR_LOOKS: dict[str, list] = {}  # book_title → [(name, look, keywords)] (进程缓存)


def _char_looks(book_title: str) -> list:
    """圣经角色锁定卡 → [(name, look, 关键词组)] (0923 确定性注入用)."""
    if book_title not in _CHAR_LOOKS:
        from .director2 import _bible_cache_path
        try:
            b = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
            _CHAR_LOOKS[book_title] = [
                (str(c.get("name") or ""), str(c.get("look") or ""),
                 [w.strip() for w in str(c.get("look") or "").replace("，", "+").split("+")
                  if len(w.strip()) >= 3])
                for c in (b.get("characters") or []) if c.get("name") and c.get("look")]
        except Exception:  # noqa: BLE001
            _CHAR_LOOKS[book_title] = []
    return _CHAR_LOOKS[book_title]


def _inject_char_looks(scene: str, book_title: str, shot: dict | None = None) -> str:
    """出场角色 look 串确定性前置 (0923 用户实锤 货不对版: 规划层 LLM 只带名字丢造型).

    命中: 角色名在画面句且 look 关键词缺失 → 前置 "角色名（look 全串），" —
    同试渲调味验证过的人物前置模式 (主体权重最高, 原句不稀释)。
    1013 P0 冲突6 (围栏对账): 主体围栏=作者 look 只准引用镜 — render 期注入
    若对非 cite 镜的作者名前置 look = 把违例放大。带 shot 时查 register,
    非 author_cite 的作者名不注入 (writer_audit 事后口的 render 期前哨)。"""
    for name, look, kws in _char_looks(book_title):
        if name and name in (scene or "") and not any(k in (scene or "") for k in kws[:3]):
            if shot is not None:
                try:
                    from .registers import author_of
                    from .shots import load_doc
                    _doc = load_doc(book_title, int(shot.get("_ep") or 0) or None) if shot.get("_ep") else None
                except Exception:  # noqa: BLE001 — 围栏查询失败不挡注入 (宽进)
                    _doc = None
                if _doc is not None and str(shot.get("register") or "") != "author_cite"                         and name == author_of(_doc):
                    logger.warning("[k2] 围栏: %s 非引用镜不前置作者 look (冲突6对账)", name)
                    return scene or ""
            return f"{name}（{look}），" + (scene or "")
    return scene or ""


_BIBLE_HUE: dict[str, tuple[float, str]] = {}  # book_title → (圣经mtime, 主色)


def _bible_hue(book_title: str) -> str:
    """圣经主色 (0926 主色注入产线): color_palette 主色字段正则提取.

    同试渲链调味 (style_board.render_bible_samples) 的取色逻辑 — 0925 诊断实锤
    该句只在试渲拼接, 产线图各镜色彩自由发挥 = 与试渲观感差距来源之一。
    1012 修: 补 mtime 守卫 (旧进程缓存, 改调色板要重启才生效 — bible_negative
    当时明知此坑未续犯, 此处补齐)。"""
    try:
        from .director2 import _bible_cache_path
        p = _bible_cache_path(book_title)
        mt = p.stat().st_mtime
        hit = _BIBLE_HUE.get(book_title)
        if hit and hit[0] == mt:
            return hit[1]
        b = json.loads(p.read_text(encoding="utf-8"))
        # 1026 修: 收口 config.pick_main_hue 单源 (顿号列表整串吞+截断病句实锤;
        # 中性白系跳过 — 暖羊毛白当主色调点缀=全图洗白)
        from .config import pick_main_hue
        hue = pick_main_hue(str(b.get("color_palette") or ""))
        _BIBLE_HUE[book_title] = (mt, hue)
        return hue
    except Exception:  # noqa: BLE001 — 无圣经/无主色 = 不注入
        return ""


def _scene_eff(shot: dict[str, Any], book_title: str) -> str:
    """K2 画面句最终组装: 角色 look 前置 + 圣经主色调味尾 (render-time, 不入 doc).

    1006 终修 (卓有ep1 纯黑虚空实锤): 渲染锚职责已上收 build_prompt
    (COMPOSITION_BOOST/PROTECT_TAIL/SKIN_HAIR_LAW, page=k2 同试渲)。
    1012 修 (主色注入被掏空实锤 — _bible_hue 沦为死代码, 产线无色调调味 =
    与试渲观感差距): 恢复主色调味, **域界随宪法** — 主色只管环境与道具,
    服装颜色归画面句 (朱砂红烧衬衫教训)。
    1015 语言切换 (用户令: 英文更准确): keyframe_en 非空优先 — Qwen3VL→UNet
    对中文细粒度指令服从度弱 (s14 A/B: 中文=无脸/英文=有脸); look 前置与主色
    尾照挂 (锚定不随语言变), fallback 镜无 EN 退中文。"""
    en = str(shot.get("keyframe_en") or "").strip()
    if en:
        # EN 路不注入 look — writer 的 keyframe_en 是同构图英文版, look 已内联
        # (林肯 (Caucasian male, ...)); 只挂主色调味尾 (域界随宪法, 英文版)
        # 1016 W1: 主色尾单源 (config.main_color_tint, 三拷贝收口)
        en += main_color_tint(_bible_hue(book_title), "en")
        return en
    scene = _inject_char_looks(str(shot.get("image_prompt_zh") or ""), book_title)
    scene += main_color_tint(_bible_hue(book_title))  # 1016 W1: 主色尾单源
    # 1017 无脸根治: 圣经角色纪律进 K2 (逐字取书; 人物镜才挂)
    if _person_in(scene, book_title):
        from .h3 import character_policy  # 惰性: h3 顶部引 brand, 避免环
        _cp = character_policy(book_title)
        if _cp:
            scene += f"。角色纪律(书级圣经, 必须遵守): {_cp}"
    return scene


def run_shot(shot: dict[str, Any], doc: dict[str, Any]) -> Path:
    """跑单镜 K2, 产物 move 到管线目录 img/{shot_id}.png, 状态 → img_done.

    0922 文字守门 (用户令: K2 文字生成是垃圾, 只准图形): 出图后 OCR 检字,
    命中换种子重掷 1 次, 仍脏 shot.text_suspect=True 交人检。
    0923 角色注入: 画面句含角色名但缺 look → 渲染前确定性补串 (货不对板根治)。
    0926 主色注入: 圣经主色+哑光织物锚 render-time 叠加 (_scene_eff, 同试渲链)。"""
    import time as _t
    _t0 = _t.monotonic()
    out_dir = shots_mod.ep_dir(doc["book_title"], doc["ep"]) / "img"
    dest = out_dir / f"{shot['shot_id']}.png"
    prefix = f"anim/{shots_mod._safe(doc['book_title'])}/ep{doc['ep']}/k2/{shot['shot_id']}"
    scene_eff = _scene_eff(shot, doc["book_title"])
    # 1015 二段质量 (s37 实锤: 申报镜的一段仍渗漏自绘乱字): 申报镜加空白面硬指令
    if shot.get("in_image_text"):
        scene_eff += ("，所有文字载体（标签/便签/白板/牌匾）完全空白无字，文字将由后续工序"
                      "添加，本层绝不绘制任何文字笔画; all text-bearing surfaces are completely "
                      "blank, draw absolutely no letters or characters")
    # 0927 终串机检 (观察模式, prompt_gate): 扫描对象=真正发给 CLIPTextEncode 的
    # 拼装串 (补 visual_ban_scan 只扫 doc 字段的盲区); 只标记不阻断
    _gate = prompt_gate.scan(build_prompt(scene_eff, doc["book_title"], page="k2"),
                             scene_zh=scene_eff, book_title=doc["book_title"])
    if _gate:
        shot["prompt_gate"] = _gate
        logger.warning("[gate] %s %s", shot["shot_id"], prompt_gate.brief(_gate))
    # 1004 修 (s16 死循环实锤): 违禁判决回流 — 硬违禁连杀镜生图前, 把致死子句
    # 从画面串里删掉 (K2 负词通道物理无效 cfg=1+ZeroOut, 唯一可靠=正面句删子句)。
    # 🎨重做到生图由此获得真实逃生面; 隐喻损失交给 ♻️重规划 (带判决反馈) 补设计。
    # 1014 P4 (s51 实锤): 视觉孪生致死时句里无致死词本身 — 扩带孪生族同剥。
    _hard_kw = sorted({w for w in _HARD_VIOLATIONS
                       for r in (shot.get("gate_rejects") or [])
                       if r.get("hard") and w in str(r.get("reason") or "")})
    if _hard_kw:
        _twin = sorted({t for k in _hard_kw for t in _HARD_TWIN.get(k, ())})
        scene_eff, _dropped = strip_negative(scene_eff, _hard_kw + _twin)
        if _dropped:
            logger.warning("[k2] %s 判决回流: 画面串剔除致死/孪生子句 ×%d (%s)",
                           shot['shot_id'], _dropped, "/".join(_hard_kw + _twin))
    # 1014 P1 → 1015 升级 → 1016 文字总路由 W1 钳制 (ID-099 釜底抽薪):
    # 文字软拒/裸载体镜只做**提示词卫生** (剔文字载体子句 + 空白素面指令,
    # 防 krea2 文字先验), 不再 auto 抽词灌 in_image_text — 二段化只认规划期
    # 显式申报 (旧版 GPU 相位自治抽词绕过全部规划闸, s01 五路文字同镜实锤;
    # 文字路由归规划层, 见 docs backlog ID-098/099/101 与运镜军规)。
    _txt_rej = [r for r in (shot.get("gate_rejects") or [])
                if not r.get("hard") and "文字" in str(r.get("reason") or "")]
    _carrier_naked = bool(
        not shot.get("in_image_text")
        and (TEXT_CARRIER_NOUN_RE.search(str(shot.get("keyframe_zh") or ""))
             # 1021 补 (s83 实锤 "做到大字亮起" 无载体物件 — 大字=字面画字指令,
             # 载体名词表只收物件拦不到, K2 原样画出中文 → 触发同款提示词卫生)
             or "大字" in str(shot.get("keyframe_zh") or "")))
    if len(_txt_rej) >= 1 or _carrier_naked:
        _strip_kw = ["写着", "字样", "字迹", "写满", "刻着", "刻字", "大字",
                     "标签黑字", "标签金字", "黑字手写体", "金字手写体", "米白标签", "金色标签",
                     "written on", "engraved", "inscription", "lettering"]
        scene_eff, _dropped2 = strip_negative(scene_eff, _strip_kw)
        if not shot.get("in_image_text"):
            scene_eff += ("；所有标签、牌匾、刻面均为无字的空白素面 (plain blank labels and "
                          "surfaces with no writing, no letters, no marks) — 文字将由后续工序添加")
            logger.info("[k2][text-policy] %s auto-2stage suppressed: no explicit "
                        "in_image_text (1016 文字总路由, 抽词权归规划层)", shot['shot_id'])
        elif _dropped2:
            logger.warning("[k2] %s 申报镜文字载体子句剔除 ×%d",
                           shot['shot_id'], _dropped2)
    from .h3 import arc_thesis  # 惰性: h3 顶部引 brand, 避免环
    _thesis = arc_thesis(doc, str(shot.get("arc_id") or ""))
    # 1017 W7: K2 产线纯英 (用户令) — 组装串过翻译层 (镜级缓存), fail-open 退中文
    from . import k2_lang
    _scene_eff_en = k2_lang.ensure_en_prompt(shot, scene_eff)
    dirty = render_gated(
        lambda sd: build_workflow(_scene_eff_en, sd, prefix,
                                  shot.get("extra_loras"), book_title=doc["book_title"],
                                  page="k2", thesis=_thesis, lang="en"),
        dest, int(shot.get("seed") or 0), f"k2 {shot['shot_id']}", shot=shot,
        face_check=_person_in(scene_eff, doc["book_title"]),
        book_title=str(doc.get("book_title") or ""))
    if dirty:
        shot["text_suspect"] = True
    shot["attempts"]["k2"] += 1
    if dest.exists():  # 0924 修: 硬违禁删图后文件不存在 → 保持 planned, 不做幽灵 img_done
        shot["image_file"] = f"img/{shot['shot_id']}.png"
        shots_mod.transition(shot, "img_done")
    else:
        logger.warning("[k2] %s 图文件不存在 (硬违禁删除/生成失败), 保持 planned",
                       shot["shot_id"])
    _ledger.record(doc["book_title"], doc["ep"], "k2", shot["shot_id"],
                   trigger=str(doc.get("_ledger_trigger") or ""),
                   ms=int((_t.monotonic() - _t0) * 1000),
                   note=str(shot.get("k2_rerolls") or ""))
    return dest


# ── 文字守门 (0922 用户令: 所有生图必须压住文字 — K2 出字即垃圾, 只生成图形) ──

_OCR: Any = None  # 缓存引擎; False = 不可用 (跳过守门)


def _ocr_engine():
    global _OCR
    if _OCR is None:
        try:
            from app.services.material_ingest_service import _new_ocr
            _OCR = _new_ocr()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[k2] OCR 守门不可用 (跳过检字): %s", exc)
            _OCR = False
    return _OCR or None


def _ocr_dirty(png: Path) -> bool:
    """图内检出可信文字 (过滤 <2% 尺寸噪点) → True."""
    ocr = _ocr_engine()
    if ocr is None:
        return False
    try:
        import numpy as np
        from PIL import Image
        im = Image.open(png).convert("RGB")
        w, h = im.size
        result, _ = ocr(np.array(im))
        for box, txt, score in (result or []):
            if float(score) < 0.55:
                continue
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            if (max(ys) - min(ys)) >= 0.02 * h and (max(xs) - min(xs)) >= 0.02 * w:
                # 0926 修: OCR score 偶为 str, %.2f 直接炸 → 异常吞成"放行",
                # 命中文字的图没重掷 (s05 实锤) — 统一 float 化
                logger.warning("[k2] 检字命中: '%s' %.2f @%s",
                               str(txt)[:20], float(score), png.name)
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[k2] OCR 守门异常 (放行): %s", exc)
    return False


def _vlm_dirty(png: Path) -> str | None:
    """VLM 兜底 (0923 用户双实锤: 试渲_1 伪文字 + 试渲_3 各国国旗): 正向提示词压不住的
    都靠这道 — K2 风格化乱字 OCR 读不出 (假阴性), 国旗国徽正向禁令无效。
    glm-4v-flash 一次查: 文字/国旗/国徽/地图/政治人物。返回违禁简述或 None。"""
    try:
        import base64
        import io as _io

        from PIL import Image

        from app.services.reverse_prompt_service import _chat as _vlm_chat
        im = Image.open(png).convert("RGB")
        if im.width > 960:
            im = im.resize((960, int(im.height * 960 / im.width)))
        buf = _io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        out = _vlm_chat(
            "data:image/jpeg;base64," + b64,
            "仔细看这张动画画面, 逐项检查: "
            "①任何可见的文字/字母/数字/汉字 (含招牌/匾额/标签/屏幕上的伪文字乱码) "
            "②任何国家的国旗 ③国徽/军徽/警徽/党徽/帽徽/领章 "
            "④中国地图或带国界线/省界的地图 (1015 口径: 藏宝图/游戏地图/示意小地图不算) "
            "⑤军装警服制式元素 ⑥政治人物或国家领导人形象 "
            "⑦吸烟/香烟/饮酒/醉酒 ⑧赌博 (扑克/骰子/老虎机/筹码) "
            "⑨暴力血腥 (血/伤口/断肢/尸体) ⑩色情暴露 (裸体/暴露) ⑪毒品/注射器 "
            "⑫红旗/红色横幅/红色条幅 (楼顶/建筑/窗外城市天际线上的; 室内红布装饰与人物红衣服不算)。"
            '只输出 JSON: {"violations": ["文字"/"国旗"/"国徽"/"地图"/"政治人物"/'
            '"吸烟饮酒"/"赌博"/"暴力血腥"/"色情暴露"/"毒品"/"红旗"], "what": "简述位置"} '
            '— 全无则 {"violations": [], "what": ""}', max_tokens=96)
        m = re.search(r'"violations"\s*:\s*\[([^\]]*)\]', out or "")
        hits = m.group(1)[:80] if (m and m.group(1).strip()) else ""
        # 1012 二段式: 远处小旗全帧 VLM 看不见 (实锤) — 像素预筛+放大3x确认
        try:
            from .video_qc import _flag_banner  # 混合检测: 上半屏红块→放大VLM
            if _flag_banner(png):
                hits = (hits + ', "红旗"' if hits else '"红旗"')
        except Exception:  # noqa: BLE001 — 不可用放行 (同 VLM fail-open)
            pass
        return hits or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[k2] VLM 违禁兜底不可用 (放行): %s", exc)
        return None


# 1008 规层 R7: 前十 = 政治红线 (绝不落盘); 后六 = 平台禁止类 (同样绝不落盘 —
# 抖音对 暴力/色情/毒品/赌博/吸烟 是删除+限流级, 与政治同规格处置)
# 1012 补: 红旗/红色横幅 — K2负词压不住+静帧守门漏检 (楼顶红旗漏进试渲实锤),
# 抖音审核风险级, 与政治红线同规格 (换seed重掷/绝不落盘)
_HARD_VIOLATIONS = ("国旗", "国徽", "党徽", "军徽", "警徽", "军装", "警服", "领导人", "地图", "政治人物",
                    "吸烟", "饮酒", "赌博", "暴力血腥", "色情暴露", "毒品", "红旗", "红色横幅")
# 1021 可豁免硬项白名单: 只收**内容判定类** (叙事正当性争议空间, 如"决策是赌博"
# 的骨牌/骰子隐喻); 政治红线 (国旗/国徽/党徽/军徽/警徽/军装/警服/领导人/地图/
# 政治人物) 绝不入豁免 — gate_exempt 写了也不生效
_EXEMPTABLE_GATES = {"吸烟", "饮酒", "赌博", "暴力血腥", "色情暴露", "毒品", "红旗", "红色横幅"}

# 1014 P4 (s51 实锤): 视觉孪生致死时画面句里没有致死词本身, 判决回流剥词空转
# 死循环 — 硬词扩带孪生族 (棋盘+地形线条渲出来就是地图; 棋盘/兵棋又会被 VLM
# 判赌博 — 合规替代表「地图→棋盘」与图像闸互搏, 两族孪生都挂上让回流收敛)。
_HARD_TWIN = {"地图": ("地形", "疆域", "版图", "国界", "边境线", "大陆轮廓", "沙盘"),
              "国旗": ("红旗",), "红旗": ("红色条幅", "红布"),
              "赌博": ("棋盘", "棋牌", "骰子", "筹码", "扑克", "兵棋", "象棋", "军棋")}


def _vlm_face_bad(png: Path) -> str | None:
    """五官完整性闸 (1014 ep3 批图实锤: s28无眼/s34空白脸/s12缺嘴鼻)。

    判定器两版史: 一版走 glm-4v-flash 同通道 — 实弹对着 s34 空白脸两把答"正常"
    (模型望不穿), 按安静验证纪律 (终审=qwen3-vl) 换 **bl CLI (qwen3-vl-plus)**
    为主判, glm 退兜底。软级: 违禁外的质量红线, 重掷仍坏保留交人检。fail-open。"""
    _ask = ("检查这张动画画面中所有人物的面部。是否有任何人物缺五官："
            "没有眼睛、没有嘴巴、或整张脸空白光滑无五官(只有腮红)？"
            '只输出 JSON: {"face_bad": "无眼或缺嘴或空白脸的简述", "what": "位置"} '
            '— 全部人物五官正常则 face_bad 为空串, 不要输出别的')
    import shutil
    import subprocess
    try:  # 主判: qwen3-vl (bl CLI, 项目纪律=终审级眼力)。参数列表直传 (shell=True
        # 会被 prompt 里的双引号切碎参数 — "Unexpected argument: /" 闸哑火实锤)
        _bl = shutil.which("bl.cmd") or shutil.which("bl") or "bl"
        r = subprocess.run(
            [_bl, "vision", "describe", "--image", str(png), "--prompt", _ask, "--quiet"],
            capture_output=True, text=True, timeout=120)
        m = re.search(r'"face_bad"\s*:\s*"([^"]{0,12})"', r.stdout or "")
        if m:
            v = m.group(1).strip()
            if v and not v.startswith("无"):  # "无异常"类表述按空处理
                return v
            return None
        if r.returncode != 0:
            logger.warning("[k2] qwen 面部闸 rc=%s: %s", r.returncode, (r.stderr or "")[:80])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[k2] qwen 面部闸异常: %s", exc)
    try:  # 兜底: glm-4v-flash (弱眼力, 有总比没有强)
        import base64
        import io as _io

        from PIL import Image

        from app.services.reverse_prompt_service import _chat as _vlm_chat
        im = Image.open(png).convert("RGB")
        if im.width > 960:
            im = im.resize((960, int(im.height * 960 / im.width)))
        buf = _io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        out = _vlm_chat("data:image/jpeg;base64," + b64, _ask, max_tokens=64)
        m = re.search(r'"face_bad"\s*:\s*"([^"]{0,8})"', out or "")
        v = m.group(1).strip() if m else ""
        return v or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[k2] VLM 面部闸不可用 (放行): %s", exc)
        return None


def render_gated(seed_to_wf, dest: Path, seed: int, label: str, *, retries: int = 1,
                  shot: dict[str, Any] | None = None,
                  face_check: bool = False,
                  book_title: str = "") -> bool:
    """出图 + 违禁守门双道: seed_to_wf(seed) 产工作流; 命中换 seed+1 重掷。

    第一道 OCR (真文字, 毫秒级); 第二道 VLM (伪文字/国旗/国徽/地图/政治人物, 秒级)。
    分级处置 (0923 用户实锤 国旗+国徽双中):
    - 文字类 (软, 质量问题): 重掷后仍脏 → 保留 + 返回 True 交人检;
    - 政治类 (硬, 抖音红线): **绝不落盘** — 重掷到额度用尽仍脏 → 恢复旧图或删图, True。
    - 五官类 (软, 1014): face_check=True (人物镜) 时 VLM 查无眼/缺嘴/空白脸 —
      重掷一次, 仍坏保留交人检 (ep3 s28/s34/s12 实锤)。
    判定器不可用自动放行。
    1004 修 (s16 死循环实锤): 判决回流 shot.gate_rejects (reason/hard/ts, 截 8 条) —
    ♻️重规划吃反馈根除载体, 🎨重做生图前剔除致死子句, 打破"换seed→同构图→再杀"环。"""
    prev = dest.read_bytes() if dest.exists() else None
    hard = False
    reasons: list[str] = []
    # 1021 镜级豁免闸 (ep7 s65-s68 实锤: 多米诺骨牌阵被 VLM 误判"赌博"硬杀连坐
    # 删图 — 用户令"别硬拦截"): shot.gate_exempt 声明的**内容判定类**项不算脏;
    # 政治红线五族+地图锁死不可豁免 (机器白名单, 抖音绝对红线)
    _ex = ({h for h in (shot.get("gate_exempt") or []) if h in _EXEMPTABLE_GATES}
           if shot is not None else set())
    for attempt in range(retries + 1):
        wf = seed_to_wf(seed + attempt)
        comfy.run_workflow(wf, dest=dest, timeout_sec=300, label=f"{label}#{attempt}")
        # 0923 修: VLM 永远跑 — OCR 先脏会短路跳过 VLM, 国徽漏检实锤 (红色制服+徽章诱导)
        vbad = _vlm_dirty(dest)
        if vbad and _ex:
            _hits = {h for h in _HARD_VIOLATIONS if h in str(vbad)}
            if _hits and _hits <= _ex:  # 命中项全部已豁免 → 视为干净 (不重掷不删图)
                logger.info("[gate] %s 豁免命中 %s (gate_exempt, 交人检)", label,
                            "/".join(sorted(_hits)))
                vbad = None
        fbad = (_vlm_face_bad(dest) or "") if (face_check and not vbad) else ""
        hard = bool(vbad) and any(h in str(vbad) for h in _HARD_VIOLATIONS)
        if vbad:
            reasons.append(f"{vbad}{'[硬]' if hard else '[软]'}")
        if fbad:
            reasons.append(f"{fbad}[软·五官]")
        if shot is not None and (vbad or fbad or _ocr_dirty(dest)):
            gr = shot.setdefault("gate_rejects", [])
            gr.append({"reason": str(vbad or fbad or "OCR文字"), "hard": hard,
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            del gr[:-8]  # 截尾留 8
            # 1017 W5: 图像闸判决 → 书级负词沉淀回路 (fail-open)
            from . import neg_learn
            neg_learn.record_verdict(book_title, str(vbad or fbad or "OCR文字"))
        if not (vbad or fbad or _ocr_dirty(dest)):
            return False
        logger.warning("[k2] 违禁守门重掷 (%s 第%d次, %s%s)", label, attempt + 1,
                       vbad or fbad or "OCR文字", " [硬·政治]" if hard else "")
    if hard:
        if prev is not None:  # 政治红线: 脏图绝不留 — 有旧图恢复旧图, 无旧图删脏图
            dest.write_bytes(prev)
            logger.warning("[k2] 硬违禁未通过, 已恢复上一版: %s", dest.name)
        else:
            dest.unlink(missing_ok=True)
            logger.warning("[k2] 硬违禁未通过且无旧版, 弃图: %s", dest.name)
        if shot is not None:
            shot["error"] = (f"硬违禁({','.join(reasons)}) 连杀, 换seed无效 — "
                             "已自动剔除致死子句, 再🎨重做; 仍杀则♻️重规划 (带判决反馈)")[:500]
    return True


def run_batch(doc: dict[str, Any], only: set[str] | None = None,
              on_shot_done: Any = None, stop_check: Any = None) -> dict[str, int]:
    """批跑所有 planned 镜 (串行, GPU 独占). 返回 {ok, fail}. 每镜落盘断点续跑.

    on_shot_done(shot_id, done, total): 每镜后回调 (服务层进度 SSE).
    stop_check(): 镜间返回 True 即停止 (协作取消).
    品牌卡镜零 GPU 直通: image_file=定妆 keyframe, 状态直接 img_done.
    """
    ok = fail = 0
    pending = sum(
        1 for s in doc["shots"]
        if s["status"] == "planned" and (only is None or s["shot_id"] in only)
    )
    logger.info("[k2] 待跑 %d 镜 (2ST 配方, ~15s/镜)", pending)
    done = 0
    _consec_fail = 0  # 1004 修 (ep5 实锤): GPU 病态 (驱动楔死/落错卡) 每镜必炸同错 —
    _last_err = ""    # 连败早停+诊断, 不陪全批烧 (重启后端无用, 须查驱动/重启机器)
    for shot in doc["shots"]:
        if shot.get("political_flag"):
            logger.warning("[k2] %s 政治禁区标记, 跳过 (需单镜🔁 重设计)", shot["shot_id"])
            fail += 1
            continue
        if only is not None and shot["shot_id"] not in only:
            continue
        if shot["status"] != "planned":
            continue
        if stop_check and stop_check():
            logger.info("[k2] 协作取消: 已处理 %d/%d, 停止", done, pending)
            break
        try:
            if shot.get("brand_card"):
                shot["image_file"] = brand_mod.resolve(doc["book_title"], doc["ep"], "keyframe")
                shots_mod.transition(shot, "img_done")
                logger.info("[k2] %s 品牌卡直通 (零 GPU, 首帧=定妆 keyframe)", shot["shot_id"])
            else:
                p = run_shot(shot, doc)
                shot.pop("error", None)  # 1004 卫生: 成功即清残留错 (死 ComfyUI 时代的旧 error 会误导人检)
                logger.info("[k2] %s done %s (%d ok / %d fail)", shot["shot_id"], p.name, ok, fail)
                time.sleep(1.0)
            ok += 1
            _consec_fail = 0
        except Exception as exc:  # noqa: BLE001 — 单镜失败不断批
            fail += 1
            shot["error"] = f"k2: {exc}"[:500]
            logger.error("[k2] %s 失败: %s", shot["shot_id"], exc)
            _sig = str(exc)[:120]
            if _sig == _last_err:
                _consec_fail += 1
            else:
                _consec_fail, _last_err = 1, _sig
            if _consec_fail >= 3:
                raise SystemExit(
                    f"连败早停: {_consec_fail} 镜同错 — GPU/ComfyUI 病态 (cuDNN/驱动/落卡), "
                    f"重启后端无用, 须查驱动或重启机器。首错: {_last_err}") from exc
        shots_mod.save(doc)
        done += 1
        if on_shot_done:
            try:
                on_shot_done(shot["shot_id"], done, pending)
            except Exception:  # noqa: BLE001 — 回调异常不炸批
                logger.warning("[k2] 进度回调异常", exc_info=True)
    return {"ok": ok, "fail": fail}
