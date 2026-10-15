"""拆书线段间承接测试 (2026-09-13b): 治"三五句挺好、段落间割裂"的三件套 —
拆解三步兜底解析 (①②③) + 段间承接确定性检测 (路标句/bigram 咬合/工具名配额/系列句)。
校准锚: 实库回放 ep2 命中 3 处 (用户投诉集), ep1/3/5/6 CLEAN (无过度执法)。
"""
from __future__ import annotations

from app.services.book_service.creation_common import (
    derive_breakdown_steps,
    section_flow_issues,
)

_LABELS = ("【0-28秒｜钩子】", "【28-56秒｜回顾+引入】", "【56-117秒｜核心概念拆解（一）】",
           "【117-178秒｜核心概念拆解（二）】", "【178-233秒｜核心概念拆解（三）】",
           "【233-300秒｜总结+下期预告】")


def _script(b1_first: str, b2_first: str, b3_first: str,
            b1_last: str = "人家敢露脸吗？", b2_last: str = "标准一换，少一半争吵。",
            prev_last: str = "广告主、用户、补贴，三档口袋各一条路。",
            tail: str = "这是六步转型的第二步。") -> str:
    return "\n".join([
        _LABELS[0], "你那条爆款，掏钱的人压根没看。",
        _LABELS[1], "上集我们留了个问句。", prev_last,
        _LABELS[2], b1_first, "这张表叫《付费主体判定表》。", b1_last,
        _LABELS[3], b2_first, "用户按月付费。", b2_last,
        _LABELS[4], b3_first, "调研问的是愿望。",
        _LABELS[5], tail, "转给一个正在想要破局的朋友。",
    ])


# ── derive_breakdown_steps: 旧总纲秘籍 ①②③ 确定性兜底 ──

def test_derive_steps_circled_markers():
    s = ("「看口袋」→《付费主体判定表》：①广告主买单→追最大公约数（《浩劫后》零广告）；"
         "②终端用户买单→订阅摆脱收视率；③平台补贴买单→算断补成本线。"
         "判定后的动作：广告主型→季度体检。")
    steps = derive_breakdown_steps(s)
    assert len(steps) == 3
    assert steps[0].startswith("广告主买单")
    assert steps[2].startswith("平台补贴买单")
    assert "判定后的动作" not in steps[2]  # 尾部动作段不属于任何一步


def test_derive_steps_needs_three_markers():
    assert derive_breakdown_steps("①只有一档②两档") == []
    assert derive_breakdown_steps("无标记的秘籍") == []
    assert derive_breakdown_steps("") == []


# ── section_flow_issues: 段间承接确定性检测 ──

def test_flow_signpost_openers_pass():
    """序数/顺序路标词开头 (ep3/ep4/ep6 真实形态) → 不报。"""
    t = _script("先看第一档，广告主买单。", "第二档，租来的太多。",
                "第三盏灯：你账上的钱，多少是租来的。")
    assert section_flow_issues(t, book_title="X书") == []


def test_flow_reversed_signpost_pass():
    """"招三，交叉补贴" (名+数倒装, ep5 形态) → 不报。"""
    t = _script("先看第一招，换用法。", "招三，交叉补贴。", "最后，多路复用。")
    assert section_flow_issues(t, book_title="X书") == []


def test_flow_bigram_bite_pass():
    """无路标词但首句与上块尾句共享 ≥2 bigram (ep2 拆解一实况) → 不报。"""
    t = _script("看清谁买单，先分清三档口袋。",
                b2_first="标准一换，问法也要换。",
                b3_first="调研问的是愿望，不是答案。",
                b1_last="先换标准，再换问法。",
                b2_last="调研给的是行为，不是态度。",
                prev_last="广告主、用户、补贴，三档口袋各一条路。")
    issues = section_flow_issues(t, book_title="X书")
    assert issues == []


def test_flow_restart_question_flagged():
    """ep2 病灶: 新设问重启块, 无路标无咬合 → 报承接断层。"""
    t = _script("看清谁买单，先分清三档口袋。",
                b2_first="你的绩效，到底谁说了算？",
                b3_first="货选对了，还有一层坑。")
    issues = section_flow_issues(t, book_title="X书")
    assert any("核心概念拆解（二）" in i and "你的绩效" in i for i in issues)
    assert any("核心概念拆解（三）" in i and "货选对了" in i for i in issues)


def test_flow_identity_line_skipped():
    """系统注入身份句落在回顾段末行, 不许挡掉拆解一的 bigram 咬合 (误报修复)。"""
    t = "\n".join([
        _LABELS[0], "你那条爆款，掏钱的人压根没看。",
        _LABELS[1], "广告主、用户、补贴，三档口袋各一条路。",
        "我是老谭，读一本书，多一个硬本事。",
        _LABELS[2], "看清谁买单，先分清三档口袋。", "这张表叫《付费主体判定表》。",
        _LABELS[3], "第二档，租来的太多。",
        _LABELS[4], "第三档，自持够高。",
        _LABELS[5], "这是六步转型的第二步。",
    ])
    assert section_flow_issues(t, book_title="X书") == []


