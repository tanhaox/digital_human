# -*- coding: utf-8 -*-
"""拆书创作公共层 — 人设常量 / 灵性金句锚定扫描 / 六段弹性标签 / LLM 工具.

拆包自 orchestrator.py , 函数体原样搬运零行为变更.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import BookProject
from app.services.book_service.reader import read_book
from app.services.llm_service import LLMService, get_llm_service

logger = logging.getLogger(__name__)

__all__ = ["elastic_labels", "validate_script", "source_context", "persona_anchor_issues",
           "derive_breakdown_steps", "section_flow_issues",
           "fandeng_opening_issues", "word_band", "hook_position_issues",
           "SPEECH_RATE", "RATE_FAST", "RATE_SLOW", "PLATFORM_DURATION_BAND"]

# ── 语速真值与字数带 (1018 C4a, 北斗星第3/5条) ──────────────────────────
# 实测 (2026-10-18): 《定位》7 集成稿 × 各自 full_paragraph.wav 真实时长 (ffprobe,
# 老谭·说书 TTS2, E盘 outputs/audio 2026-09-22~10-04), 字数按 validate 同口径
# (剥标签/空白): ep1 5.31 / ep2 4.96 / ep3 5.16 / ep4 5.13 / ep5 4.97 / ep6 4.82 /
# ep7 4.90 → median 4.974。旧 4.6 (0912 锚) 偏慢 8%: 300s 按 4.6 预算 1380 字
# 实际只出 ~276s (4:36)。设计值取 5.0, 包络校验用实测两端。
SPEECH_RATE = 5.0    # 字/秒 设计值 (median 4.974)
RATE_FAST = 5.31     # 实测最快 — 包络下限用 (最快语速下仍 >4:30)
RATE_SLOW = 4.82     # 实测最慢 — 包络上限用 (最慢语速下仍 <7:00)
# 平台带 (北斗星第5条): 单条视频 >4:30 且 <7:00
PLATFORM_DURATION_BAND = (270.0, 420.0)
# 字数绝对包络: 下限 ceil(270×5.31)=1434 (n/5.31 > 270), 上限 floor(420×4.82)=2024 (n/4.82 < 420)
_WORD_ENV = (1434, 2024)


def word_band(target: float) -> tuple[int, int, int, int]:
    """生效修复带 (lo, hi) 与硬线 (hlo, hhi) — 相对带 ∩ 平台包络 (1018 C4a)。

    相对: cap = target×SPEECH_RATE, 软带 [0.90, 1.08]×cap, 硬线 [0.80, 1.20]×cap;
    包络: _WORD_ENV (平台带按实测语速两端保真)。
    target ∈ PLATFORM_DURATION_BAND 时带子必非空 (270s→[1434,1458], 420s→[1890,2024])。
    """
    cap = int(float(target or 600) * SPEECH_RATE)
    lo, hi = max(int(cap * 0.90), _WORD_ENV[0]), min(int(cap * 1.08), _WORD_ENV[1])
    hlo, hhi = max(int(cap * 0.80), _WORD_ENV[0]), min(int(cap * 1.20), _WORD_ENV[1])
    return lo, hi, hlo, hhi

# ── 人设后处理注入层 (P1 定稿措辞): 创作 LLM 保持中性, 身份段/结尾在此加 ──
# 2026-08-23: 自称"静"而非"静姐" — 拉大受众群 (静姐限定了已婚熟龄, 静覆盖更广)
_PERSONA_IDENTITY = "我是静，读透一本好书，陪你遇见更好的自己。"
_PERSONA_ENDING = "照顾好自己，让我们一起成长。我是静，下期见。"

# 老谭读书版 (2026-09-07 双人物): 身份段/结尾与静读书差异化, 融 brand_tag
_LAOTAN_BOOK_IDENTITY = "我是老谭，读一本书，多一个硬本事。"
# 0913 集间差异化 (用户令: 系列每集转发话术一样需加强): 固定转发/收藏 CTA 撤出系统结尾 —
# CTA 改由创作 LLM 按本集秘籍现写进收尾段末 (模板【CTA 规格】+ validate_script require_cta 兜底),
# 系统只注入品牌句。原固定串 ep1-5 逐字同句, 系列连播同一句转发话术=观众疲劳。
_LAOTAN_BOOK_ENDING = "我是老谭，拆一本书，翻一座山。咱们下一集，见。"
# 收官集版 (0908): 无下一集 + 交棒 + 登顶 (呼应品牌句"山巅相见" — 爬了N集, 到顶了)
# (0913 保留原样: 收官集全系列仅一次, 无集间重复; 交棒+转发 CTA 是收官仪式的一部分)
# 1005 修: "六集拼完了"写死于 6 集时代, 0914 集数由书定 ([5,10]) 后 7 集书说"六集"=事实错 —
# 集数改由调用方传入 (ep_word), 兜底"六"保持旧行为
def _finale_ending(ep_word: str = "六") -> str:
    return (f"{ep_word}集拼完了，这套系统，现在是你的一套了。"
            "转给一个正在想要破局的朋友，这套东西能帮他省半年弯路。"
            "我是老谭，拆一本书，翻一座山——咱们，山顶见。")


_LAOTAN_BOOK_ENDING_FINALE = _finale_ending()


def persona_identity_ending(prompt_template: str | None, *, finale: bool = False,
                             ep_word: str = "六") -> tuple[str, str]:
    """身份段/固定结尾 按persona模板分流 .

    prompt_template 含 "laotan-book" → 老谭读书版; finale=收官集 → 完结版结尾 (山顶见).
    ep_word: 收官结尾的集数汉字 (集数由书定, 调用方传 _cn(总集数))。
    """
    if prompt_template and "laotan-book" in prompt_template:
        return _LAOTAN_BOOK_IDENTITY, (_finale_ending(ep_word) if finale else _LAOTAN_BOOK_ENDING)
    return _PERSONA_IDENTITY, _PERSONA_ENDING

# ── 灵性金句锚定扫描 (2026-08-21): 生成后确定性检查, 未锚定→触发重写 ──
# 本书/灵性主题流传广的金句, 出现必须带"书里/书中/作者"锚定, 防宿命论/向内归罪/伪科学.
_OCCULT_QUOTE_PATTERNS = [
    r"外面没有别人",
    r"凡是你抗拒的",
    r"都是[^。，]{0,14}的礼物",
    r"事件[^。，]{0,8}中性",
    r"内在[^。，]{0,8}(镜子|投射)",
    r"内在投射",
    r"爱[^。，]{0,4}喜悦[^。，]{0,4}和平",
    r"心想事成",
    r"境由心转",  # 唯心/心转断言, 须锚定书里比喻
    r"二十一天",  # 21天固定周期, 须锚定书里建议/弱化确定性
]
_ANCHOR_RE = re.compile(r"(书里|书中|作者|按照书|这本书|书里讲|书里说|书里提到|书中提出|书中记录|书中的看法)")


def _scan_occult_quotes(out: str) -> list[str]:
    """扫描未锚定书中观点的灵性金句, 返回问题清单 (每句一条).

    判定: 金句所在句(上一个句末标点到金句结束)内必须出现锚定词(书里/书中/作者/按照书等);
    跨句的'这本书'泛介绍不算锚定.
    """
    issues: list[str] = []
    for pat in _OCCULT_QUOTE_PATTERNS:
        for m in re.finditer(pat, out):
            parts = re.split(r"[。！？!?；;\n]", out[: m.end()])
            sentence = parts[-1] if parts else ""
            if not _ANCHOR_RE.search(sentence):
                issues.append(
                    f"「{m.group(0)}」需锚定书中观点——前句带'书里有一句比喻/书中提出/按照书里的看法'主语，禁作客观真理")
    return issues

# 兜底基底 (2026-08-20): config/jingshu-book.txt 缺失/过短时回退, 防管线静默崩坏.
# 精简版人设 + 六段结构说明; 附加指令/Gate B 仍由 generate_episode 动态追加.
_FALLBACK_BASE = (
    "你是\"静读书\"的拆书讲述者静，45岁+，已有上小学的儿子。满级情商、温柔、知性、"
    "通过分享达到精神成长。说话不端着、不卖弄、不居高临下，拒绝煽情和鸡汤腔。\n"
    "【核心任务】基于【本集输入】（书名/本集路线图/全书输入/读者反应/素材包/前序覆盖），"
    "按六段结构写一集口播稿：把这本书拆透、讲人话。\n"
    "【受众画像】抖音、小红书：25-50岁女性，希望通过阅读、听书获得成长。喜欢知识沉淀，"
    "又没有太多时间进行仔细阅读；爱听\"这本书说的，对我有什么用\"，打比方用职场/育儿/"
    "关系/深夜独处等生活场景。\n"
    "【口吻红线】普遍规律用\"咱们\"、个人建议才用\"你\"；转场极简；禁元叙述/计数预告句；"
    "比喻须与论证严格对应；温柔但不绵软——软声说硬话，观点清晰有立场。\n"
    "【拆书约束】同一引导词全篇最多1次；每句≤20字；强比喻≤3个；"
    "书中内容≤40%、场景/解读≥60%；观点须可追溯来源，书中观点一律\"书中提出/作者认为\"视角限定；"
    "金句转述不挂引号。\n"
    "【六段结构】钩子 → 回顾+引入 → 核心概念拆解（一）→（二）→（三）→ 总结+下期预告，"
    "每段以附加指令给的【A-B秒｜段名】标签行开头，逐字保留。\n"
    "【输出】纯口播文本，末尾另起一段附严格 JSON 块 {\"coverage\":[str]} 列本集知识点，"
    "该块之后禁止再输出任何文字。"
)

# 老谭读书兜底基底 (2026-09-07 双人物): laotan-book.txt 缺失/过短时回退
_FALLBACK_BASE_LAOTAN = (
    "你是\"老谭读书\"的讲述者老谭——上市公司副总裁出身, 40岁+, 管过真预算、坐过战略决策桌、"
    "亲历过业务转型; 格局对标头条/腾讯/阿里量级高管, 说话是老谭的接地气大白话——"
    "把大厂战略语言翻译成牌桌话是本账号核心手艺 (\"格局要大, 说话要接地气\")。毒舌拆书人、"
    "认知挖掘机，读书不是充电打卡是拆穿。锋利、笃定、不绕弯，拒绝鸡汤腔/学术腔/成功学。\n"
    "【核心任务】基于【本集输入】（书名/本集路线图/全书输入/读者反应/素材包/前序覆盖），"
    "按六段结构写一集口播稿：把这本书拆透、讲人话。\n"
    "【受众画像】抖音/B站：25-50岁男性为主的认知升级人群——企业主/集团部门管理者/"
    "小生意人/想升值的职场人，按书性质侧重适配（读书系统适应所有书，禁一根筋）；"
    "关心人性规律、财富逻辑、决策与格局；迁移视角按【本书迁移视角】清单适配。\n"
    "【口吻红线】普遍规律用\"咱们\"、个人建议才用\"你\"；转场极简；禁元叙述/计数预告句；"
    "比喻须与论证严格对应；锋利不刻薄——毒舌书里的花活但不贬损读者，过来人不是教训人。\n"
    "【拆书约束】同一引导词全篇最多1次；每句≤20字；强比喻≤3个；"
    "书中论证为叙事主体（书中内容≥60%），迁移落地≤40%；主线\"你\"全篇=书定读者 L1 不换人，"
    "闲话层沿上下游/上下层两轴搭桥点名（每组必带同构桥句，每集≤2组）；"
    "观点须可追溯来源，书中观点一律\"书中提出/作者认为\"视角限定；"
    "金句转述不挂引号。\n"
    "【钩子】身份认领式：从迁移视角造3~4个「如果…」排比（末个\"甚至\"递进）→段尾下段悬念句；钩子段零书名（书名在引入段价值锚句处首出）；"
    "禁单一痛点开场/人设故事垫场；段落尾落款\"各位书友, 我是老谭, 一本书, 拆透一层人性\"。\n"
    "【第1集特例】引入段=价值锚句（真实落差≥2个具体物: 年份/数字/代名作, 从A到B, 禁抽象）+悬念问句；概念一段=六集动词化预告"
    "（每集差异化动词+半句悬念, 第6集=工具汇总+顺序追更钩）；概念二/三=一口预演禁展开。\n"
    "【拼图感+诊断先行】开头一句话说破本集是第几块拼图(前一块/后一块)；开头半分钟先拿秘籍"
    "给观众做自问诊断再讲书。【培训感三件套】每集：暂停练习×1（视角迁移处，具体到观众业务/账本）；小结口诀"
    "≤14字（收尾独立成行）；微行动（今天5分钟可做）——末集合订六集口诀+行动成册。"
    "收尾段末加转发/收藏CTA（全稿唯一一处、按本集秘籍现造集集不同：对象=本集秘籍痛点人群、"
    "理由带本集工具名、收藏给场景理由，禁「破局/死磕」万金油词；收官集除外——CTA系统注入禁再写）；"
    "非首集回顾段=上集秘籍回声+拼图位+本集诊断问三合一（禁复述腔）；"
    "末集结构倒转：工具串实战+交棒仪式+晒作业，完结语禁下集预告。\n"
    "【六段结构】钩子 → 回顾+引入 → 核心概念拆解（一）→（二）→（三）→ 总结+下期预告，"
    "每段以附加指令给的【A-B秒｜段名】标签行开头，逐字保留。\n"
    "【输出】纯口播文本，末尾另起一段附严格 JSON 块 {\"coverage\":[str]} 列本集知识点，"
    "该块之后禁止再输出任何文字。"
)


def fallback_base(prompt_template: str | None) -> str:
    """兜底基底按persona模板分流 ."""
    if prompt_template and "laotan-book" in prompt_template:
        return _FALLBACK_BASE_LAOTAN
    return _FALLBACK_BASE

# 防幻觉核心四字段: 仅 L0 提取或人工输入可入总纲
_CORE_FIELDS = ("全书核心主张", "关键概念清单", "核心金句", "核心案例")

# 弹性标签六段基准 (270s), 按目标时长等比缩放
# 0913 标签重切对齐蓝图 (用户批): 钩子区 0-11s→0-25s (蓝图秒级设计 0-25s 钩子四拍:
# 断言3s/锤3-12/认领12-20/悬念收口20-25 — 旧标签11s装不下四拍, 钩子被压成清单);
# 总结扩 (兑付+CTA+预告本就挤); 拆解三段压缩并降级为纯时间戳 (结构跟秘籍形态走)
_LABEL_BASE = [
    (0, 25, "钩子"),
    (25, 50, "回顾+引入"),
    (50, 105, "核心概念拆解（一）"),
    (105, 160, "核心概念拆解（二）"),
    (160, 210, "核心概念拆解（三）"),
    (210, 270, "总结+下期预告"),
]
_LABEL_RE = re.compile(r"【(\d+)-(\d+)秒｜([^】]+)】")


def _llm() -> LLMService:
    # 2026-09-09 用户令: kimi 主力 + deepseek 兜底 (统一工厂)
    return get_llm_service()


# B08 (2026-10-16): 函数体迁入 app/services/llm_service._parse_json (anim_pipeline
# 与 book_service 共用的中立底层), 此处 re-export 保持 book 侧旧入口零连锁。
from app.services.llm_service import (
    _parse_json as _parse_json,  # noqa: F401  # re-export
)


def _first_list(data: Any) -> list:
    """LLM 常把数组包进 dict — 取第一个非空 dict 列表值."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def elastic_labels(target: float) -> list[tuple[int, int, str]]:
    """六段结构按目标时长等比缩放 (默认 600s)."""
    k = target / 270.0
    return [(round(a * k), round(b * k), name) for a, b, name in _LABEL_BASE]


