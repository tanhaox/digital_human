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
         patch("app.services.anim_pipeline.k2_lang.translate_batch",
               return_value={"pending": 1, "ok": 1, "fail": 0}) as m:
        k2.pretranslate(doc)
    sent = m.call_args[0][0]
    assert len(sent) == 1 and sent[0][0]["shot_id"] == "s04"
