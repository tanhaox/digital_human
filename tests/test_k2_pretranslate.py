# -*- coding: utf-8 -*-
"""Y1 批前预翻译 (1103): 批量压上 GPU 零等待 — 分组批调/坏组单发补/缓存跳过/同源 sha."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from app.services.anim_pipeline import k2_lang

_CFG = patch("app.services.anim_pipeline.config.load", return_value=NS(llm=NS()))


def _shot(sid, cached=None):
    s = {"shot_id": sid, "status": "planned", "in_image_text": None}
    if cached:
        s["image_prompt_en"] = cached
    return s


def _batch_ret(*ens):
    return {"t": [{"id": f"s{i:02d}", "en": e} for i, e in enumerate(ens)]}


def test_batch_groups_and_caches():
    items = [(_shot(f"s{i:02d}"), f"中文提示词第{i}镜" * 8) for i in range(7)]
    with _CFG, patch("app.services.anim_pipeline.llm.chat_json",
                     return_value=_batch_ret(*["english prompt " * 5] * 7)) as m:
        r = k2_lang.translate_batch(items, group=5)
    assert m.call_count == 2  # 7 镜 / 组5 = 2 组
    assert r["ok"] == 7 and r["fail"] == 0
    assert all(s["image_prompt_en"]["en"] for s, _ in items)


def test_batch_group_fail_falls_to_single_retry():
    """组失败 (deepseek 503 类) → 坏的走单发补一次 (有需要的才走一次)."""
    items = [(_shot("sA"), "中文甲" * 12), (_shot("sB"), "中文乙" * 12)]
    with _CFG, patch("app.services.anim_pipeline.llm.chat_json",
                     side_effect=RuntimeError("HTTP 503 busy")), \
         patch.object(k2_lang, "_chat", side_effect=["english A " * 6, "english B " * 6]) as m1:
        r = k2_lang.translate_batch(items, group=5)
    assert m1.call_count == 2 and r["ok"] == 2 and r["fail"] == 0


def test_batch_short_en_retried():
    items = [(_shot("sA"), "中文甲" * 12)]
    with _CFG, patch("app.services.anim_pipeline.llm.chat_json",
                     return_value={"t": [{"id": "sA", "en": "short"}]}), \
         patch.object(k2_lang, "_chat", return_value="english full prompt A " * 6):
        r = k2_lang.translate_batch(items)
    assert r["ok"] == 1 and r["fail"] == 0


def test_pretranslate_skips_cached_and_filtered():
    from app.services.anim_pipeline import k2
    zh = "同一中文源串" * 10
    sha = k2_lang._sha(zh)
    doc = {"book_title": "X", "ep": 2, "shots": [
        _shot("s01", cached={"sha": sha, "en": "cached-en"}),   # 已缓存跳过
        {"shot_id": "s02", "status": "img_done"},               # 非 planned 跳过
        {"shot_id": "s03", "political_flag": True},             # 禁区跳过
        _shot("s04"),
    ]}
    with patch.object(k2, "_scene_eff_k2", lambda s, b: zh if s["shot_id"] in ("s01", "s04") else "x"), \
         patch.object(k2, "_warm_book_en", return_value=0), \
         patch("app.services.anim_pipeline.k2_lang.translate_batch",
               return_value={"pending": 1, "ok": 1, "fail": 0}) as m:
        k2.pretranslate(doc)
    sent = m.call_args[0][0]
    assert len(sent) == 1 and sent[0][0]["shot_id"] == "s04"


def test_native_en_zero_llm():
    """Y2: keyframe_en 在 → 英文直用零 LLM (英→英 round-trip 废除, s09 实锤)."""
    s = _shot("sN"); s["keyframe_en"] = "Planner-written English prompt " * 5
    with patch.object(k2_lang, "_chat") as m:
        got = k2_lang.ensure_en_prompt(s, "Planner-written English prompt " * 5 + ", tint")
    assert m.call_count == 0
    assert got.startswith("Planner-written English")
    assert s["image_prompt_en"]["native"] is True


def test_native_en_pretranslate_skipped():
    """Y2: 预翻译收集跳过原生英文镜 (64/64 有 keyframe_en → 预翻译空转)."""
    from app.services.anim_pipeline import k2
    doc = {"book_title": "X", "ep": 2, "shots": [_shot("s01")]}
    doc["shots"][0]["keyframe_en"] = "English native " * 6
    with patch.object(k2, "_scene_eff_k2") as m_asm, \
         patch.object(k2, "_warm_book_en", return_value=0), \
         patch("app.services.anim_pipeline.k2_lang.translate_batch") as m_b:
        r = k2.pretranslate(doc)
    assert m_asm.call_count == 0 and m_b.call_count == 0
    assert r["pending"] == 0


# ── 1108 书级串预热 (Y1「GPU 零等待」收口) ──────────────────────────────


def test_warm_book_en_covers_all_strings(monkeypatch):
    """预热面 = 角色卡 look×N + 配方四串 + 弧主旨 (按 arc_ids) — s1 惰性首译停 26s 根修."""
    from app.services.anim_pipeline import k2
    from app.services.anim_pipeline import h3
    calls = {"look": [], "str": [], "thesis": [], "standin": []}

    monkeypatch.setattr(k2, "_char_looks",
                        lambda bt: [("老谭", "深红冲锋衣"), ("阿特金森", "深炭西装")])

    def _look(nm, lk):
        calls["look"].append(nm)
        return (nm, lk)
    def _mk_str(tag):
        return lambda bt: calls["str"].append(tag) or ""
    monkeypatch.setattr(k2_lang, "char_look_en", _look)
    monkeypatch.setattr(k2_lang, "style_head_en", _mk_str("head"))
    monkeypatch.setattr(k2_lang, "style_head_empty_en", _mk_str("head_empty"))
    monkeypatch.setattr(k2_lang, "style_tail_en", _mk_str("tail"))
    monkeypatch.setattr(k2_lang, "character_policy_en", _mk_str("policy"))
    monkeypatch.setattr(k2_lang, "standin_look_en",
                        lambda sl: calls["standin"].append(sl) or "")

    def _thesis(bt, th):
        calls["thesis"].append(th)
        return ""
    monkeypatch.setattr(k2_lang, "thesis_en", _thesis)
    # 弧主旨源: h3.arc_thesis (doc.arcs.metaphor_core)
    monkeypatch.setattr(h3, "arc_thesis",
                        lambda doc, aid: {"A1": "信任账户倾覆", "A2": ""}.get(aid, ""))
    # 无圣经 → 替身分支静默跳过 (except pass)
    def _no_bible(bt):
        raise FileNotFoundError(bt)
    monkeypatch.setattr("app.services.anim_pipeline.director2._bible_cache_path", _no_bible)

    doc = {"book_title": "X", "shots": [{"arc_id": "A1"}, {"arc_id": "A2"}]}
    n = k2._warm_book_en(doc)
    assert calls["look"] == ["老谭", "阿特金森"]
    assert calls["str"] == ["head", "head_empty", "tail", "policy"]
    assert calls["thesis"] == ["信任账户倾覆"]        # 空主旨弧不调
    assert calls["standin"] == []
    assert n == 7


def test_pretranslate_warms_even_when_no_pending(monkeypatch):
    """keyframe_en 全原生 (pending=0) 时书级串预热仍要跑 — ep6 实锤病灶就在这里."""
    from app.services.anim_pipeline import k2
    doc = {"book_title": "X", "ep": 2, "shots": [_shot("s01")]}
    doc["shots"][0]["keyframe_en"] = "English native " * 6
    warmed = []
    monkeypatch.setattr(k2, "_warm_book_en",
                        lambda d, arc_ids=None: warmed.append(arc_ids) or 9)
    r = k2.pretranslate(doc)
    assert len(warmed) == 1
    assert r["pending"] == 0