# ── 拆解三步与段间承接 (0913b 治段落割裂) ─────────────────────────
# 病根: 拆解三块的内容分配全靠模型即兴 (ep2 把三档总览塞进拆解一, 拆解二新设问
# 重启, 拆解三换轨讲配套工具) — 句级全过、段间无胶水。三层修: 总纲分配步骤
# (拆解三步) + 模板路标句铁律 + 此处确定性承接检测打回。

_CIRCLED_SPLIT_RE = re.compile(r"[①②③④⑤]")


def derive_breakdown_steps(secret: str) -> list[str]:
    """旧总纲兜底: 从本集秘籍的 ①②③ 标记确定性切三步, 不成 3 条返回 [] ."""
    if not secret or not _CIRCLED_SPLIT_RE.search(secret):
        return []
    segs = [s for s in _CIRCLED_SPLIT_RE.split(secret)]
    steps = []
    for s in segs[1:]:
        # 第三步后面常跟"判定后的动作: …"尾部段 — 不属于任何一步, 切掉
        s = re.split(r"判定后", s)[0]
        s = s.strip(" 　:.：，,。；;、")
        if s:
            steps.append(s[:48])
    return steps[:3] if len(steps) >= 3 else []


# 步骤路标词: 拆解块首句的"接棒"信号 (序数推进 / 顺序连词 / 回指)。
# 真实语料形态: "第二档，租来的太多" / "再看回收窗口线" / "第三盏灯" / "招三，交叉补贴"
_SIGNPOST_RE = re.compile(
    r"第[一二三四五六七八九1-9]\s*盏?\s*(?:步|档|条|面|种|块|线|部分|章|个|灯|招|问|关)"
    r"|(?:招|灯|步|档|关|盏)[一二三四五六1-9]"
    r"|先看|先说|再看|再说|接着|其次|然后|最后|回到|上一?[步档面条]|刚才")

