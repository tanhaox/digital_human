"""passes.snapshot 的 opening 组合件测试 (2026-10-18 C4b, R5):
樊登逐字 + ep1 开场秩序检查此前只在首稿/重试点查, 终稿复检不含 → 人闸问题单漏报
(ep1 终稿实犯"fd段被改写"但 roadmap _issues 不显示)。现纳入 snapshot 可组合项。
"""
from __future__ import annotations

from app.services.book_service.passes import snapshot

_TITLE = "值得信赖的顾问：成为客户心中无可替代的人"
_PARAS = [
    "咱们今天聊的这本书，讲的是专业顾问如何真正赢得客户信任的心法。",
    "这本书的作者是大卫·梅斯特，他在专业服务领域摸爬滚打几十年。",
]


def _snap(text: str) -> list[str]:
    return snapshot(text, target=300, book_title=_TITLE,
                    verbatim_paras=_PARAS, include=("opening",))["opening"]


def _script(hook: str) -> str:
    return "\n".join([
        "【0-28秒｜钩子】",
        hook,
        "【28-56秒｜回顾+引入】", "上集留了个问句。",
        "【56-117秒｜核心概念拆解（一）】", "先看第一个概念。",
        "【117-178秒｜核心概念拆解（二）】", "再看第二个。",
        "【178-233秒｜核心概念拆解（三）】", "最后第三个。",
        "【233-300秒｜总结+下期预告】", "下集聊聊。",
    ])


def test_opening_clean_when_paras_verbatim_and_book_first():
    """报书名在锁段前 + 锁段逐字在场 → opening 无 issue。"""
    hook = f"今天要解读的是《{_TITLE}》。{_PARAS[0]}{_PARAS[1]}"
    assert _snap(_script(hook)) == []


def test_opening_flags_rewritten_para():
    """锁段被改写 (非连续子串) → fandeng 逐字检查报缺段/改写。"""
    hook = f"今天要解读的是《{_TITLE}》。咱们今天聊的这本书讲的是顾问赢得客户信任的心法。" \
           f"书的作者是梅斯特，摸爬滚打了几十年。"
    issues = _snap(_script(hook))
    assert issues, "改写锁段未被 opening 捕获"


def test_opening_flags_fd_before_bookname():
    """锁段跑在报书名之前 → ep1 开场秩序 issue (钩子秩序)。"""
    hook = f"{_PARAS[0]}{_PARAS[1]}今天要解读的是《{_TITLE}》。"
    issues = _snap(_script(hook))
    assert any("钩子秩序" in i for i in issues)


def test_snapshot_opening_noop_without_paras():
    """无锁段 (非 ep1) → opening 组合件零 issue, 不误伤。"""
    s = snapshot(_script(f"今天要解读的是《{_TITLE}》。断言开头。"),
                 target=300, book_title=_TITLE, verbatim_paras=[], include=("opening",))
    assert s["opening"] == []
