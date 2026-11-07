# -*- coding: utf-8 -*-
"""1105 v3 V2 立意: 语态切弧/骨架/弧界执法/路由接线 (零 LLM)."""
import sys

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

from app.services.anim_pipeline.v3_pipeline import arcs


def _lines():
    return [
        {"no": 1, "text": "很多顾问都踩过这个大坑：", "mode": "hook"},
        {"no": 2, "text": "【0-60 秒｜开篇】", "mode": "section"},
        {"no": 3, "text": "书里说：信任是一点一滴存出来的账户。", "mode": "quote"},
        {"no": 4, "text": "这话我信。", "mode": "comm"},
        {"no": 5, "text": "但你账户塌的时候，第一个念头是补。", "mode": "comm"},
        {"no": 6, "text": "", "mode": "comm"},                      # 空行剔除
        {"no": 7, "text": "2020年那单，我丢了。", "mode": "persona"},
        {"no": 8, "text": "关注我，下集继续。", "mode": "cta"},
        {"no": 9, "text": "【卡:止损两问】两轮不开吗？", "mode": "card"},
    ]


def test_modules_group_consecutive_mode():
    mods = arcs.modules_of(_lines())
    assert [(m["mode"], m["no_start"], m["no_end"]) for m in mods] == [
        ("hook", 1, 1), ("quote", 3, 3), ("comm", 4, 5),
        ("persona", 7, 7), ("cta", 8, 8)]
    # 段标/空行/卡指令都不成模块
    assert all(m["mode"] not in ("section", "card", "img", "unknown") for m in mods)


def test_skeleton_one_arc_per_module():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    assert [a["arc_id"] for a in sk] == ["A1", "A2", "A3", "A4", "A5"]
    assert [a["type"] for a in sk] == ["hook", "quote", "narr", "persona", "cta"]
    assert sk[2]["no_start"] == 4 and sk[2]["no_end"] == 5   # 两行评论并一弧
    assert sk[2]["line_count"] == 2
    assert all(a["est_s"] > 0 for a in sk)


def test_boundary_clean_passes():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    assert arcs.boundary_issues(sk, _lines()) == []


def test_boundary_cross_mode_flagged():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    bad = [dict(sk[0]), dict(sk[1], no_end=5), *sk[3:]]   # 书证弧吞掉评论行
    issues = arcs.boundary_issues(bad, _lines())
    assert any("跨语态" in i for i in issues), issues


def test_boundary_missing_and_duplicate_flagged():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    dropped = sk[:-1]                                      # 丢掉最后一段 CTA
    assert any("未归属任何弧" in i for i in arcs.boundary_issues(dropped, _lines()))
    dup = sk + [dict(sk[2], arc_id="A9")]                  # 同一区间归两弧
    assert any("重复归属" in i for i in arcs.boundary_issues(dup, _lines()))


def test_non_narrative_types():
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    non = {a["type"] for a in sk if a["mode"] in ("hook", "cta", "brand")}
    assert non == {"hook", "cta"}
    assert "brand" in arcs.NON_NARRATIVE


def test_wiring_v2():
    import inspect
    from pathlib import Path
    from app.routers import anim_v3
    src = inspect.getsource(anim_v3)
    assert "/concept\"" in src and "/concept/save" in src
    assert "boundary_issues" in src                  # 保存时复检执法
    # 端点内延迟导入必须真能解析 (V1 实锤: 相对导入错 → 保存恒 500)
    import importlib
    for fn in (anim_v3.v3_concept_get, anim_v3.v3_concept_save):
        for line in inspect.getsource(fn).splitlines():
            line = line.strip()
            if line.startswith("from ") and " import " in line:
                exec(line, {})
    for f in ("web/v3/concept.html", "web/v3/concept.js", "web/v3/concept.css"):
        assert Path(f).exists(), f


def test_missing_worlds_only_creative_arcs():
    """钩子/行动召/品牌位本就无世界; 叙事/书证/人设缺 world = 系统缺陷, 要自查。"""
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    miss = {a["arc_id"] for a in arcs.missing_worlds(sk)}
    # _lines() 里: 钩子(A1)/书证(A2)/评论(A3)/人设(A4)/行动召(A5)
    by_type = {a["arc_id"]: a["type"] for a in sk}
    assert miss == {aid for aid, t in by_type.items() if t in ("narr", "quote", "persona")}
    assert not any(by_type[aid] in ("hook", "cta", "brand") for aid in miss)
    for a in sk:
        if a["type"] in ("narr", "quote", "persona"):
            a["world"] = "工厂流水线，工人逐项勾选清单"
    assert arcs.missing_worlds(sk) == []