# 系统注入行 (人设后处理): 不是模型的语篇, 段间检测须跳过 —
# 实库回放实锤: 身份句落在回顾段末行, 拆解一 bigram 咬合被它挡掉 → 误报
_INJECTED_LINES = {_PERSONA_IDENTITY, _LAOTAN_BOOK_IDENTITY}


def _content_bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）【】《》—…·\-~]", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def fandeng_opening_issues(text: str, paras: list[str]) -> list[str]:
    """樊登开局逐字段校验 (用户想法: ep1 前三段一字不改 — 拼装式开局的质量锚).

    判定: 每段去空白与引号形态 (含 ⟦⟧ 标记; 直引号"与弯引号“”等价 — 弯引号是
    产线字幕/反讽规则要求的形态, 源稿直引号属源文件工件) 后须是成稿的连续子串 —
    LLM 只许把标签行插在句号处, 不许动任何字 (数字形态从严: 365户≠三百六十五户)。
    缺段/改写 → 打回重试。
    """
    issues: list[str] = []
    if not paras:
        return issues
    blob = re.sub(r"[\s⟦⟧\"“”‘’'']", "", text or "")
    for i, para in enumerate(paras, 1):
        needle = re.sub(r"[\s\"“”‘’']", "", para)
        if needle and needle not in blob:
            issues.append(f"樊登开局段{i}未逐字保留 — 一字不改 (标签行只许插在句号处), "
                          f"段首『{para[:16]}…』")
    return issues


