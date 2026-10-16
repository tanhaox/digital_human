"""受众两层测试 (2026-09-14): L1 书定主线 + L2 两轴闲话池 (上下游/上下层)。

设计: 用户令 — 大纲双面 (内容面=拆解三步 × 人群面=闲话层); L2 池两组
(上下游=价值链同构, 上下层=组织同构), 每组带同构桥, 桥通过
"你不做这行也一样——"句式检验。锚: HBO 实跑三轮校准 (书内cast平移漂移→观众世界指称)。
"""
from __future__ import annotations

from pathlib import Path

from app.services.book_service import creation_steps as cs
from app.services.book_service.series_outline import _norm_chatter

# ── _norm_chatter: 闲话层归一 (导演输出容错) ──

def test_norm_chatter_dicts():
    raw = [{"轴": "上下层", "group": "拍板预算的业务负责人", "bridge": "买流量vs养内容签字分钱"},
           {"轴": "上下游", "group": "平台运营", "bridge": "被自家算法KPI摆布"}]
    out = _norm_chatter(raw)
    assert len(out) == 2
    assert out[0]["轴"] == "上下层" and out[1]["轴"] == "上下游"
    assert out[0]["bridge"].startswith("买流量")


def test_norm_chatter_shorthand_string():
    """简写 "轴·组; 轴·组" (UI/手填形态) 也能归一。"""
    out = _norm_chatter("上下层·背着KPI的团队长；上下游·管投放预算的市场负责人")
    assert [o["轴"] for o in out] == ["上下层", "上下游"]
    assert out[0]["group"] == "背着KPI的团队长"


def test_norm_chatter_axis_recovery_and_caps():
    """轴名漂移但串里含合法轴 → 认轴; 非法轴丢弃; 超量截到 2 条。"""
    assert _norm_chatter([{"group": "平台运营"}]) == []  # 无轴线索 → 丢
    out = _norm_chatter("上下游轴的乙方服务商")  # "上下游轴" 含轴名 → 认
    assert out and out[0]["轴"] == "上下游"
    three = [{"轴": a, "group": g} for a, g in
             [("上下游", "A"), ("上下层", "B"), ("上下游", "C")]]
    assert len(_norm_chatter(three)) == 2  # ≤2 截断


# ── _derive_audience_map: 锁定器 v2 ──

class _StubLLM:
    def __init__(self, payload):
        self._p = payload
        self.calls: list[str] = []

    def chat(self, sys_p, user_p, **kw):
        self.calls.append(sys_p)
        return self._p


class _Book:
    book_title = "测试书"
    author = "某人"
    input_json = {"book_meta": {"blurb": "一本关于内容变现的书"}}


_FACING = {"kernel": {"one_liner": "内容战略驱动商业模式",
                      "why_read": "内容操盘者急需摆脱流量陷阱",
                      "positioning": "商业分析手册", "reader_shift": ["从流量到订阅"]}}

_GOOD = ('{"reader":"握渠道靠外部供给的操盘手","evidence":"全书讲渠道转自制","eligible":true,'
         '"chain":[{"group":"平台运营","bridge":"被自家算法KPI摆布"}],'
         '"org":[{"group":"拍板预算的业务负责人","bridge":"买流量vs养内容"}]}')


def test_audience_map_ok(monkeypatch):
    stub = _StubLLM(_GOOD)
    monkeypatch.setattr(cs, "_llm", lambda: stub)
    m = cs._derive_audience_map(_Book(), _FACING)
    assert m["L1"]["reader"] == "握渠道靠外部供给的操盘手"
    assert m["上下游"][0]["group"] == "平台运营"
    assert m["上下层"][0]["group"] == "拍板预算的业务负责人"
    # 反漂移铁律在提示词里 (书内cast平移禁令 + 观众世界指称)
    assert "书的cast" in stub.calls[0] and "观众世界" in stub.calls[0]


