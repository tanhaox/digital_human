# -*- coding: utf-8 -*-
"""1019 纯听听感系统能力测试 — 框架词自解释/计数唯一/术语口语化 (豆包诊断 ep2 实锤)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_service.creation_common import _listen_feel_issues
from app.services.compliance import listen_substitute


# ── ep2 真实病灶样本 ──

EP2_HOOK = "《卓有成效的管理者》五步转型，今天讲第一件工具。"


def test_framework_word_first_use_needs_explain():
    """ep2 实锤: '五步转型'首现句无解释 → 报."""
    issues = _listen_feel_issues(EP2_HOOK + "先来做个诊断。")
    assert any("框架词首现无自解释" in i for i in issues)


def test_framework_word_with_explain_passes():
    """豆包改法: '一共五步转型' 同句自解释 → 过."""
    s = "《卓有成效的管理者》里，管理者提升效能一共五步转型，今天讲第一步工具。"
    issues = _listen_feel_issues(s)
    assert not any("框架词首现无自解释" in i for i in issues)


def test_explain_in_previous_sentence_passes():
    """解释在紧邻前一句也算 (首现句±前一句窗口): 总述句带'总共' → 过."""
    s = "管理者提升效能总共分几步？五步转型，今天讲第一步。"
    issues = _listen_feel_issues(s)
    assert not any("框架词首现无自解释" in i for i in issues)


def test_dual_counting_systems_flagged():
    """'五步'与'件工具'两套计数同稿 → 报计数体系混用."""
    s = ("《卓有成效的管理者》五步转型，今天讲第一件工具。\n"
         "这是五步转型第 1 步。前三件工具解决的是别的问题。")
    issues = _listen_feel_issues(s)
    assert any("计数体系混用" in i for i in issues)


def test_single_counting_system_ok():
    """单一计数体系 (只有'步') → 不报混用."""
    s = "提升效能一共五步转型，今天讲第一步。第二步讲减法。"
    issues = _listen_feel_issues(s)
    assert not any("计数体系混用" in i for i in issues)


def test_term_mechanical_substitute():
    """ep2 实锤: 路径依赖 → 思路固化 (出口机械替)."""
    out, hits = listen_substitute("长期单一聚焦，容易形成路径依赖，方向必须定期复盘。")
    assert out == "长期单一聚焦，容易形成思路固化，方向必须定期复盘。"
    assert hits == ["路径依赖"]


def test_rewrite_only_not_substituted_but_flagged():
    """'战略级聚焦'只报警不机械替 (语境改写)."""
    out, hits = listen_substitute("华为资源做战略级聚焦。")
    assert "战略级聚焦" in out and hits == []
    issues = _listen_feel_issues("华为资源做战略级聚焦。")
    assert any("听感术语残留" in i for i in issues)


def test_clean_text_no_issues():
    assert _listen_feel_issues("记录在前，判断在后。先看清时间去了哪。") == []
    assert listen_substitute("干净的口播。") == ("干净的口播。", [])


# ── 1019 B1 定桩升级: 钦定执法 / 逐族首现不遮蔽 / 句内点名标记 ──

def test_pinned_banned_series_noun_flagged():
    """钦定五件工具后, '五步转型'全称出现 → 非钦定."""
    s = "管理者提升效能一共五件工具，今天讲第一件工具。\n后来又提了一句五步转型。"
    issues = _listen_feel_issues(s, series_word="五件工具")
    assert any("非钦定框架词" in i for i in issues)


def test_pinned_canonical_with_explain_passes():
    s = "《卓》里管理者提升效能一共五件工具，今天讲第一件工具。"
    issues = _listen_feel_issues(s, series_word="五件工具")
    assert not any(("框架词首现" in i or "非钦定" in i or "混用" in i) for i in issues)


def test_pinned_generic_step_counter_not_banned():
    """工具内部'第一步/分三步'泛型计数 ≠ 非钦定 (只有族全称被禁)."""
    s = "一共五件工具。第二件工具填法分三步：先辨明，再定边界。"
    issues = _listen_feel_issues(s, series_word="五件工具")
    assert not any("非钦定框架词" in i for i in issues)


def test_per_family_no_masking():
    """旧版只查全局首现: 「第一步」遮蔽「五步转型」; 新版逐族各查 → 各报一条."""
    s = "想升级做事成效，第一件工具先出手。\n五步转型，讲透它。"
    issues = _listen_feel_issues(s)
    assert len([i for i in issues if "框架词首现无自解释" in i]) == 2


def test_sent_mark_jia_naming_passes():
    """豆包修补句式: 首现句内 '叫时间诊断' 点名 → 过 (旧窗口标记全不命中)."""
    s = "《卓》里管理者提升效能的五件工具，今天讲第一件，叫时间诊断。"
    issues = _listen_feel_issues(s, series_word="五件工具")
    assert not any("框架词首现无自解释" in i for i in issues)


def test_resolve_series_word_pin_and_detect():
    from types import SimpleNamespace as NS
    from app.services.book_service.creation_common import resolve_series_word
    # 钦定优先
    assert resolve_series_word(NS(input_json={"灵魂三问": {"系列框架词": "五件工具"}},
                                  episodes=[])) == "五件工具"
    # 无钦定 → 总纲族词多数 (roadmap 里件工具多 → 检测为五件工具)
    b = NS(input_json={"灵魂三问": {}}, episodes=[
        NS(roadmap_json={"承上": "上一件工具: 时间诊断", "启下": "下一件工具"}, title="t"),
        NS(roadmap_json={"承上": "回顾上集", "启下": "预告"}, title="u")])
    assert resolve_series_word(b) == "五件工具"
    # 两无 → None (回退现行为)
    assert resolve_series_word(NS(input_json={}, episodes=[])) is None


# ── 1019 B3: 复读段 8-gram 标定 (14 份实稿回放: ≤7字金句/锚母题误报清零, 真重复保留) ──

def test_repeat_motif_quote_not_flagged():
    """合法金句母题 (6字含引号, 全稿作对比轴复读) ≠ 复读段."""
    from app.services.book_service.creation_common import validate_script
    s = ("开场先立轴：他把“我会什么”挂在嘴边，十年没挪窝。")
    s += "中间讲透贡献。别再问“我会什么”，改成问“我解决什么”，路就宽了。"
    s += "结尾收轴：从“我会什么”到“我解决什么”，就这一步，也是唯一的一步。"
    ok, issues = validate_script(s + "。" * 0, 600, "测试书")
    assert not any("复读段" in i for i in issues)


def test_repeat_true_7char_still_flagged():
    """真重复 (≥7 连续字跨句再现) 仍报."""
    from app.services.book_service.creation_common import validate_script
    s = "第一段先把结论放这儿：连续五六个小时初稿才能出来，别不信。"
    s += "第二段补证据：连续五六个小时初稿才能出来，这是硬门槛。"
    ok, issues = validate_script(s, 600, "测试书")
    assert any("复读段" in i for i in issues)