_PERSONA_PAST_RE = re.compile(r"这个坑我|我(踩过|当年|带过|管过|签过|做过|入行|那几年|刚工作|头几年|熬过|亏过)")


def persona_anchor_issues(text: str) -> list[str]:
    """ep1 老谭第一人称经历禁令 (用户令: 大钩子集只有三个声音 — 观众处境/书的事实/
    工具承诺; '我踩过/我当年/我管过'式职业回忆零出场, 血肉留给工具集)。命中打回。"""
    hits = [m.group(0) for m in _PERSONA_PAST_RE.finditer(text or "")]
    if hits:
        return [f"ep1 第一人称经历标记 {len(hits)} 处 ({'/'.join(hits[:3])}) — "
                "大钩子集禁老谭职业回忆, 场景血肉只用书里事实和观众处境"]
    return []


def typing_card_issues(card: str, frozen_card: str = "") -> list[str]:
    """打字卡骨架校验 (0914): 冻结卡→逐字一致; 自产卡→骨架三件套。

    骨架: 含 每晚睡前(行为频率) + 超过90%(量化) + 15~60字 + 零内部词
    (九集/系列/工具/判定/行动册 — 零上下文位)。违规打回重试。
    """
    c = (card or "").strip()
    if not c:
        return ["缺打字卡首行 — 首行必须单独输出「打字卡：…」"]
    if frozen_card:
        if c != frozen_card.strip():
            return [f"打字卡与书级冻结卡不一致 — 一字不改照抄「打字卡：{frozen_card.strip()}」"]
        return []
    issues = []
    if "每晚睡前" not in c:
        issues.append("打字卡缺行为频率锚 ('每晚睡前' 式) — 骨架: 如果你能坚持每晚睡前看一集，一个月后，{收益}，会超过90%的{人群}")
    if "超过90%" not in c:
        issues.append("打字卡缺量化对比 ('超过90%' 式)")
    if re.search(r"[二三四五六七八九十\d]{1,2}集|系列|工具|判定|行动册|秘籍", c):
        issues.append("打字卡混入系列内部词汇 (零上下文位, 冷观众秒不懂 — 集数/工具/判定✗; '一集'行为单位可用)")
    if not 15 <= len(c) <= 60:
        issues.append(f"打字卡长度 {len(c)} 不在 15~60")
    return issues


def ep1_opening_order_issues(text: str, book_title: str, verbatim_paras: list[str]) -> list[str]:
    """ep1 开场秩序 (0914 六拍+钩子后移): 报书名须在逐字段之前、钩子段首句不得是逐字段开场。

    判定: 书名《题》在稿中的首次出现位置, 须早于逐字段第1段开头(去空白)的位置;
    且钩子段(首标签段)第一行不得以逐字段第1段开头起手。
    """
    if not verbatim_paras:
        return []
    blob = re.sub(r"\s", "", text or "")
    t = re.sub(r"[《》\s]", "", book_title or "")
    p1_head = re.sub(r"\s", "", verbatim_paras[0])[:10]
    pos_book = blob.find(f"书名{t}") if False else blob.find(t)  # 首次书名(不带书名号也认)
    pos_p1 = blob.find(p1_head)
    issues = []
    if pos_p1 >= 0 and (pos_book < 0 or pos_p1 < pos_book):
        issues.append("钩子秩序: 逐字段开场跑在报书名前面 — 六拍(报书名→宣言→地位→自谦→承诺→认领)先行, 故事钩子后移")
    marks = list(_LABEL_RE.finditer(text or ""))
    if marks:
        seg = text[marks[0].end(): marks[1].start() if len(marks) > 1 else len(text)]
        first_line = next((l.strip() for l in seg.split("\n") if l.strip()), "")
        if p1_head and p1_head in re.sub(r"\s", "", first_line)[:20]:
            issues.append("钩子段首句=逐字段开场 — 首段第一句必须是报书名 ('今天要解读的…《书名》')")
    return issues


