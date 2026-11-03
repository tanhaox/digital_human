# -*- coding: utf-8 -*-
"""S1 排程闸 (1103): 概念近义撞车 + 相邻集秘籍同型 — _validate 扩展的单测.

实锤锚: 《值得信赖的顾问》ep2「包容的专业精神」vs ep6「包容的专业主义」两字之差
逃过精确交集; ep1/ep2 秘籍同为"抛预制问句+听"操作符 (五件工具账本区分度塌)。
回放基准: 存量书 7 集真值跑新检查 → 恰好命中 2 项, 无第三项 (假阳性受控)。
"""
from app.services.book_service.creation_common import concept_core
from app.services.book_service.series_outline import _validate

SOUL = {"core": "c", "who": "w", "gain": "g"}

# ── 存量书真值 (data/pipeline.db book 98686658, 1103 取数) ──
REAL_CONCEPTS = {
    1: ["值得信赖的顾问", "专业服务提供者"],
    2: ["技术至上论", "包容的专业精神", "排他的专业主义"],
    3: ["信任等式", "自我导向", "亲近感"],
    4: ["赢得权利", "感性界定", "反馈式倾听"],
    5: ["共同承诺", "构想另外一种情景", "期望管理"],
    6: ["科伦坡式咨询方法", "客户差异化辨识", "包容的专业主义"],
    7: ["信任建立五阶段", "服务即营销", "问题界定价值论"],
}
REAL_SECRETS = {
    1: "每次提交方案前，先问客户一句'您最担心这个方案带来什么后果'，用这句话把专业建议翻译成客户的安全感。",
    2: "开会时强迫自己闭嘴前三分钟，只问'关于这个问题，您目前看到了哪些我没看到的信息'，用提问替代说教。",
    3: "见客户前在笔记本写下'我今天绝不提自己的业绩和头衔'，把自我导向的分母强制清零。",
    4: "客户说完诉求后，用'听起来这件事让您感到……'的句式复述一遍情绪，确认房间里的大象再给方案。",
    5: "把提案里的'我建议'全部替换为'如果我们共同承担这个风险，下一步是……'，逼客户和你绑定在同一张赌桌上。",
    6: "遇到办公室政治施压时，立刻收起专家姿态，用'我有点没看懂这里的逻辑，您能教教我吗'来夺取控制权。",
    7: "从今天起，每次沟通结束后的24小时内，给客户发一条只包含一个具体行动点的跟进信息，用微习惯锁死信任。",
}


def _eps(concepts: dict[int, list], secrets: dict[int, str], n: int = 7) -> list[dict]:
    """结构合法最小集 (其余字段沿用 test_book_audience_map._struct_eps 骨架)."""
    base = {"主题": "x", "核心任务": "", "承上": "y", "启下": "z",
            "拆解三步": ["①短语甲", "②短语乙", "③短语丙"],
            "闲话层": [{"轴": "上下层", "group": "业务负责人"}]}
    eps = [dict(base, ep=i, 概念=concepts.get(i, ["a"]), 本集秘籍=secrets.get(i, "秘籍判定表一二三"))
           for i in range(1, n + 1)]
    eps[0] = dict(eps[0], 核心任务="大钩子: 收益承诺+导览钩住人", 拆解三步=[])
    eps[-1] = dict(eps[-1], 核心任务="行动册装订: 全工具合订", 拆解三步=[], 闲话层=[])
    return eps


# ── concept_core 归一 ──

def test_concept_core_strips_type_suffix():
    assert concept_core("包容的专业精神") == "包容的专业"
    assert concept_core("包容的专业主义") == "包容的专业"
    assert concept_core("技术至上论") == "技术至上"
    assert concept_core("信任等式") == "信任"
    assert concept_core("科伦坡式咨询方法") == "科伦坡式咨询"


def test_concept_core_keeps_short_remainder():
    """剥后 <2 字 = 概念本身就是后缀词, 保留原样 (防「理性」→「理」误伤)."""
    assert concept_core("理性") == "理性"
    assert concept_core("信任感") == "信任"


# ── 概念近义撞车 ──

