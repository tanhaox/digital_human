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