def test_audience_map_not_eligible_and_empty_pools(monkeypatch):
    """eligible=false → None; 某轴撑不起 → 空数组照收 (禁硬编)。"""
    monkeypatch.setattr(cs, "_llm", lambda: _StubLLM(
        '{"reader":"","evidence":"","eligible":false}'))
    assert cs._derive_audience_map(_Book(), _FACING) is None
    stub = _StubLLM('{"reader":"操盘手","evidence":"x","eligible":true,"chain":[],"org":[]}')
    monkeypatch.setattr(cs, "_llm", lambda: stub)
    m = cs._derive_audience_map(_Book(), _FACING)
    assert m["上下游"] == [] and m["上下层"] == []


def test_director_template_has_chatter_schema():
    """导演模板: 闲话层调度段 + JSON schema 列在位 (双面大纲)。"""
    t = (Path(__file__).resolve().parents[1] / "config" / "laotan_series_director.txt").read_text(encoding="utf-8")
    assert "闲话层调度" in t
    assert '"闲话层"' in t and '"拆解三步"' in t


def test_persona_layer_audience_demoted():
    """人设层受众段降权 (0914): 只剩听感底色, 人群名单/反推禁令锁死 —
    人设层不再把大礼包风格带回来 (用户令: 老谭共享受众地图而不带偏)。"""
    t = (Path(__file__).resolve().parents[1] / "config" / "laotan-book.txt").read_text(encoding="utf-8")
    assert "听感底色" in t
    assert "只认【本集主受众/闲话层】" in t          # 优先级铁律在位
    # 三层人群名单 (旧大礼包烙印) 不得回流人设层
    assert "集团部门管理者、内容创作者/账号操盘手、想升值的职场人" not in t


def _struct_eps(n: int):
    """n 集结构合法数据: ep1 大钩子 / 中间工具集 / 末集行动册。"""
    base = {'主题': 'x', '核心任务': '', '本集秘籍': '秘籍判定表一二三', '概念': ['a'],
            '承上': 'y', '启下': 'z',
            '拆解三步': ['①广告主档+浩劫后零广告钉', '②用户档+订阅续费钉', '③补贴档+断补成本线钉'],
            '闲话层': [{'轴': '上下层', 'group': '业务负责人'}]}
    eps = [dict(base, ep=i) for i in range(1, n + 1)]
    # ep1 大钩子集: 拆解三步豁免, 闲话层仍必填 (受众最宽的一集要搭桥)
    eps[0] = dict(eps[0], 核心任务='大钩子: 收益承诺+导览钩住人', 拆解三步=[])
    eps[-1] = dict(eps[-1], 核心任务='行动册装订: 全工具合订', 拆解三步=[], 闲话层=[])
    return eps


def test_series_structure_parameterized():
    """0914 结构参数化 (用户令: 集数由书定=K+2, 系统守不变量): 头尾豁免三步/闲话层,
    带宽 [5,9], 末集须行动册职能, 头须大钩子职能。"""
    from app.services.book_service.series_outline import _validate
    soul = {'core': 'c', 'who': 'w', 'gain': 'g'}
    # 8 集 (K=6) 与 6 集 (K=4) 均合法
    for n in (6, 8):
        iss = [i for i in _validate({'soul': soul, 'episodes': _struct_eps(n)})
               if '集数' in i or '拆解三步' in i or '闲话层' in i or '职能' in i]
        assert iss == [], (n, iss)
    # 带外拒绝
    for n in (4, 10):
        assert any('集数' in i for i in _validate({'soul': soul, 'episodes': _struct_eps(n)}))
    # 末集没行动册职能 → 打回
    bad = _struct_eps(8); bad[-1] = dict(bad[-1], 核心任务='讲最后一个新概念')
    assert any('行动册' in i for i in _validate({'soul': soul, 'episodes': bad}))
    # 中间集缺三步 → 打回 (头尾不豁免中间)
    bad2 = _struct_eps(8); bad2[3] = dict(bad2[3], 拆解三步=[])
    assert any('拆解三步' in i for i in _validate({'soul': soul, 'episodes': bad2}))
    # ep1 缺闲话层 → 打回 (大钩子集也要搭桥); 末集缺闲话层 → 合法
    bad3 = _struct_eps(8); bad3[0] = dict(bad3[0], 闲话层=[])
    assert any('闲话层' in i for i in _validate({'soul': soul, 'episodes': bad3}))


