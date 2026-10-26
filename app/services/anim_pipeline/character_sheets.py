"""角色设计台 (0923 用户令) — 圣经角色锁定卡 → 定妆照沉淀.

独立新功能 (不与选型/试渲合并): 每个角色一张 K2 定妆照
(书级风格配方 + look 关键词 → 单角色全身立绘, 干净背景),
产物沉淀 _资产/角色设计/{书}/{角色}.png — 全书人物锚:
人检定稿后供分镜参考 / 未来 i2i 一致性底图; look 文本回写圣经 characters。
SSE: chars_done/chars_error; GPU 锁与产线互斥; 违禁守门同产线。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

from .config import _safe_name
from .director2 import _bible_cache_path
from .k2 import build_workflow, render_gated
from .style_board import _assets, _emit

logger = logging.getLogger(__name__)

_JOBS: dict[str, dict] = {}
_DESIGN_SEED = 20260923
# 0928 铬人四连败史: 纹理型风格 LoRA 在定妆照短句人物提示词上吞掉整个生成
# (0.65/0.35/0.15 三档压帽 + 文字锚全线失守, 连 look 遵从度都崩) —
# 终案: 定妆照渲染摘除风格 LoRA (见 _char_recipe), 教训留此防回潮。


def _char_recipe(recipe: dict | None) -> dict:
    """定妆照专用配方 (0928 用户令"以后也是如此"): ①风格 LoRA 整体摘除 — 纹理型
    LoRA 在短句人物提示词上吞掉人物 (0.65/0.35/0.15 三档铬人实锤, 文字锚全线
    失守+look 遵从度崩); 定妆照职责=look 复现的人物锚参考图, 家族纹理归正式
    分镜的全配方 ②头部家族纹理词消毒 (液态金属/流体/霓虹/酸性 → 哑光黏土/干净/
    明亮), 人物形态轴 (3D卡通) 保留 ③配方尾剥环境 — 背景一律最简纯白
    ④1013 双人根治: 头部比例词消毒 — 风格头的"大头/Q版身比例/chibi"与定妆照
    头身比1:7律 (0928 用户令) 直接对冲且全胜 (卓有8卡全渲成2-3头身Q版实锤),
    比例归定妆照律, 风格头只留家族质感词。"""
    head = (str((recipe or {}).get("style_prompt_head") or "")
            .replace("大头Q版身比例", "")
            .replace("Q版", "")
            .replace("大头", "")
            .replace("圆润可爱", "精致生动")  # 1013 二追: 圆润可爱=大头推手 (消毒后仍3头身实锤)
            .replace("抽象流体霓虹风格，", "")
            .replace("液态金属质感", "哑光黏土质感")
            .replace("液态金属", "哑光黏土")
            .replace("酸性霓虹色调", "明亮柔和色调")
            .replace("霓虹", "明亮")
            .replace("流体", "干净"))
    head = re.sub(r"(?i)chibi\s*", "", head)
    head = re.sub(r"[，,]\s*[，,]", "，", head).strip("，, ")  # 保留末尾"："作 head→scene 分隔
    if head and not head.endswith(("：", ":", "，", ",")):
        head += "："
    r = dict(recipe or {})
    r["lora"] = ""          # 摘除: build_workflow 对空 lora 跳过 LoRA 节点
    r["lora_strength"] = 0.0
    r["style_prompt_head"] = head
    r["style_prompt_tail"] = "纯白色无缝干净背景，无地面无液体无粒子无渐变，9:16竖幅全身像"
    return r


def _design_dir(book_title: str) -> Path:
    return _assets() / "角色设计" / _safe_name(book_title)


def characters(book_title: str) -> list[dict]:
    try:
        raw = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
        return [c for c in (raw.get("characters") or []) if isinstance(c, dict)]
    except Exception:  # noqa: BLE001
        return []


def save_characters(book_title: str, chars: list[dict]) -> dict:
    """角色卡增删改 → 回写圣经 (圣经 characters 是唯一事实源, 本页只是编辑器).

    1008 P9: linked_props (关联道具, 从道层 props 勾选) 入白名单 — 定妆照/分镜
    消费: 角色出场自带其道具。"""
    p = _bible_cache_path(book_title)
    data = json.loads(p.read_text(encoding="utf-8"))
    valid_refs = {str(pr.get("ref") or "") for pr in (data.get("props") or [])}
    data["characters"] = [{"name": str(c.get("name") or "").strip()[:24],
                           "role": str(c.get("role") or "")[:64],
                           "look": _strip_skin_tone(str(c.get("look") or ""))[:160],
                           "linked_props": [str(r) for r in (c.get("linked_props") or [])
                                            if str(r) in valid_refs]}
                          for c in chars if (c.get("name") or "").strip()]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"saved": len(data["characters"])}


def _author_name(raw_author: str) -> str:
    """DB author 字段剥装饰取核心名: '【美】德鲁克（Drucker，P. F. ）'→'德鲁克'。
    先按 ;/著/译 切断取第一作者段, 防译者混入 ('罗伯特·戴博德 著 · 陈赢 译')。"""
    import re as _re
    seg = _re.split(r"[;；]|著|译", str(raw_author or ""))[0]
    seg = _re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", seg)
    m = _re.search(r"[\u4e00-\u9fa5][\u4e00-\u9fa5·]{1,11}", seg)
    return (m.group(0).strip("·") if m else "")


def ensure_author_card(book_id: str, book_title: str) -> dict:
    """0928 用户令"每本书都要 (作者卡), 可以不用, 不能没有": 存量书补作者卡。

    已含则原样返回; 缺则一次小 LLM 出 look (同铁律) 追加进圣经 characters,
    不动已有卡 (不触发其它卡定妆照失效)。"""
    import sqlite3

    from .config import load
    con = sqlite3.connect(load().db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("select author from book_projects where id=?", (book_id,)).fetchone()
    con.close()
    name = _author_name(row["author"] if row else "")
    if not name:
        raise RuntimeError("本书 author 字段为空 — 无法定作者卡")
    chars = characters(book_title)
    hit = next((c for c in chars if name in c["name"] or c["name"] in name), None)
    if hit:
        return {"author": name, "card": hit, "added": False}
    from app.services import compliance as _cmp
    from app.services.llm_service import get_llm_service as _llm, _parse_json
    out = _llm().chat(
        "你是动画短片的角色设计师。为拆书动画补一张**本书作者**角色锁定卡 — "
        "讲述口播里高频出现'作者说/作者举了个例子', 需要一个可复用的作者形象锚。\n"
        "规则: 名人不做写实肖像, 靠公知标志特征识别 (发型/眼镜/年代着装等); "
        "look=服饰+脸型发型+标志道具的关键词串 (中文 25-50 字); "
        "look 是单帧静态快照 — 禁两帧叙事与状态转换 ('表情从X转Y'), "
        "姿态神态写单一确定状态; "
        "look 禁文字载体道具 (标语牌/横幅/带字物件), 禁军装警服国徽; "
        "禁半透明/透明皮肤/发光/琉璃化 — 实色哑光卡通皮肤。\n"
        + _cmp.visual_constitution_prompt()
        + '\n输出严格 JSON: {"name": "...", "role": "...", "look": "..."}',
        f"书名: 《{book_title}》 作者: {name}\n"
        "(look 里可用的年代/身份线索从书名与作者名合理推断, 禁编细节数字)",
        model="pro", temperature=0.4)
    card = _parse_json(str(out or "")) or {}
    card = {"name": str(card.get("name") or name).strip()[:24],
            "role": str(card.get("role") or "本书作者")[:64],
            "look": str(card.get("look") or "")[:160]}
    if len(card["look"]) < 12:
        raise RuntimeError("作者卡 look 过短 — 稍后重试")
    save_characters(book_title, chars + [card])
    logger.info("[chars] 作者卡已补: %s (%s)", card["name"], book_title[:16])
    return {"author": name, "card": card, "added": True}


def derive_characters(book_id: str, book_title: str) -> dict:
    """0928 角色生成归本页 (用户裁定: 圣经页只管视觉身份, 角色台管 cast).

    喂料 (用户判词"最好围绕每集稿件"): 各集 roadmap 贯穿人物 (每集粒度规划层) +
    各集成稿 script_text (空气真相, 有则喂) + 故事库节选 (look 的服饰道具细节)。
    产出 3-6 张卡 (同一真实人物跨集并一张), 走 save_characters 回写圣经 →
    人检/改 look → 定妆照。覆盖校验: 每集贯穿人物必须被某张卡吃住, 缺口点名。
    """
    import re
    import sqlite3

    from .config import load
    con = sqlite3.connect(load().db_path)
    con.row_factory = sqlite3.Row
    eps = con.execute(
        "select ep_index, title, roadmap_json, script_text from book_episodes"
        " where book_id=? order by ep_index", (book_id,)).fetchall()
    con.close()
    ep_lines, script_blobs = [], []
    for er in eps or []:
        try:
            rm = json.loads(er["roadmap_json"] or "{}")
        except Exception:  # noqa: BLE001
            rm = {}
        pc = str(rm.get("贯穿人物") or "").strip()
        ep_lines.append(f"- 第{er['ep_index']}集《{er['title']}》贯穿人物: {pc or '未定'}")
        st = (er["script_text"] or "").strip()
        if st:
            script_blobs.append(f"【第{er['ep_index']}集 成稿节选】\n{st[:1800]}")
    if not ep_lines:
        raise RuntimeError("本书还没有集 (总纲未跑?) — 先跑总纲再提炼角色")
    # 0928 用户令: 作者卡每本书必含 (可以不用, 不能没有) — 与贯穿人物同进硬覆盖
    con = sqlite3.connect(load().db_path)
    arow = con.execute("select author from book_projects where id=?", (book_id,)).fetchone()
    con.close()
    author = _author_name(arow[0] if arow else "")  # 1005 修: 裸 connect 返回 tuple, 旧代码按列名取 → TypeError
    author_line = (f"\n【本书作者 (必含卡): {author or '字段为空, 按书内常识判断'} — "
                   "讲述口播高频'作者说', 需常驻形象锚; 卡名用稿内称呼】\n") if author else ""
    story = ""
    from .feeds import get_feed
    _feed = get_feed("story_section")  # B08: 读料经注册点 (外壳 anim_service 注入), 管线零 book_service import
    if _feed:
        try:
            story = (_feed(book_title, "故事库") or "")[:2600]
        except Exception as exc:  # noqa: BLE001 — 故事库为可选加料, 缺了不阻断提炼
            logger.warning("[chars] 故事库取料跳过: %s", exc)
    else:
        logger.debug("[chars] story_section 供给未注册, 跳过故事库加料")
    from app.services import compliance as _cmp
    from app.services.llm_service import get_llm_service as _llm, _parse_json
    sys_p = (
        "你是动画短片的角色设计师。为一系列拆书动画提炼角色锁定卡 — 全书人物锚, "
        "观众靠服饰跨场景认人。\n\n"
        "规则:\n"
        "- **必含卡·本书作者** (见下方【本书作者】): 讲述口播高频'作者说', 需常驻形象锚; "
        "名人不做写实肖像, 靠公知标志特征识别;\n"
        "- **每集『贯穿人物』必须逐人覆盖** (同一真实人物跨集并一张卡, role 写清哪几集出场+"
        "叙事功能); 数量以覆盖为准, 一般 4-8 张; 只在成稿节选里反复出场、撑得起画面的可再补 1 张;\n"
        "- 成稿里没出现的人物不建卡 (库外实体/作者生平圈外人一律不要);\n"
        "- look = 服饰+脸型发型+标志道具的关键词串 (中文 25-50 字), 贴合人物在书中的真实形象"
        "与身份地位, 不同人物的 look 必须一眼可区分;\n"
        "- **look 是单帧静态快照** (1013 双人根治): 定妆照=单幅单人立绘, 禁两帧叙事与状态转换"
        " ('表情从自信转错愕'会渲成同角色多版本排列 — 董事长x3表情变体实锤), 禁'先…后…';"
        "姿态神态写单一确定状态, 一张卡一个姿态一个表情;\n"
        "- **默认禁写肤色词** (1026 用户令·A案, 抖音受众实锤): look 一律不写肤色 — "
        "禁'浅棕/深棕/棕/小麦/古铜/黝黑/白皙/白净'+皮肤组合, 禁'肤色XX'; 人种区分靠"
        "脸型/发型/服装; 肤色统一交模型默认 (受众偏好白里透红, 不落文字);\n"
        "- **唯一例外·真实非白人名人** (1006 乔丹令, 1026 收窄): 非白人名人肤色是第一"
        "识别特征, 明写'非裔/深棕皮肤'等+标志性特征 (体格/耳环/胡型); 服饰可卡通化, "
        "**肤色人种不可漂白**; 白人名人/虚构角色无此例外, 回默认禁写;\n"
        "- **名人大众印象战袍锚** (1006 乔丹二修: 白色中性背心≠乔丹 — 大众第一印象是公牛红球衣): "
        "服饰必须取该人物最大众认知的标志性造型 (乔丹=红色跨栏球衣/乔布斯=黑色高领+牛仔裤/"
        "巴菲特=灰西装), **禁中性化泛化处理**; 书内无着装描述时按大众印象, 不按'身份地位'泛写; "
        "球衣上的数字/队名/文字一律不写进 look (K2 灭字宪法, OCR 守门杀数字);\n"
        "- look 铁律: 禁文字载体道具 (标语牌/横幅/招牌/带字物件 — K2 出字即乱码), "
        "道具一律造型物; 禁军装警服制式与国徽类元素; 禁半透明/透明皮肤、皮肤内能量流/"
        "发光/琉璃化 — 皮肤一律实色卡通皮肤; 名人不做写实肖像, 靠标志特征识别。\n"
        + _cmp.visual_constitution_prompt() +
        '\n输出严格 JSON: {"characters": [{"name": "...", "role": "...", "look": "..."}]}'
    )
    user = ("\n【各集主题与贯穿人物 (硬覆盖要求)】\n" + "\n".join(ep_lines)
            + author_line
            + (("\n\n【各集成稿节选 (空气真相, 角色以这里实际出场为准)】\n"
                + "\n\n".join(script_blobs)) if script_blobs else "")
            + ("\n\n【故事库节选 (服饰/道具细节取材)】\n" + story if story else ""))
    out = _llm().chat(sys_p, user, model="pro", temperature=0.4)
    data = _parse_json(str(out or "")) or {}
    chars = [c for c in (data.get("characters") or []) if isinstance(c, dict)]
    chars = [{"name": str(c.get("name") or "").strip()[:24],
              "role": str(c.get("role") or "")[:64],
              "look": str(c.get("look") or "")[:160]}
             for c in chars if (c.get("name") or "").strip()]
    if not chars:
        raise RuntimeError("提炼结果为空 — 稍后重试或 ➕ 手填")
    # 覆盖校验: 每集贯穿人物 (先剥括号注释, 再按顿号切, 再剥"某公司/某"类前缀)
    # 须被某张卡的 name/role 吃住 — 全名或名首二字命中皆算
    misses = []
    blob = " ".join(c["name"] + c["role"] for c in chars)
    for line in ep_lines:
        m = re.search(r"贯穿人物: (.+)$", line)
        if not m or m.group(1) == "未定":
            continue
        for seg in re.split(r"[、,，;；/]", re.sub(r"[（(].*?[)）]", "", m.group(1))):
            nm = re.sub(r"^(某公司|某位|某|一位|一个)", "", seg).strip()
            if len(nm) >= 2 and nm not in blob and nm[:2] not in blob:
                misses.append(nm)
    saved = save_characters(book_title, chars)
    # 作者卡缺口并入覆盖校验 (用户令"不能没有")
    if author and author not in blob:
        misses.append(f"作者·{author}")
    logger.info("[chars] ep 角色提炼 ✓ %d 张 (覆盖缺口: %s)",
                saved["saved"], "、".join(misses) or "无")
    return {**saved, "characters": chars,
            "coverage_misses": misses[:6]}


def designs(book_title: str) -> list[dict]:
    """已沉淀定妆照 [{name, file}] (文件名=角色名)."""
    d = _design_dir(book_title)
    out = []
    if d.exists():
        for p in sorted(d.glob("*.png")):
            out.append({"name": p.stem, "file": p.name})
    return out


# 文字载体道具 (0923 用户实锤 奶球小妹围裙乱码汉字): look 里带这些词 = 给 K2 递写字许可
_TEXT_PROPS = ("标语牌", "横幅", "告示牌", "招牌", "带字", "书页字", "字牌")

# 两帧叙事 (1013 双人根治): "表情从自信转错愕"类状态转换 = 单幅单人立绘的
# 多版本排列邀请 (董事长x3表情变体实锤) — 前缀锚定表情/神态/动作族, 防误伤
# "从头顶到脚尖"类空间描述 (无前缀不匹配)。
_TWO_FRAME_RE = re.compile(
    r"[，,；;]?(?:表情|神态|情绪|动作|姿态|目光|眼神|语气|气氛)"
    r"从[^\s，,。；;]{1,10}(?:转|变成?|化为?|到|渐)[^\s，,。；;]{0,10}")



_TONES = "浅棕|深棕|棕黄|棕色|小麦|古铜|黝黑|白皙|白净|奶白|红润"
_SKIN_TONE_RE = re.compile(
    rf"[，,、]?\s*(?:{_TONES})色?皮肤"
    rf"|[，,、]?\s*皮肤(?:色)?(?:为|是)(?:{_TONES})色?"
    rf"|[，,、]?\s*肤色(?:白净|白皙|红润|黝黑|偏黑|偏白)")


def _strip_skin_tone(look: str) -> str:
    """1026 用户令·A案: look 默认剥肤色词 (确定性闸, 生成律只是减量) — 肤色统一
    交模型默认, 人种区分靠脸/服。例外: 含'非裔'的卡 = 1006 乔丹令 (真实非白人
    名人点名肤色识别), 原样放行。"""
    if "非裔" in look:
        return look
    return _SKIN_TONE_RE.sub("", look).strip("，,、 ")


def _sanitize_look(look: str) -> str:
    """确定性消毒: 剥离文字载体道具词段与两帧叙事段 (资产原文不动, 只清生成入参)."""
    out = str(look or "")
    for w in _TEXT_PROPS:
        if w in out:
            logger.warning("[char-design] look 含文字载体道具 '%s', 生成时剥离: %s", w, out[:50])
            out = out.replace(w, "")
    m = _TWO_FRAME_RE.search(out)
    if m:
        logger.warning("[char-design] look 含两帧叙事 '%s', 生成时剥除 (定妆照=单帧单人): %s",
                       m.group(0), out[:50])
        out = _TWO_FRAME_RE.sub("", out)
    return out


# 姿态词 (1013 双人根治): look 已述姿态时 _scene 不再注入站姿 — 旧版写死
# "正面自信站姿"与 look"坐轮椅"对冲, 模型两版都画 (霍普金斯x3姿势变体实锤)
_POSTURE_WORDS = ("坐", "躺", "跪", "蹲", "骑", "倚", "靠", "斜坐", "行走", "走路", "奔跑", "跑动")


def _scene(look: str) -> str:
    """定妆照画面句 (1013 双人根治重写 — 用户令"修系统不修个例").

    ComfyUI /history 原字节 + bl vision 逐图亲证的三层病灶:
    ①旧首词"角色设计定妆照"=基座角色设定表先验邀请函 — character sheet 训练
    样本即"同角色多姿势/多表情网格", 9:16 竖幅下渲成纵向排列 (林肯x2复制/
    董事长x3表情变体/霍普金斯x3姿势变体/银行总裁x2眼镜两版, 全是同一角色
    多版本, 非两个不同人) → 改纯正向单人锚定 (1012 国旗终案范式);
    ②旧版否定枚举"禁第二个人物/陪衬路人/镜像分身"违反 1012 铁律 (否定枚举=
    点名投放, "镜像分身"精确喂出复制排列) → 删除, 多人压制移交 K2 负通道
    (k2._SHEET_NEG, cfg=2.0 真负通道);
    ③剥尽提示词内嵌的人类批注 — 旧版把"与其他角色卡同一比例体系/罗斯福双人
    实锤"等历史注释写进提示词, "其他角色卡"本身就在邀请其他角色, 人名直接
    喂给模型; 教训只留在代码注释。
    姿态归 look 裁决 (见 _POSTURE_WORDS)。"""
    lk = _sanitize_look(look)
    posture = "" if any(w in lk for w in _POSTURE_WORDS) else "正面自信站姿，"
    return ("单幅全身人物立绘插画，画面中只有这一名角色独自呈现，一人一画，"
            "头顶到脚尖全身完整在画面内，四周留出适当呼吸边距，"
            "不裁切头部脚部或任何道具，所有标志道具完整可见，"
            "成年人物比例，头身比约1:7，四肢比例正常，"
            + posture +
            "表情自然生动，完整呈现服装层次与标志道具，角色细节清晰，"
            "人物皮肤为实色哑光卡通材质，头发为分组雕塑感造型，服装为哑光织物，"
            "干净纯白色无缝背景，无地面无液体无粒子无渐变无任何环境元素，"
            "服装配饰与道具全部无文字无字母，制服不带任何徽章标识："
            + lk)


def view(book_id: str, book_title: str, bound: str | None, recipe: dict | None) -> dict:
    j = _JOBS.get(book_id)
    # 1008 P9: 道层道具表随视图下发 (角色卡"关联道具"勾选源)
    prop_opts: list[dict] = []
    try:
        raw = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
        prop_opts = [{"ref": str(p.get("ref") or ""), "concept": str(p.get("concept") or "")}
                     for p in (raw.get("props") or []) if p.get("ref")]
    except Exception:  # noqa: BLE001 — 无圣经/无道具 = 空选项
        pass
    return {"bound": bound, "style_recipe": recipe,
            "characters": characters(book_title),
            "designs": designs(book_title),
            "props": prop_opts,
            "job": {k: j.get(k) for k in ("id", "kind", "status", "error", "log")}
            if j else None}


def render_missing(book_title: str, emit) -> tuple[int, int]:
    """补渲缺/变的定妆照 (0923 抽出: 圣经重掷自动链 + 设计台共用).

    emit(msg) 接收进度; 返回 (新渲数, 跳过数). 占 GPU 锁."""
    import hashlib

    from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK

    from .config import resolve_style
    recipe = resolve_style(book_title)
    d = _design_dir(book_title)
    d.mkdir(parents=True, exist_ok=True)

    def _meta(c):
        return d / f"{_safe_name(c['name'])}.meta.json"

    def _look_h(c):
        # 1012 修: 指纹并入风格配方 (head/tail/LoRA+强度) — 旧版只哈希 look 文本,
        # 换绑后 8 张定妆照仍是旧风格渲的却报"不动" (r2v 人物镜参考图被旧风格
        # 带偏); 配方变 → h 变 → 自动重渲
        # 1013 双人根治: 版本盐 — 画面句/负通道/头消毒重写后, 旧定妆照 (含同角色
        # 多版本排列病) 全部按新律判旧自动重渲 (指纹只哈希输入不哈希提示词法,
        # 法变必须靠盐, 否则病图永远报"不动")
        return hashlib.md5("|".join(str(x or "") for x in (
            "v3-单人律+比例律1013",
            c.get("look") or "",
            recipe.get("style_prompt_head"), recipe.get("style_prompt_tail"),
            recipe.get("lora"), recipe.get("lora_strength"),
            # 1026 修: character 页 override 入指纹 — 旧版只哈希基座头,
            # 该页专属头改动永不判旧 (渐变染白大褂重渲 0 张实锤)
            (recipe.get("page_overrides") or {}).get("character", {}).get("style_prompt_head", ""),
        )).encode("utf-8")).hexdigest()[:10]

    work, skipped = [], 0
    for c in characters(book_title):
        same = False
        if (d / f"{_safe_name(c['name'])}.png").exists() and _meta(c).exists():
            try:
                same = json.loads(_meta(c).read_text(encoding="utf-8")).get("h") == _look_h(c)
            except Exception:  # noqa: BLE001
                pass
        if same:
            skipped += 1
        else:
            work.append(c)
    if work:
        with GPU_JOB_LOCK:
            for i, c in enumerate(work, 1):
                emit(f"定妆照 {i}/{len(work)}: {c['name']} 渲染中 (含违禁守门)…")
                dirty = render_gated(
                    lambda sd, lk=c.get("look") or "": build_workflow(
                        _scene(lk), sd,
                        f"chardesign/{_safe_name(book_title)}/{_safe_name(c['name'])}",
                        style=_char_recipe(recipe), portrait=True,
                        page="character", book_title=book_title),
                    d / f"{_safe_name(c['name'])}.png", _DESIGN_SEED + i + int(time.time()) % 100000,
                    f"char-design {c['name']}", retries=3)
                _meta(c).write_text(json.dumps({"h": _look_h(c), "suspect": bool(dirty)},
                                               ensure_ascii=False), encoding="utf-8")
                if dirty:
                    emit(f"⚠️ 定妆照 {i}/{len(work)} {c['name']} 疑似违禁残留 (重掷后仍在), 请人检/改 look 重渲")
                else:
                    emit(f"定妆照 {i}/{len(work)} ✓: {c['name']}")
    return len(work), skipped


def start_design(book_id: str, book_title: str, names: list[str],
                 looks: dict | None = None) -> dict:
    """定妆照 job: names 空列表 = 全部角色. 单书单槽 (与圣经/试渲槽各自独立).

    looks (0923): {角色名: look} 覆写 — 页面文本框当前值随渲随存 (改完直接点 🖩 一步到位)."""
    j = _JOBS.get(book_id)
    if j and j.get("status") == "running":
        raise RuntimeError("角色设计进行中, 稍后再试")
    chars = characters(book_title)
    if looks:
        changed = False
        for c in chars:
            new_lk = str(looks.get(c["name"]) or "").strip()
            if new_lk and new_lk != (c.get("look") or ""):
                c["look"] = new_lk[:160]
                changed = True
        if changed:
            save_characters(book_title, chars)
    todo = [c for c in chars if not names or c["name"] in names]
    if not todo:
        raise RuntimeError("圣经无角色卡 (先在艺术圣经页生成/添加角色)")
    j = {"id": str(uuid.uuid4()), "kind": "chars", "status": "running",
         "error": None, "log": [], "t0": time.time()}
    _JOBS[book_id] = j

    def _run():
        import hashlib

        from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK

        from .config import resolve_style
        from .style_board import bound_style
        try:
            bound = bound_style(book_title)
            if not bound:
                raise RuntimeError("本书未绑定风格")
            recipe = resolve_style(book_title)
            d = _design_dir(book_title)
            d.mkdir(parents=True, exist_ok=True)

            def _meta(c):
                return d / f"{_safe_name(c['name'])}.meta.json"

            def _look_h(c):
                # 1012 修: 同 render_missing — 指纹并入风格配方, 换绑后手动单渲也按新风格判
                # 1013 双人根治: 同 render_missing — 版本盐, 两处必须同把尺
                from .config import resolve_style as _rs
                _r = _rs(book_title)
                return hashlib.md5("|".join(str(x or "") for x in (
                    "v3-单人律+比例律1013",
                    c.get("look") or "",
                    _r.get("style_prompt_head"), _r.get("style_prompt_tail"),
                    _r.get("lora"), _r.get("lora_strength"),
                )).encode("utf-8")).hexdigest()[:10]

            if not names:  # 全部生成 = 补齐缺/变 (0923 抽出 render_missing, 与圣经自动链共用)
                n_new, n_skip = render_missing(book_title, lambda m: _emit(j, m))
                j["status"] = "done"
                if n_new == 0:
                    _emit(j, "全部已沉淀且 look 未变, 无需生成", "chars_done")
                else:
                    j["log"] = [f"新渲 {n_new} 张, 跳过 {n_skip} 张"]
                    _emit(j, f"角色设计完成 ✓ 新渲{n_new} 跳过{n_skip} → _资产/角色设计/", "chars_done")
                return
            with GPU_JOB_LOCK:
                for i, c in enumerate(todo, 1):
                    _emit(j, f"定妆照 {i}/{len(todo)}: {c['name']} 渲染中 (含违禁守门)…")
                    dirty = render_gated(
                        lambda sd, lk=c.get("look") or "": build_workflow(
                            _scene(lk), sd,
                            f"chardesign/{_safe_name(book_title)}/{_safe_name(c['name'])}",
                            style=_char_recipe(recipe), portrait=True,
                            page="character", book_title=book_title),
                        d / f"{_safe_name(c['name'])}.png", _DESIGN_SEED + i + int(time.time()) % 100000,
                        f"char-design {c['name']}", retries=3)
                    _meta(c).write_text(json.dumps({"h": _look_h(c), "suspect": bool(dirty)},
                                                   ensure_ascii=False), encoding="utf-8")
                    if dirty:
                        _emit(j, f"⚠️ {c['name']} 疑似违禁未通过 (重掷后仍存疑), 请人检")
                    j["log"].append(f"{c['name']} ✓")
                    _emit(j, f"定妆照 {i}/{len(todo)} ✓: {c['name']}")
            j["status"] = "done"
            _emit(j, f"角色设计完成 ✓ 共{len(todo)}张 → _资产/角色设计/", "chars_done")
        except Exception as exc:  # noqa: BLE001
            j["status"] = "error"
            j["error"] = str(exc)
            _emit(j, f"角色设计失败: {exc}", "chars_error")
            logger.exception("[char-design] 失败")

    threading.Thread(target=_run, daemon=True, name="char-design").start()
    return {"status": "running", "id": j["id"], "n": len(todo)}