def section_flow_issues(text: str, book_title: str = "",
                        *, max_tool_mentions: int = 2,
                        ep_index: int = 0,
                        flow_check: bool | None = None) -> list[str]:
    """段间承接确定性检测 (0913b): 句级校验管不到的语篇层.

    ① 拆解块首句须=路标句 (序数/顺序连词/回指) 或与上块尾句共享 ≥2 个内容 bigram
      (ep2 实锤: "你的绩效，到底谁说了算？"重启 — 无路标无咬合 → 打回);
    ② 工具名全称 (《…》≥4字, 排除书名) 出现 >max_tool_mentions 次 (定名+三件套点名
      之外用回指 — 表名回环=割裂感, ep2 实锤);
    ③ 系列句"X步转型的第Y步" >1 次 (回顾段/总结段二选一)。

    flow_check (0914 结构参数化): 只对**工具集**做① — 大钩子集走开局铁律八拍、
    行动册集走工具页装订, 时间戳边界≠步骤边界, 段首设问/页名开头合法;
    传 None 时按旧规则 (仅 ep_index==1 豁免) 兼容。配额②③全集照查。
    """
    if flow_check is None:
        flow_check = ep_index != 1
    issues: list[str] = []
    text = text or ""
    marks = list(_LABEL_RE.finditer(text))
    if flow_check and len(marks) >= 2:
        blocks = []
        for i, m in enumerate(marks):
            seg = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            lines = [l.strip() for l in seg.split("\n")
                     if l.strip() and l.strip() not in _INJECTED_LINES]
            blocks.append((m.group(3), lines))
        for i in range(1, len(blocks)):
            name, lines = blocks[i]
            if "拆解" not in name or not lines:
                continue
            first = lines[0]
            prev_lines = blocks[i - 1][1]
            last = prev_lines[-1] if prev_lines else ""
            if _SIGNPOST_RE.search(first):
                continue
            if last and len(_content_bigrams(first) & _content_bigrams(last)) >= 2:
                continue
            issues.append(
                f"承接断层: 「{name}」首句『{first[:18]}』无步骤路标词也未咬住上段尾句"
                f"『{last[:18]}』— 首句改路标句 (第X步/再看X式) 或半句回指接上段话头")
    # 工具名全称配额 (书名白名单: 书名合法提及≈2次, 不算工具)
    _title = re.sub(r"[《》\s]", "", book_title or "")
    for name in set(re.findall(r"《([^》]{4,14})》", text)):
        if _title and (_title in name or name in _title):
            continue
        n = text.count(f"《{name}》")
        if n > max_tool_mentions:
            issues.append(f"工具名《{name}》全称出现{n}次>{max_tool_mentions} — "
                          "定名+三件套点名各一处, 其余用回指 ('这张表'式)")
    # 系列句配额: 回顾段与总结段各说一遍 = 复读 (ep2 实锤 ×2);
    # 0914 泛化: 步数随书 (K 件工具 → "X步转型"), 禁写死六
    _series = re.findall(r"[一二三四五六七八九\d]步转型的第[一二三四五六七八九\d]步", text)
    if len(_series) > 1:
        issues.append(f"系列句「{_series[0]}」出现{len(_series)}次 — 回顾段/总结段二选一")
    return issues


