# -*- coding: utf-8 -*-
"""neg_learn — 判决→书级负词沉淀回路 (1017 W5, 用户令: 多音字同构).

分层 (共用 vs 个性):
  系统级 (代码常量, 全书共用): _BASE/_FACE/_CROWD/_SHEET/_PORTRAIT_NEG +
    _PLATFORM_NEG (合规 json) — 人类图形的普适律, 改动走 git 发版。
  书级 (圣经): negative_keywords (用户手填主权区) +
    negative_keywords_auto (本回路系统累积区) — 注入时合并, 手填区永不自动写。

回路: 图像闸 (k2 gate_rejects) 与视频闸 (video_qc suspect) 判决 →
neg_learn.record_verdict(book, kind) → 台账计数 (outputs/动画/_资产/负词台账_书.json) →
同 kind 复发 ≥2 → 判决族 token 追加圣经 negative_keywords_auto (去重+截12族,
mtime 守卫免重启生效)。日志 [neg-learn] 全程留痕。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 判决 kind 关键词 → 书级负词族 (族=逗号短语, 一次整族进出)
# 注意: 背身/无脸朝向族已在系统级 _FACE_NEG (1017), 不重复入书;
# 此表只收**书风格特性**会复发、且全局不押的族。
_KIND_FAMILIES: list[tuple[tuple[str, ...], str]] = [
    (("乱码", "伪字", "pseudo"), "pseudo-glyphs, unreadable invented characters, smeared text-like marks on surfaces"),
    (("崩坏", "黑屏", "马赛克", "撕裂"), "corrupted frames, black screens, noise mosaics, torn smeared regions"),
    (("红旗", "旗帜", "条幅"), "flags, banners, red streamers, pennants on rooftops or buildings"),
    (("衣色", "recolor", "换色"), "recolored clothing, hue-shifted outfit, costume color change"),
    (("多人", "路人", "群像"), "extra bystanders, crowd of background figures multiplying"),
    (("风格漂移", "画风", "写实"), "photorealistic drift, live-action texture, realistic skin pores"),
]

_THRESHOLD = 2          # 同书同族复发阈值
_MAX_FAMILIES = 12      # auto 区截断


def _assets_dir() -> Path:
    from .shots import anim_output_root
    d = anim_output_root() / "_资产"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger_path(book_title: str) -> Path:
    return _assets_dir() / f"负词台账_{book_title}.json"


def _bible_path(book_title: str) -> Path:
    from .director2 import _bible_cache_path
    return _bible_cache_path(book_title)


def _match_family(kind_text: str) -> str | None:
    t = str(kind_text or "")
    for keys, family in _KIND_FAMILIES:
        if any(k in t for k in keys):
            return family
    return None


def record_verdict(book_title: str, kind_text: str) -> None:
    """判决记账; 过阈值整族沉淀入圣经 auto 区。fail-open 永不炸产线。"""
    if not book_title:
        return
    try:
        family = _match_family(kind_text)
        if not family:
            return
        lp = _ledger_path(book_title)
        ledger: dict = {}
        if lp.exists():
            ledger = json.loads(lp.read_text(encoding="utf-8"))
        entry = ledger.setdefault(family, {"count": 0, "ts": []})
        entry["count"] += 1
        entry["ts"] = (entry["ts"] + [time.strftime("%Y-%m-%dT%H:%M:%S")])[-8:]
        lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        if entry["count"] < _THRESHOLD or entry.get("banked"):
            return
        # 过阈值 → 沉淀入圣经 auto 区 (一次性, banked 标记防重复写)
        bp = _bible_path(book_title)
        bible = json.loads(bp.read_text(encoding="utf-8"))
        auto = [x.strip() for x in
                str(bible.get("negative_keywords_auto") or "").replace("，", ",").split(",")
                if x.strip()]
        if family in auto:            # 已在 auto (手挪过) → 标 banked 即可
            entry["banked"] = True
            lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        manual = [x.strip() for x in
                  str(bible.get("negative_keywords") or "").replace("，", ",").split(",")
                  if x.strip()]
        if any(family in m or m in family for m in manual):
            entry["banked"] = True    # 手填区已覆盖同族 → 不重复入 auto
            lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        if len(auto) >= _MAX_FAMILIES:
            return
        auto.append(family)
        bible["negative_keywords_auto"] = ", ".join(auto)
        bp.write_text(json.dumps(bible, ensure_ascii=False, indent=2), encoding="utf-8")
        entry["banked"] = True
        lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("[neg-learn] 《%s》判决族×%d → 圣经 auto 区沉淀: %s",
                    book_title, entry["count"], family[:50])
    except Exception as exc:  # noqa: BLE001 — 沉淀失败不炸产线
        logger.warning("[neg-learn] 记账失败 (忽略): %s", exc)