def test_ep1_big_hook_exempt():
    """ep1=大钩子集: 段间承接跳过路标检查 (八拍段首设问合法), 配额照查;
    flow_check 显式开关 (0914: 只对工具集做承接断层)。"""
    from app.services.book_service.creation_common import section_flow_issues
    t = ("【0-33秒｜钩子】\n打字卡：每晚一集。\n"
         "【33-67秒｜回顾+引入】\n增长停了。\n"
         "【67-140秒｜核心概念拆解（一）】\n什么问题，能把一本书劈成三半？\n"
         "【140-213秒｜核心概念拆解（二）】\n第二面墙，替代墙。\n"
         "【213-280秒｜核心概念拆解（三）】\n第三面墙。\n"
         "【280-360秒｜总结+下期预告】\n这是六步转型的第一步。这是六步转型的第一步。")
    issues = section_flow_issues(t, book_title='X书', flow_check=False)
    assert not any('承接断层' in i for i in issues)      # 路标豁免
    assert any('系列句' in i for i in issues)             # 配额不豁免
    # 工具集 (flow_check=True): 段首设问要打回
    issues2 = section_flow_issues(t, book_title='X书', flow_check=True)
    assert any('承接断层' in i for i in issues2)
    # 系列句步数泛化: 七步转型也计数
    t2 = t.replace('六步转型', '七步转型')
    assert any('七步转型' in i for i in section_flow_issues(t2, book_title='X书', flow_check=False))


def test_typing_card_doctrine():
    """打字卡定稿教义: 书级固定卡 (全系列同一句) + 骨架冻结两槽随书 +
    内部词汇/主角反差禁令 (零上下文位)。"""
    t = (Path(__file__).resolve().parents[1] / "config" / "laotan-book.txt").read_text(encoding="utf-8")
    assert "打字机定场卡" in t
    assert "书级固定卡" in t and "照抄一字不改" in t          # 固定卡: 系统注入, 稿层照抄
    assert "骨架冻结" in t and "对比人群" in t                 # 两槽随书
    assert "禁系列内部" in t                                   # 零上下文位, 内部词不进卡
    gen = (Path(__file__).resolve().parents[1] / "app" / "services" / "book_service" / "episode_gen.py").read_text(encoding="utf-8")
    assert "书级固定卡" in gen and "系列打字卡" in gen         # 生成层注入+回填在位


def test_series_card_frozen_formula():
    """书级固定打字卡 (用户令写活两槽): 骨架冻结两槽随书 — 收益主体大白话 +
    对比人群; 生成不合格骨架拒绝; 模板/生成层接入点在位。"""
    from app.services.book_service import creation_common as cc
    from app.services.book_service import series_outline as so

    class _Stub:
        def __init__(self, payload): self.p = payload
        def chat(self, *a, **k): return self.p

    # 合格卡 → 收
    good = '{"card": "如果你能坚持每晚睡前看一集，一个月后，你看内容生意的眼光，会超过90%的同行。"}'
    monkey_target = cc
    import pytest
    monkey = pytest.MonkeyPatch()
    monkey.setattr(monkey_target, "_llm", lambda: _Stub(good))
    card = so._derive_series_card("X书", {"core": "c", "gain": "g"})
    assert card.startswith("如果你能坚持每晚睡前看一集")
    assert "超过90%" in card
    # 骨架缺件 → 拒 (走 ep1 自产兜底)
    monkey.setattr(monkey_target, "_llm", lambda: _Stub('{"card": "随便一句没有骨架的话"}'))
    assert so._derive_series_card("X书", {"core": "c", "gain": "g"}) == ""
    monkey.undo()
    t = (Path(__file__).resolve().parents[1] / "config" / "laotan-book.txt").read_text(encoding="utf-8")
    assert "书级固定卡" in t and "照抄一字不改" in t