def validate_script(text: str, target: float, book_title: str = "",
                    *, require_cta: bool = False, finale: bool = False,
                    ) -> tuple[bool, list[str]]:
    """硬校验: 字数按目标时长换算 + 六标签完整 + 末段终点≈目标±10%.

    0907: 旧硬编码 2700-3000 按 600s 设计 — 老谭读书 300s 时正确长稿被判
    "字数不足"永远打回 (LLM 怎么写都过不了)。改为语速×target,
    带宽 -6%/+0% (宁短勿超)。
    0912: 语速 4.7→4.6 (2.5 说书音色实测锚, ep1 368s/ep5 422s 双证);
    1018 C4a: 4.6→SPEECH_RATE=5.0 (《定位》7 集 full_paragraph.wav 实测 median 4.974,
    旧锚偏慢 8%); 单带改三档 — 软带出带=进修复, 硬线=打回重写级, 平台包络=北斗星第5条。
    字数=弹性工具 (北斗星第3条), 带子存在只为逼 LLM 在板块预算内完成运营任务。
    并加车轱辘话检测 (论点级复读 + 近重复句) — 用户令: 超字数背后藏着同义反复。
    0913: require_cta — 老谭非收官集收尾段须含转发+收藏 CTA (系统结尾已缩为品牌句,
    漏写=这一集没有任何转发号召, 确定性打回让重试自己补)。
    1018 C4c: finale — 非收官集收尾段禁集数计数收口 (ep1 实锤"这7集一集一件工具…
    第7集装订"穿透提示词禁令; 系列导览归导览拍, CTA 规格④只许"第X件")。
    """
    issues: list[str] = []
    body = _LABEL_RE.sub("", text or "")
    n = len(re.sub(r"\s", "", body))
    # 1018 C4a: 三档字数带 (软带→修复 / 硬线→重写级 / 包络=平台带保真); 文案保持"字数 N"前缀
    # (passes.issue_family/word_band_ok 的 `字数 (\d+)` 正则依赖)
    _lo, _hi, _hlo, _hhi = word_band(target or 600)
    if n > _hhi or n < _hlo:
        issues.append(f"字数 {n} {'超硬线 ' + str(_hhi) if n > _hhi else '不足硬线 ' + str(_hlo)}"
                      f" (目标{target:.0f}s → 修复带{_lo}-{_hi}, 硬线{_hlo}-{_hhi})")
    elif not _lo <= n <= _hi:
        issues.append(f"字数 {n} {'超出 ' + str(_hi) if n > _hi else '不足 ' + str(_lo)} (目标{target:.0f}s → {_lo}-{_hi})")
    # 车轱辘话检测 (0912): ①近重复句 (字符 bigram 重叠 ≥65% 的句子对) ②高频复读词
    sents = [s.strip() for s in re.split(r"[。！？\n]", body) if len(s.strip()) >= 10]
    def _bg(s: str) -> set[str]:
        return {s[i:i + 2] for i in range(len(s) - 1)}
    _seen: list[tuple[str, set[str]]] = []
    _dup_examples: list[str] = []
    for s in sents:
        bg = _bg(s)
        for t, bt in _seen:
            if bg and bt and len(bg & bt) / min(len(bg), len(bt)) >= 0.65:
                # 0912: 原句对进 issue — 重试从盲改变点名改
                _dup_examples.append(f"「{t[:24]}」≈「{s[:24]}」")
        _seen.append((s, bg))
    if _dup_examples:
        issues.append("车轱辘话: 以下判断说了两遍, 删旧保新只留一说 — " + "; ".join(_dup_examples[:3]))
    # 6-gram 共现检 (0912 CTA撞衫实锤): 任意两句共享 ≥6 连续字 = 复读
    # (转给一个正在死磕/电影是相亲 类; 比句级 bigram 重叠更稳)
    # 0913 修: 书名滑窗白名单 — 书名合法提及2次, 其 6字滑窗碎片 (BO的内容战…)
    # 曾各算一次"多处出现"三连误报 (ep2-5 实稿句级近重复实为 0, A/B/C 参数对照证清白)
    _title = re.sub(r"[《》\s]", "", book_title or "")
    _gram: dict[str, int] = {}
    _gram_dups: set[str] = set()
    for si, s in enumerate(sents):
        for k in range(len(s) - 5):
            g = s[k:k + 6]
            # 剥书名号再比对 — 「《HBO的内」这类带《前缀的滑窗也是书名碎片
            if _title and re.sub(r"[《》]", "", g) in _title:
                continue
            if g in _gram and _gram[g] != si:
                _gram_dups.add(g)
            elif g not in _gram:
                _gram[g] = si
    if _gram_dups:
        issues.append("复读段: 「" + "」「".join(sorted(_gram_dups)[:3]) + "」出现多处 — 同句/同号召只留一处")
    # 0913 反套路扫描 (ban_list 同源, 确定性): 打数字互动/指定收件人转发/预告形容词
    _ban_re = re.compile(r"[打扣]\s?[1一2二3三]|转给那个[^，。！？]*的人|下一集更[冷狠劲爆猛]")
    _ban_hits = sorted(set(_ban_re.findall(text or "")))
    if _ban_hits:
        issues.append("反套路违规 (ban_list): " + " / ".join(h.strip() for h in _ban_hits[:4])
                      + " — 打数字改共鸣问句/转发送理由/预告用料内悬念")
    # 0928 归因句配额 (用户实锤: ep2 六处"书里写"起手成口头禅 — 讲的就是这本书,
    # 无须反复挂牌): 直引锚段处归因保留, 其余转述变奏 (校验器同步扫描)
    _attr = re.findall(r"书里(?:还写|写|说|警告|提出|给了|强调|指出|举例)", text or "")
    if len(_attr) > 3:
        issues.append(f"归因句堆叠: 「书里写/说/警告」类起手 {len(_attr)} 处 (>3) — 本节目讲的就是"
                      "这本书, 除直引锚段外禁用'书里写'起手; 转述变奏: 德鲁克在书里举了个例子 / "
                      "书里那位董事长 / 按德鲁克的诊断 / 这在书里叫…")
    # 0928 内部黑话扫描: 管线词禁入播稿 (判定钉/反馈钉实锤渗稿) — 说人话
    # 1001 补: 评论钩/微行动 (ep5 "评论钩留给你：" 被念出来实锤 — 注入层术语
    # 被模型当台词回写); 行动册/口诀是观众面词汇不禁
    _jargon_re = re.compile(r"判定钉|反馈钉|铁证钉|证据钉|金句钉|指向陈述|收口句式|论证骨架|案例库|料仓|评论钩|微行动|下集留个悬念|下集预告")
    _jargon_hits = sorted(set(_jargon_re.findall(text or "")))
    if _jargon_hits:
        issues.append("内部黑话渗稿: " + "/".join(_jargon_hits)
                      + " — 管线词说人话 ('判定钉'→'就问一句: 不做会有什么后果'式直给)")
    # 1018 用户令: 评论引导禁贬收藏 — "收藏夹吃灰"式梗以"存而不用"为晒点, 讽刺收藏
    # 行为=降收藏率, 与互动链 (收藏>评论>点赞) 相悖 (ep2 回顾段实锤)
    _deval = re.search(r"收藏夹?吃灰|存了[^。！？\n]{0,10}(才翻|没看|不看|没用|没翻)", text or "")
    if _deval:
        issues.append(f"评论钩贬收藏: 「{_deval.group(0)}」— 报到晒点须是在用/用了/卡在哪, "
                      "禁以'收藏了没用'为梗 (讽刺收藏=降收藏率, 互动链收藏>评论)")
    # 1018 C2 书名错写闸 (R1): 书名提及只许 全名 或 冒号前主名; 与真名用字重叠
    # ≥0.5 的意译名 (《被信任的专业顾问》vs《值得信赖的顾问》— fdopen 无书名字段
    # 意译实锤, 字集重叠 4/7=0.57) → 打回。工具名/他书引用与真名字集重叠低, 不误伤。
    _t_full = re.sub(r"[《》\s]", "", book_title or "")
    if _t_full:
        _t_main = re.split(r"[：:]", _t_full)[0]
        _t_chars = set(_t_full)
        for _nm in set(re.findall(r"《([^《》]{2,24})》", text or "")):
            _mm = re.sub(r"\s", "", _nm)
            if _mm == _t_full or _mm == _t_main:
                continue
            if len(_mm) >= 4 and len(set(_mm) & _t_chars) / min(len(set(_mm)), len(_t_chars)) >= 0.5:
                issues.append(f"书名错写: 《{_nm}》≠《{book_title}》— 提及只许全名或主名"
                              f"《{_t_main}》(拐棍稿书名以提示词顶部为准)")
    # 0913 CTA 集间差异化: 老谭非收官集 — 收尾段须有按本集秘籍现造的转发+收藏 CTA
    # (固定结尾已缩为品牌句, 漏写=这集没有转发号召; 打回后重试在收尾段自补)
    if require_cta:
        _m_tail = re.search(r"【\d+-\d+秒｜总结\+下期预告】(.*)", text or "", flags=re.S)
        _tail_seg = _LABEL_RE.sub("", _m_tail.group(1)) if _m_tail else ""
        if not re.search(r"转给|转发", _tail_seg):
            issues.append("收尾段缺转发CTA — 收尾段末须写转发号召(对象/理由挂钩本集秘籍, 系统结尾不再带)")
        if not re.search(r"收藏|留到|存下|翻出来", _tail_seg):
            issues.append("收尾段缺收藏CTA — 收藏须给场景理由(何时翻出来用本集工具)")
        # 1018 C4c 计数收口闸 (北斗星2): 非收官集收尾段禁集数计数收口 — 三窄 pattern
        # (校准锚 ep1"这7集一集一件工具…第7集装订"); "下一集/下集"预告与"第X件"CTA 不误伤。
        if not finale and _tail_seg:
            _cnt = (re.search(r"[一二三四五六七八九\d]\s*集一集一件", _tail_seg)
                    or re.search(r"第[一二三四五六七八九\d]\s*集[^。！？\n]{0,20}(装订|集齐|拼完|行动册)", _tail_seg)
                    or re.search(r"(全|整)(套|部)[一二三四五六七八九\d]\s*集", _tail_seg))
            if _cnt:
                issues.append(f"收尾计数收口: 「{_cnt.group(0)}」— 非收官集收尾禁集数计数收口; "
                              "系列导览归导览拍, 系列感用'第X件工具'式 (收官集由系统结尾注入)")
    labels = _LABEL_RE.findall(text or "")
    names = [x[2].strip() for x in labels]
    for _a, _b, name in _LABEL_BASE:
        if name not in names:
            issues.append(f"缺标签【{name}】")
    if labels:
        end = max(int(b) for _a, b, _n in labels)
        if abs(end - target) > target * 0.1:
            issues.append(f"末段终点 {end}s 偏离目标 {target:.0f}s ±10%")
    return (not issues, issues)


