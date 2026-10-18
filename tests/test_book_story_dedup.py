"""故事库跨集排重三件套测试 (2026-10-19 用户三案令):
① 喂料剔除已用条目 (_drop_used_stories)
② 兜底闸按标题查重, 工具集阻断/头尾集提示 (_story_dup_issues)
③ 确认闸: 阻断级问题未解决禁 confirm (episode_gen.confirm_episode)
锚词规则: 只取主体人名; 滤"某/一位"起手与角色词; 跨条目重复名不作锚;
职业前后缀剥离 (牙医安德鲁→安德鲁); 短名是长名子串时归并到长名条目。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.services.book_service import episode_gen as eg

_LIB = """# 故事库
**【大卫·福克与迈克尔·乔丹的经纪关系】**
背景：经纪人与球星。
主体：大卫·福克。
决策与转折：福克两次主动减免费用。
结果：赢得长期信任。
启示：预判需求。

**【电视制片人汤玛斯与阿特金森的效率对比】**
背景：纪录片行业。
主体：汤玛斯、阿特金森。
决策与转折：汤玛斯两三页简报即谈成。
结果：数量更多更高效。
启示：信任减少流程。

**【外科医生的专业悖论】**
背景：罗伯特的女儿手术。
主体：罗伯特、苏珊、外科医生。
决策与转折：医生把女孩叫成他。
结果：技术服人不靠近。
启示：亲近感。

**【大卫·梅斯特与牙医安德鲁的信任建立】**
背景：初诊沟通。
主体：大卫·梅斯特、牙医安德鲁。
决策与转折：倾听式提问。
结果：信任建立。
启示：倾听。

**【吉姆·柯波兰德赢得信任】**
背景：审计现场。
主体：吉姆·柯波兰德。
决策与转折：先认错再谈。
结果：客户回头。
启示：坦承。

**【吉姆的记忆挑战】**
背景：名字记不住。
主体：吉姆。
决策与转折：记名字训练。
结果：客户感到被重视。
启示：名字。