def test_flow_tool_name_quota():
    """工具名全称 >2 次 (定名+点名之外复述) → 报; 书名不算工具。"""
    t = _script("先看第一档。", "第二档，租来的。",
                "第三档，自持。",
                tail="《付费主体判定表》帮你圈出来。这是六步转型的第二步。")
    t = t.replace("调研问的是愿望。", "回到《付费主体判定表》。")
    issues = section_flow_issues(t, book_title="X书")
    assert any("《付费主体判定表》" in i and "全称出现3次" in i for i in issues)
    # 书名白名单: 书名提及 3 次不算工具
    t2 = t.replace("《付费主体判定表》。", "《X书》。").replace(
        "《付费主体判定表》帮你圈出来", "《X书》帮你圈出来").replace(
        "回到《付费主体判定表》。", "回到《X书》。")
    assert not any("全称出现" in i for i in section_flow_issues(t2, book_title="X书"))


def test_flow_series_phrase_quota():
    """系列句"六步转型的第X步" ×2 (回顾段+总结段各一遍, ep2/ep4 实锤) → 报。"""
    t = _script("先看第一档。", "第二档，租来的。", "第三档，自持。",
                tail="这是六步转型的第二步。完整行动册在第六集等你。")
    t = t.replace("上集我们留了个问句。", "上集我们留了个问句。这是六步转型的第二步。")
    issues = section_flow_issues(t, book_title="X书")
    assert any("系列句" in i and "出现2次" in i for i in issues)
    # 只说一遍 → 不报
    assert not any("系列句" in i for i in section_flow_issues(
        _script("先看第一档。", "第二档，租来的。", "第三档，自持。"), book_title="X书"))


def test_fandeng_opening_verbatim():
    """樊登开局逐字段 (用户想法: ep1 前三段一字不改): 标签行插句号处+⟦⟧标记=CLEAN;
    换词/缺段=打回。"""
    from app.services.book_service.creation_common import fandeng_opening_issues
    p1 = "四十多年前，HBO起步时只有365户。今天它占九成份额。"
    p2 = "要理解转型，得回到沟通的起点。"
    ok = "【0-33秒｜钩子】\n打字卡：每晚一集。\n⟦" + p1 + "⟧\n【33-67秒｜回顾+引入】\n缝。⟦" + p2 + "⟧"
    assert fandeng_opening_issues(ok, [p1, p2]) == []
    assert fandeng_opening_issues(ok.replace("365户", "三百六十五户"), [p1, p2])
    assert fandeng_opening_issues(ok, [p1, p2, "没出现的段"])
    assert fandeng_opening_issues("任意稿", []) == []
    # 引号形态等价: 源稿直引号 vs 成稿弯引号 (产线字幕规则) — 放行
    p3 = "成了\"美剧之王\"的代名词。"
    out3 = "成了“美剧之王”的代名词。"
    assert fandeng_opening_issues(out3, [p3]) == []
    # 数字形态从严: 365户 ≠ 三百六十五户 — 打回
    assert fandeng_opening_issues("手里只有三百六十五户。", ["手里只有365户。"])


def test_typing_card_and_opening_order():
    """打字卡骨架校验 + ep1 开场秩序 (六拍先行, 逐字段后移)。"""
    from app.services.book_service.creation_common import typing_card_issues, ep1_opening_order_issues
    good = "如果你能坚持每晚睡前看一集，一个月后，让用户一直愿意掏钱的本事，会超过90%的做内容的人。"
    assert typing_card_issues(good) == []
    # 冻结卡 → 逐字一致才过
    assert typing_card_issues(good.replace("90%", "89%"), frozen_card=good)
    assert typing_card_issues(good, frozen_card=good) == []
    # 自产骨架违规: 缺行为锚 / 缺量化 / 内部词 / 缺卡
    assert typing_card_issues("九集一集一件工具，拿回定价权")
    assert typing_card_issues("如果你能坚持每晚看一集，一个月后，财商会超过九成人")  # 缺 每晚睡前+超过90%
    assert typing_card_issues("")
    # 开场秩序: 报书名先于逐字段
    p1 = "四十多年前，HBO在北美刚刚起步的时候，手里只有365户订阅用户。"
    ok_order = "【0-28秒｜钩子】\n今天咱们要解读的是——《HBO的内容战略》。\n它不是传记。\n\n【28-56秒｜回顾+引入】\n" + p1
    assert ep1_opening_order_issues(ok_order, "HBO的内容战略", [p1]) == []
    bad_order = "【0-28秒｜钩子】\n" + p1 + "\n\n【28-56秒｜回顾+引入】\n书名《HBO的内容战略》"
    assert ep1_opening_order_issues(bad_order, "HBO的内容战略", [p1])


def test_ep1_persona_past_ban():
    """ep1 老谭第一人称经历禁令: '我踩过/我当年'式职业回忆打回; 普通第一人称(我把这书读薄了)放行。"""
    from app.services.book_service.creation_common import persona_anchor_issues
    assert persona_anchor_issues("这个坑我踩过。\n合同条条压着保底量。")
    assert persona_anchor_issues("我当年管过五十人的部门。")
    assert persona_anchor_issues("") == []
    assert persona_anchor_issues("我把这书读薄了，就一句话。") == []   # 消化感第一人称合法
    assert persona_anchor_issues("咱们接着说。") == []
