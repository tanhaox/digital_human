# -*- coding: utf-8 -*-
"""唯一宪法 — 拆书动画产线的单一事实源 (1013 P0 整合, 用户令).

背景: 道法术器/K2H3/MTV影视剧等理念各自落地成独立规则, 互相掣肘致产出持续
降级 (docs: 计划 streamed-soaring-crescent)。本模块把全部配额/尺度/域界/策略
收进 config/anim_constitution.json 一处 — prompt 注入 (prompt_block) 与校验
(final_gate / validate_doc / hook_zone / k2) 都从这里读。

设计约束:
- 零 LLM: 分区判定 (zone_of) / 配额数学 / 尺度表全部确定性。
- 迁移纪律: 某条规则迁入宪法后, 原散落处的散文/常量改为消费宪法 (阶段1=数值
  等价搬迁), 新写者 (scene_writer) 与新闸 (final_gate) 只引宪法, 不私设。
- mtime 守卫: 改 JSON 无需重启 (对齐 k2._bible_hue 的坑教训)。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}  # path → (mtime, doc)


def _const_path() -> Path:
    """config/anim_constitution.json — 相对本文件定位 (不依赖 cwd)."""
    return Path(__file__).resolve().parents[3] / "config" / "anim_constitution.json"


def load() -> dict[str, Any]:
    """读宪法 (进程缓存 + mtime 守卫 — 改 JSON 立即生效, 无需重启)."""
    p = _const_path()
    try:
        mt = p.stat().st_mtime
        hit = _CACHE.get(str(p))
        if hit and hit[0] == mt:
            return hit[1]
        doc = json.loads(p.read_text(encoding="utf-8"))
        _CACHE[str(p)] = (mt, doc)
        return doc
    except Exception as exc:  # noqa: BLE001 — 宪法缺失/损坏 = 退内置默认, 产线不炸
        logger.error("[constitution] 宪法加载失败, 退默认值: %s (%s)", p, exc)
        return _DEFAULTS()


def reload() -> dict[str, Any]:
    _CACHE.clear()
    return load()


def rule(name: str, zone: str | None = None) -> Any:
    """取一条规则; 带 zone 时若该键下有分区标量/列表则直接返回, 否则返回整条."""
    r = (load().get("rules") or {}).get(name) or {}
    if zone is None:
        return r
    v = r.get(zone)
    return v if isinstance(v, (int, float, str, list)) else r


def zone_value(name: str, subkey: str, zone: str) -> float | None:
    """规则 name 下子表 subkey 的分区值 (如 person_quota.scene_min_present.hook)."""
    r = (load().get("rules") or {}).get(name) or {}
    tbl = r.get(subkey) or {}
    return tbl.get(zone) if isinstance(tbl, dict) else None


# ── 分区判定 (三级, 零 LLM) ──────────────────────────────────────────

def zone_of(shot: dict[str, Any], doc: dict[str, Any], opening_sec: float = 0.0) -> str:
    """镜 → hook/case/cite/mid/brand.

    优先级: brand 寄存器 → 音频轴 <60s → case/cite 寄存器 → mid。
    opening_sec = 品牌卡开场预留 (音频轴 0 点 = 正文起点, 与 hook_zone 同源)。"""
    reg = str(shot.get("register") or "").strip()
    if reg == "host_brand" or shot.get("brand_card"):
        return "brand"
    t0 = float(shot.get("t_start") or 0) - float(opening_sec)
    if t0 < zone_end():
        return "hook"
    if reg == "case_narrative":
        return "case"
    if reg == "author_cite":
        return "cite"
    return "mid"


def zone_end() -> float:
    return float(((load().get("zones") or {}).get("hook") or {}).get("window", [0, 60])[1])


# ── 尺度表 (冲突11: 唯一时长尺) ──────────────────────────────────────

def duration_table() -> dict[str, float]:
    d = dict((load().get("rules") or {}).get("duration") or {})
    return {
        "slot_lo": float(d.get("slot_target", [3.0, 6.0])[0]),
        "slot_hi": float(d.get("slot_target", [3.0, 6.0])[1]),
        "elastic_lo": float(d.get("slot_elastic", [2.8, 6.3])[0]),
        "elastic_hi": float(d.get("slot_elastic", [2.8, 6.3])[1]),
        "split_sent": float(d.get("split_sent", 9.0)),
        "shot_floor": float(d.get("shot_floor", 1.5)),
        "shot_min_span": float(d.get("shot_min_span", 4.0)),
        "h3_max_span": float(d.get("h3_max_span", 10.0)),
    }


# ── 检测词表 (人物在场/度量尾/景深词 — validate 与注入共用一把尺) ────

def person_words() -> list[str]:
    return list(((load().get("rules") or {}).get("person_quota") or {})
                .get("person_words") or _DEFAULT_PERSON_W)


def measure_tails() -> tuple[str, ...]:
    return tuple(((load().get("rules") or {}).get("person_quota") or {})
                 .get("measure_tails") or _DEFAULT_MEAS_TAILS)


def depth_big_words() -> list[str]:
    return list(((load().get("rules") or {}).get("depth_quota") or {})
                .get("big_words") or _DEFAULT_BIG_W)


# ── 散文宪法唯一出口 (prompt_block) ─────────────────────────────────

def prompt_block(zone: str) -> str:
    """分区宪法 → 注入 prompt 的文案。所有散文铁律从这里出, 模块不私设。

    phase1 等价: hook 区返回与 hook_zone.ZONE_RULES 逐字等价的条款 (数值由
    duration_table 插值, 改宪法即改条款)。"""
    dt = duration_table()
    if zone == "hook":
        return (
            "【🔥 60s 特区条款 — 本场开头与正文 0-60s 相交的部分适用, 覆盖宪法中冲突条款】\n"
            "特区 = 正文前 60 秒流量池 (平台推荐机制锁定), 目标 = 每一镜都把观众按在屏幕前:\n"
            f"1. 镜界 = 槽界: 一槽一镜, 槽数/时长已由系统定死 ({dt['slot_lo']:.0f}-{dt['slot_hi']:.0f}s/镜), "
            "你只填画面, 禁增删并镜、禁改镜界。\n"
            "2. 微空间变奏 (1008 用户令·电影感, 硬校验): 组内全部镜同属**一个大空间** (location, 2-6字,\n"
            "   如 开放式办公室/深夜书房/印刷车间 — 从世界规则的世界清单选); 每镜在大空间内换**微位**:\n"
            "   \"scene_tag\" = 2-6字微位名 (工位/窗边/白板前/会议室玻璃后/茶水间/档案柜区), 组内微位禁重复;\n"
            "   相邻镜景别+角度必须都变 (只换微位不换景别=废稿); 大空间可跨组延续 (电影世界感)。\n"
            "3. 母题轮换 (a1 实弹教训, 保留): 每镜用**不同的隐喻载体** (立意 visual_motifs 挨个轮换, 一镜一母题);\n"
            "   同一物件组合 (如 桌面+稿纸+红笔) 不得出现在两镜以上; 批评轮带构图相似度审计。\n"
            "4. 微位先行: 动笔前先定组的大空间 → 再给全部槽各配一个互不相同的微位 scene_tag → 再逐镜写画面;\n"
            "   旧版画面仅作反面教材, 旧版的场景串一律不得沿用。\n"
            "5. 首帧复杂度 (场景满/主体聚焦, 1013 裁决两律各管各的对象): **场景**必须满 — 前中后三层\n"
            "   景深 + ≥3 组叙事元素 + ≥2 个可动点 (H3 要有事干); **人物**是唯一视觉焦点 —\n"
            "   一镜一主角, 禁大场面把主角淹没。简单首帧 = 简单动画 = 观众划走。\n"
            "6. 每镜首拍 1s 内必须有可见动作/光效 (宪法原有, 特区内加倍执行)。\n"
            "7. 视线链 (1008 令·电影感): 组内相邻镜要有承接 — 上镜人物看向的方向, 下镜顺着接\n"
            "   (他低头→下镜桌面上是什么; 他抬头→下镜看见什么); 动作连续优先于并列换画。"
        )
    # 中段/案例/引用区: 人物与景深配额行 (writer 注入用)
    pq = (load().get("rules") or {}).get("person_quota") or {}
    sp = (pq.get("scene_min_present") or {})
    lo = sp.get(zone, sp.get("mid", 0.25))
    if zone in ("case", "cite"):
        return (f"【分区宪法·{zone}】本场为{'案例' if zone == 'case' else '引用'}镜: 画面主体=人物 "
                "(携带角色锁定卡 look, 露脸有表情, 与叙事物件互动); 禁纯物件空镜。")
    return (f"【分区宪法·mid·MTV手法正名】电影情节为主 — 人物在概念世界中行动 "
            f"(讲述人如MTV歌手克制出镜, 绝不刷屏不当万金油); 人物在场下限 "
            f"{lo:.0%}, 概念物件是舞台、人物在台上做事; 相邻场禁同一概念世界, "
            "每镜换隐喻载体与人物姿态, 禁克隆构图。")


def text_law(key: str, default: str = "") -> str:
    return str((((load().get("rules") or {}).get("text_law") or {}).get(key)) or default)


def skin_fallback() -> str:
    return str((((load().get("rules") or {}).get("skin_law") or {}).get("k2_fallback")) or "")


def writer_enabled() -> bool:
    return bool(load().get("writer_enabled"))


# ── 内置默认 (宪法文件缺失时的保命底, 与 JSON 默认值一致) ────────────

_DEFAULT_PERSON_W = ["人物", "角色", "男人", "女人", "男性", "女性", "小伙", "姑娘",
                     "老者", "青年", "中年", "身影", "侧脸", "人脸", "面部", "表情",
                     "微笑", "皱眉", "眉头", "目光", "眼神", "胡须", "白发", "男子", "女子",
                     "小人", "匠人", "工人", "学者", "读者", "旁观者", "来访者"]
_DEFAULT_MEAS_TAILS = ("身高", "手掌", "头部", "头顶", "胸口", "腰部", "肩部", "臂长", "比例")
_DEFAULT_BIG_W = ["辽阔", "地平线", "全景", "远山", "天空占", "广阔", "一望无际",
                  "延伸到画面", "远方", "鸟瞰", "视野尽头", "大片"]


def _DEFAULTS() -> dict[str, Any]:
    """宪法文件不可读时的等价默认 (phase1 数值即现行值)."""
    return {
        "version": 0, "writer_enabled": False,
        "zones": {"hook": {"basis": "time", "window": [0, 60]}},
        "rules": {
            "person_quota": {"person_words": _DEFAULT_PERSON_W,
                             "measure_tails": list(_DEFAULT_MEAS_TAILS)},
            "depth_quota": {"big_words": _DEFAULT_BIG_W},
        },
    }