# 黄金三秒钩子落位闸正交化用: 剥空白与常见标点 (弯直引号/破折号/省略号)
_STRIP_RE = re.compile(r"[\s，。！？、：；⋯…·—\-“”\"'‘’]")


def hook_position_issues(text: str, hook_ref: str) -> list[str]:
    """黄金三秒钩子落位闸 (1018 C4c, 北斗星1/3 — tier-1 吸粉零执法补位)。

    工具集/收官集: 路线图「黄金三秒钩子」的断言头 (剥标点后前 12 字) 须出现在
    钩子段 (首标签段) 前 150 字内 — 开段即出, 留存 3s 法则。ep1 豁免 (拼装式开局
    六拍+打字卡扛前 3 秒; ep1 旧实锤: 痛点钩子埋到 ~25s 处无人管)。
    """
    h = _STRIP_RE.sub("", hook_ref or "")[:12]
    if len(h) < 6:
        return []
    marks = list(_LABEL_RE.finditer(text or ""))
    if len(marks) < 2:
        return []
    seg = _LABEL_RE.sub("", text[marks[0].end():marks[1].start()])
    head = _STRIP_RE.sub("", seg)[:150]
    if h in head:
        return []
    return [f"黄金三秒钩子未落位: 「{(hook_ref or '')[:18]}」未出现在钩子段前150字 — "
            "钩子断言须开段即出 (留存3s法则, 北斗星tier-1)"]


def source_context(book: BookProject, cap: int = 12000) -> str:
    """L0 来源上下文: 精华/原书章节摘要 (cap 内), 无来源返回空串."""
    if not book.source_path:
        return ""
    try:
        parsed = read_book(book.source_path)
    except Exception as exc:
        logger.warning("[book] L0 读取失败 %s: %s", book.source_path, exc)
        return ""
    parts, used = [], 0
    for ch in parsed["chapters"]:
        if used >= cap:
            break
        take = ch["text"][: max(500, cap - used)]
        parts.append(f"【{ch['name']}】{take}")
        used += len(take)
    return "\n".join(parts)