**【一位律师的真情流露】**
背景：庭审之外。
主体：罗伯特、丈夫、妻子、资深合伙人。
决策与转折：情绪外露。
结果：反而更信任。
启示：真实。
"""


def _amap(noise: str = "大卫·梅斯特"):
    return eg._story_anchor_map(_LIB, noise=noise)


def test_anchor_rules():
    a = _amap()
    by_title = {t: v for t, v in a.values()}
    # 人名成锚; 角色词/某一起手不作锚; 罗伯特跨条目重复 → 频次滤除
    # (外科医生剥"医生"后缀得"外科" — 同条目附带锚, 只增召回)
    assert by_title["外科医生的专业悖论"] == {"外科", "外科医生", "苏珊"}
    assert by_title["电视制片人汤玛斯与阿特金森的效率对比"] == {"汤玛斯", "阿特金森"}
    # 职业前后缀剥离: 牙医安德鲁 → 安德鲁; 复合全串同锚
    assert by_title["大卫·梅斯特与牙医安德鲁的信任建立"] == {"牙医安德鲁", "安德鲁"}
    # 吉姆仅此一条主体为裸名 → 单锚; 柯波兰德条目锚为复合全串
    assert by_title["吉姆的记忆挑战"] == {"吉姆"}
    assert by_title["吉姆·柯波兰德赢得信任"] == {"吉姆·柯波兰德"}
    # 主体全是角色词 → 无锚 fail-open
    assert by_title["一位律师的真情流露"] == set()
    # 书元词 (作者名) 不作锚
    assert "大卫·梅斯特" not in by_title["大卫·梅斯特与牙医安德鲁的信任建立"]


def test_first_entry_not_lost_when_header_same_para():
    # 文件头标题行与首条目同段 (真库实锤: 福克乔丹整条漏检) — search 不得漏
    assert len(_amap()) == 7
    assert _amap()[0][0] == "大卫·福克与迈克尔·乔丹的经纪关系"


def test_drop_used_stories():
    used = "汤玛斯两三页就谈成。苏珊在手术室外面等着。反过来有个牙医，安德鲁。"
    keep, dropped = eg._drop_used_stories(_LIB, used, noise="大卫·梅斯特")
    assert dropped == ["电视制片人汤玛斯与阿特金森的效率对比",
                       "外科医生的专业悖论",
                       "大卫·梅斯特与牙医安德鲁的信任建立"]
    assert "汤玛斯" not in keep
    assert "安德鲁" not in keep
    assert "福克" in keep  # 未用条目保留


def test_hit_stories_substring_merge():
    # 讲"吉姆·柯波兰德"不应误判"吉姆的记忆挑战"也讲过
    a = _amap()
    titles = {i: t for i, (t, _) in a.items()}
    hit = eg._hit_stories(a, "吉姆·柯波兰德先认了错。")
    assert [titles[i] for i in hit] == ["吉姆·柯波兰德赢得信任"]


def _mk_book():
    eps = [
        NS(ep_index=1, script_text="汤玛斯两三页简报就谈成，效率对比惊人。"),
        NS(ep_index=2, script_text="外科医生出来报平安，苏珊皱眉。"),
        NS(ep_index=3, script_text="再看外科医生的故事，苏珊又出现。"),
        NS(ep_index=4, script_text="新案例，无重复。"),
        NS(ep_index=5, script_text="收官。"),
    ]
    return NS(book_title="测试书", author="", episodes=eps)


def test_dup_issues_tool_ep_blocks_head_eps_hint(monkeypatch):
    monkeypatch.setattr("app.services.book_service.distiller.load_section",
                        lambda title, sec: _LIB if sec == "故事库" else "")
    book = _mk_book()
    # ep3 (工具集) 重讲 ep2 的外科医生 → 阻断
    out = eg._story_dup_issues(book, NS(ep_index=3), book.episodes[2].script_text)
    assert out and all(s.startswith("案例跨集重复(阻断): 「外科医生的专业悖论」已由第2集") for s in out)
    # ep1 (头集) 重讲 ep2 的外科医生 → 提示不阻断
    out1 = eg._story_dup_issues(book, NS(ep_index=1), "外科医生的故事在第二集。苏珊。")
    assert out1 and out1[0].startswith("案例跨集重复(提示):")


def test_confirm_episode_blocked_by_hard_dup():
    ep = NS(roadmap_json={"_issues": ["案例跨集重复(阻断): 「X」已由第2集讲过"]},
            status="draft", book=NS(episodes=[]))
    with pytest.raises(ValueError, match="确认被阻断"):
        eg.confirm_episode(None, ep)  # type: ignore[arg-type]
    ep_ok = NS(roadmap_json={"_issues": ["案例跨集重复(提示): 「X」已由第2集讲过"]},
               status="draft", book=NS(episodes=[NS(status="draft")]))
    db = NS(commit=lambda: None)
    assert eg.confirm_episode(db, ep_ok).status == "confirmed"  # type: ignore[arg-type]


# ── coverage 模糊查重 (1019 升格: fail-open 故事的确定性兜底网) ──

def _mk_cov_book():
    def _e(ep, cov, concepts):
        return NS(ep_index=ep, coverage_json=cov,
                  roadmap_json={"概念": concepts}, script_text="x")
    return NS(book_title="测试三书", episodes=[
        _e(1, ["信任等式", "梅斯特立遗嘱选律师案例", "系列行动册"], ["信任等式"]),
        _e(2, ["大卫绩效评估被解雇案例", "大卫统计学教学措辞反思", "低防御措辞三句话",
               "五步转型第1步"], ["技术至上论"]),
        _e(3, ["梅斯特绩效评估被解雇教训", "信任等式：可信度与亲近感为分子",
               "华为客户导向案例（公开报道：《华为基本法》表述客户价值）",
               "低防御措辞我讲清楚了吗"], ["信任等式"]),
        _e(4, ["华为早期海外技术服务公开报道案例", "五步转型第3步"], ["先付出后收获"]),
        _e(5, ["收官"], []),
    ])


def test_coverage_dup_fuzzy_pair_and_tag(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.book_service.l0._l0_dir", lambda t: tmp_path / t)
    book = _mk_cov_book()
    # ep2 工具集: 大卫绩效评估被解雇 ≈ ep3 梅斯特绩效评估被解雇 (bigram≥6) → 阻断;
    # 低防御措辞×2 / 五步转型第N步 = 工具名/脚手架序号 (bigram=4) → 误报带放行
    out = eg._coverage_dup_issues(book, book.episodes[1])
    assert any(s.startswith("案例跨集重复(阻断): 「大卫绩效评估被解雇案例」与第3集") for s in out)
    assert not any("低防御" in s or "五步转型" in s for s in out)
    # ep1 头集: 遗嘱律师 vs ep2? 无; vs ep3 无 → 但系列行动册/信任等式=概念豁免 → 无
    out1 = eg._coverage_dup_issues(book, book.episodes[0])
    assert out1 == [] or all("(提示)" in s for s in out1)


def test_coverage_dup_concept_escape_and_brand(monkeypatch, tmp_path):
    (tmp_path / "测试三书").mkdir(parents=True, exist_ok=True)
    (tmp_path / "测试三书" / "案例库-本书.json").write_text(
        '{"cases": [{"name": "华为"}]}', encoding="utf-8")
    monkeypatch.setattr("app.services.book_service.l0._l0_dir", lambda t: tmp_path / t)
    book = _mk_cov_book()
    # ep3: 信任等式定义 = 概念型豁免; 梅斯特被解雇 ≈ ep2 → 阻断; 华为 vs ep4 → 品牌名单命中
    out = eg._coverage_dup_issues(book, book.episodes[2])
    assert any("梅斯特绩效评估被解雇教训」与第2集" in s and "(阻断)" in s for s in out)
    assert any("华为客户导向案例" in s and "第4集" in s for s in out)
    assert not any("信任等式" in s for s in out)  # 概念跨集复现不拦
    # ep4 工具集: 华为 vs ep3 → 阻断
    out4 = eg._coverage_dup_issues(book, book.episodes[3])
    assert any("(阻断)" in s and "第3集" in s for s in out4)