def test_fill_missing_worlds_applies_llm_result(monkeypatch):
    """补全是定点调用: 只补缺的弧, 已填的不动 (1105 用户令: 系统自己补, 不甩给人)。"""
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))
    missing = {a["arc_id"] for a in arcs.missing_worlds(sk)}
    assert missing

    class _LLM:
        def chat(self, *a, **kw):
            assert kw.get("tag") == "v3_arcs_world"
            body = a[1]
            ids = [ln.split("|")[0] for ln in body.splitlines() if ln.startswith("A")]
            return ('{"arcs":[%s]}'
                    % ",".join('{"arc_id":"%s","world":"无护栏的悬崖盘山公路"}' % i
                               for i in ids))

    class _Svc:
        @staticmethod
        def get_llm_service():
            return _LLM()

        @staticmethod
        def _parse_json(raw):
            import json
            return json.loads(raw)

    monkeypatch.setitem(sys.modules, "app.services.llm_service", _Svc)
    n = arcs.fill_missing_worlds(sk, _lines(), "测试书", 1)
    assert n == len(missing)
    assert arcs.missing_worlds(sk) == []
    assert all(a["world"] == "无护栏的悬崖盘山公路"
               for a in sk if a["arc_id"] in missing)


# ── 批1 选角 (1107): cast pass 枚举执法 + 人设弧保险 ──────────────────


def test_llm_fill_cast_enum_and_dedup(monkeypatch):
    """cast 只收名单内卡名: 越界剔除+记 hint, 重复去重 (批1 选角执法)。"""
    monkeypatch.setattr(arcs, "_card_catalog",
                        lambda bt: {"老谭": "深红冲锋衣", "阿特金森": "深炭西装"})
    sk = arcs.build_skeleton(arcs.modules_of(_lines()))

    class _LLM:
        def chat(self, *a, **kw):
            assert kw.get("tag") == "v3_arcs"
            assert "可用角色卡" in a[1]           # 卡名单必须进 prompt
            return ('{"arcs":['
                    '{"arc_id":"A3","narrative":"n","world":"w","metaphor":"m",'
                    '"cast":["阿特金森","阿特金森","比尔盖茨"]},'
                    '{"arc_id":"A4","narrative":"n2","world":"","metaphor":"m2",'
                    '"cast":["老谭"]}]}')

    class _Svc:
        @staticmethod
        def get_llm_service():
            return _LLM()

        @staticmethod
        def _parse_json(raw):
            import json
            return json.loads(raw)

    monkeypatch.setitem(sys.modules, "app.services.llm_service", _Svc)
    out, hints = arcs._llm_fill(sk, _lines(), "测试书", 1)
    assert out["A3"]["cast"] == ["阿特金森"]
    assert out["A4"]["cast"] == ["老谭"]
    assert any("越界" in h and "比尔盖茨" in h for h in hints), hints


def test_card_catalog_reads_bible(monkeypatch, tmp_path):
    """圣经 characters[].name+look → 卡目录 (look 截 40); 无圣经静默空。"""
    import json as _json

    from app.services.anim_pipeline import director2
    bp = tmp_path / "bible.json"
    bp.write_text(_json.dumps(
        {"characters": [{"name": "阿特金森", "look": "深炭色正装配暗红领带" * 5},
                         {"name": "", "look": "无名卡不进目录"}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(director2, "_bible_cache_path", lambda bt: bp)
    m = arcs._card_catalog("任意书")
    assert set(m) == {"阿特金森"}
    assert len(m["阿特金森"]) == 40

    def _missing(bt):
        raise FileNotFoundError(bt)

    monkeypatch.setattr(director2, "_bible_cache_path", _missing)
    assert arcs._card_catalog("无圣经书") == {}


def test_ensure_persona_host():
    """人设弧缺老谭 → 补; 卡目录无老谭 → 零操作 (不造卡名)。"""
    cards = {"老谭": "深红冲锋衣"}
    arcs_ = [{"arc_id": "A1", "type": "hook", "cast": []},
             {"arc_id": "A2", "type": "persona", "cast": []},
             {"arc_id": "A3", "type": "persona", "cast": ["老谭"]},
             {"arc_id": "A4", "type": "narr", "cast": []}]
    n = arcs.ensure_persona_host(arcs_, cards)
    assert n == 1
    assert arcs_[1]["cast"] == ["老谭"]
    assert arcs_[2]["cast"] == ["老谭"]        # 已有不动
    assert arcs_[0]["cast"] == [] and arcs_[3]["cast"] == []
    assert arcs.ensure_persona_host(arcs_, {"别人": "x"}) == 0   # 无老谭卡目录不造名