# ── 中国品牌案例闸 (0926 硬核层; 0930 自 episode_gen 迁入 — passes.py 共用) ──
# 0926 硬核层: 中国品牌案例词典 (案例库名 + 公共品牌兜底) — 硬指标确定性闸用
_ZH_BRANDS_BASE = {"美团", "饿了么", "百度外卖", "蒙牛", "伊利", "滴滴", "快的",
                   "加多宝", "王老吉", "娃哈哈", "非常可乐", "拼多多", "元气森林",
                   "瑞幸", "小米", "华为", "比亚迪", "蜜雪冰城", "农夫山泉", "京东", "淘宝"}

_ZH_BIGTECH = {"字节", "字节跳动", "腾讯", "阿里", "阿里巴巴", "蚂蚁", "华为", "美团",
               "京东", "小米", "百度", "拼多多", "网易", "比亚迪", "宁德时代", "大疆",
               "快手", "抖音", "滴滴", "携程", "顺丰", "B站", "哔哩哔哩"}
_JUDGE_WORDS = ("伟大", "神话", "奇迹", "传奇", "碾压", "吊打", "衰落", "没落", "败局",
                "塌房", "翻车", "救星", "罪人", "英雄", "帝国", "最强", "最牛", "完败", "垃圾")
_QUALIFIER_TOKS = ("公开报道", "公开资料", "当年", "早期", "对外披露", "披露", "财报",
                   "公开信", "访谈", "传记", "官方")
# 案例引入句特征词: 首提大厂只有落在"讲它做了什么"的句子里才要求限定词 —
# 平台自指("在抖音上刷到")/生活动作("点个美团外卖")不带这些词, 天然豁免
_CASE_INTRO_RE = re.compile(r"推行|推出|提出|实行|宣布|做法|战略|打法|文化|制度|组织|内部|曾经|起家|发家|转型|改革")
_HYPO_HEAD_RE = re.compile(r"你|如果|假如|假设|要是|想象")   # 品牌前提及=假设句非案例
_RECENT_YEARS_RE = re.compile(r"(?:202[4-9]|20[3-9]\d)年")


def _zh_brands(book_title: str = "") -> set[str]:
    out = set(_ZH_BRANDS_BASE)
    try:
        from app.services.book_service.l0 import _l0_dir as _l0d
        _bp = _l0d(book_title).parent / "案例库.json"
        if _bp.exists():
            for c in (json.loads(_bp.read_text(encoding="utf-8")).get("cases") or []):
                if c.get("region") == "global":
                    continue   # 0926 修: 海外案例名 (安飞士/赫兹) 入词库会让闸误判
                for _tok in re.split(r"对|和|vs|与|:", str(c.get("name") or "")):
                    if len(_tok.strip()) >= 2:
                        out.add(_tok.strip())
    except Exception:  # noqa: BLE001 — 词典缺库不炸校验
        pass
    return out


def _zh_brand_case_issues(text: str, book_title: str = "") -> list[str]:
    """0927 案例口径三则闸 (确定性, 可修级): 判词贴品牌=评企业; 大厂首提句有案例
    动词而无信源限定词; 品牌与2024+年份同句=近两年热点 — 全部可通过加限定词/
    换措辞/换案例修复, 与零中国案例废稿闸同函数族返回."""
    issues: list[str] = []
    brands = _zh_brands(book_title) | _ZH_BIGTECH
    sents = [s for s in re.split(r"[。！？!?\n]+", text or "") if s.strip()]
    for s in sents:
        for w in _JUDGE_WORDS:
            i = s.find(w)
            if i < 0:
                continue
            if any(b in s[max(0, i - 10):i] or b in s[i + len(w):i + len(w) + 12]
                   for b in brands):
                issues.append(f"案例口径违规(不评企业): 判词'{w}'贴着品牌名 — 只描述管理动作"
                              "+它印证的书本观点, 禁评企业伟大/失败/强弱")
                break
    for b in sorted(_ZH_BIGTECH):
        s = next((x for x in sents if b in x), None)
        if not s or any(q in s for q in _QUALIFIER_TOKS):
            continue
        head = s[:s.find(b)]
        if _HYPO_HEAD_RE.search(head) or not _CASE_INTRO_RE.search(s):
            continue
        issues.append(f"案例口径违规(限定词): 首次引入大厂'{b}'的句子缺信源限定词 — 句内加 "
                      "公开报道/当年对外披露/早期 之一 (如'公开报道、当年对外披露，字节早期…')")
    for s in sents:
        if _RECENT_YEARS_RE.search(s) and not _HYPO_HEAD_RE.search(s) \
                and any(b in s for b in brands):
            issues.append("案例口径违规(选题避雷): 品牌案例与2024年后年份同句 — 近两年热点"
                          "争议事件避开, 换已沉淀的公知商业史案例")
            break
    return list(dict.fromkeys(issues))


def _zh_brand_issues(text: str, book_title: str = "") -> list[str]:
    """工具集硬指标 (ep2 多连实锤, 宪法/注入/编辑层皆可丢) + 案例口径三则 (0927):
    零中国品牌=废稿级; 口径违规=可修级 — 同闸返回, 首稿/重试/仲裁/注入四执法点齐查."""
    issues: list[str] = []
    if not any(b in text for b in _zh_brands(book_title) | _ZH_BIGTECH):
        issues.append("缺中国品牌案例视角 (每集硬指标): 须 ≥1 个中国品牌案例 (正面/反面/格局均可) — "
                      "案例库概念匹配优先, 无匹配则从公共商业史提名 (只用公知口径骨架); 零中国案例=废稿")
    return issues + _zh_brand_case_issues(text, book_title)