def test_near_dup_concepts_flagged():
    iss = _validate({"soul": SOUL, "episodes": _eps(REAL_CONCEPTS, REAL_SECRETS)})
    assert any("概念近义撞车" in i and "ep2/ep6" in i for i in iss), iss


def test_exact_dup_still_flagged():
    cs = {1: ["信任等式"], 2: ["信任等式"]}
    cs.update({k: [f"独有概念{k}{j}" for j in range(3)] for k in range(3, 8)})
    iss = _validate({"soul": SOUL, "episodes": _eps(cs, REAL_SECRETS)})
    assert any("概念重叠" in i and "ep1/ep2" in i for i in iss), iss


def test_contrast_concepts_pass():
    """书内对立概念 (排他 vs 包容) 跨集合法 — 归一后核心不同, 放行."""
    cs = {1: ["独有概念甲"], 2: ["排他的专业主义"], 3: ["包容的专业主义"]}
    cs.update({k: [f"独有概念{k}{j}" for j in range(3)] for k in (1, 4, 5, 6, 7)})
    iss = [i for i in _validate({"soul": SOUL, "episodes": _eps(cs, REAL_SECRETS)})
           if "近义" in i]
    assert iss == [], iss


def test_containment_near_dup_flagged():
    cs = {1: ["科伦坡式"], 2: ["科伦坡式咨询"]}
    cs.update({k: [f"独有概念{k}{j}" for j in range(3)] for k in range(3, 8)})
    iss = _validate({"soul": SOUL, "episodes": _eps(cs, REAL_SECRETS)})
    assert any("概念近义撞车" in i for i in iss), iss


def test_short_core_containment_passes():
    """短核 (2字) 不触发包含 — 「信任」vs「信任建立五阶段」跨集合法."""
    cs = {1: ["信任"], 2: ["信任建立五阶段"]}
    cs.update({k: [f"独有概念{k}{j}" for j in range(3)] for k in range(3, 8)})
    iss = [i for i in _validate({"soul": SOUL, "episodes": _eps(cs, REAL_SECRETS)})
           if "近义" in i]
    assert iss == [], iss


# ── 相邻集秘籍同型 ──

def test_adjacent_same_operator_secrets_flagged():
    iss = _validate({"soul": SOUL, "episodes": _eps(REAL_CONCEPTS, REAL_SECRETS)})
    assert any("秘籍同型" in i and "ep1/ep2" in i for i in iss), iss


def test_distant_same_operator_passes():
    """远集同操作符 = 呼应, 合法 (只查相邻)."""
    secrets = dict(REAL_SECRETS)
    secrets[1] = "每次提交前写下三行备忘，锁定本次沟通的唯一目标。"  # ep1 改写下式
    secrets[2] = REAL_SECRETS[5]                                   # ep2 改替换式 (无问词)
    secrets[3] = REAL_SECRETS[1]                                   # ep1 式问句秘籍挪 ep3
    cs = {k: [f"独有概念{k}{j}" for j in range(3)] for k in range(1, 8)}
    iss = [i for i in _validate({"soul": SOUL, "episodes": _eps(cs, secrets)})
           if "同型" in i]
    assert iss == [], iss


def test_quote_without_ask_passes():
    """ep6 式 (引语示弱, 无问词) 不算同型."""
    secrets = dict(REAL_SECRETS)
    secrets[2] = REAL_SECRETS[6]  # ep2 换成 ep6 式
    cs = {k: [f"独有概念{k}{j}" for j in range(3)] for k in range(1, 8)}
    iss = [i for i in _validate({"soul": SOUL, "episodes": _eps(cs, secrets)})
           if "同型" in i]
    assert iss == [], iss


# ── 存量书回放总闸 (假阳性受控基准) ──

def test_real_book_replay_exactly_two_new_issues():
    """7 集真值 → 新检查恰好命中 2 项 (ep2/ep6 近义 + ep1/ep2 同型), 无第三项."""
    iss = [i for i in _validate({"soul": SOUL, "episodes": _eps(REAL_CONCEPTS, REAL_SECRETS)})
           if "近义" in i or "同型" in i]
    assert len(iss) == 2, iss
