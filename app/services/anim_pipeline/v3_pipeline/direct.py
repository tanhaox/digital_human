# -*- coding: utf-8 -*-
"""v3 ④ 导演 (V4) — LLM 吐四元组枚举, 程序管执行参数.

分工 (1105 用户令「教会 LLM 规范用语, 程序判定步数」):
- LLM: 从封闭枚举选 cam{kind,dir,amp,speed} + 写 主体运动/光影; 规则前移进提示。
- 程序: 枚举校验 / 槽长闸 / 工位路由 / 步数表 / 英文句渲染 / 景别配额。

单一事实源 CAM_DEFS — 三个消费端同表: ④ 页预览 (API 下发) / H3 壳 /
校验器。LLM 永不写英文句 (模板活, 零漂移); 步数永不出 LLM (执行策略)。
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── CAMERA_LAW 运镜律 (1107 立律 — 十二~十四轮全族实测法典化, 单一事实源) ──
# 证据 (docs/guides/h3_camera_language_guide.md 第十二~十四轮):
#   推 8/8 唯一 100% 可控 (幅速面宽); 摇 = 特写/近景域 2/2 + 方向控制 9/9 (1024
#   w1 句); 升降 0/3 / 弧 0/3 / 拉 0/2 / 摇@大场景 0/2 / 移 0/1 / 跟 0/2 → 出局。
#   附验: s8/s38 图级渗漏与运镜族无关 — 律管不了图, 图的病走 K2 重生图。
# 新证据 → 改律一处, 四消费端 (④SYS/④校验/clause 装配/⑧注入) 自动跟随。
# 字段: dirs 方向枚举 / static 固定镜 / en 官方动词 / v2 机位词投影 (608 预裁 +
#   三补丁链入口) / family 执行族 / amp_speed 幅速配方 ("free"=LLM 自由,
#   dict=强制稳定档) / domain 适用景别域 (空=全域) / close 构图收口 (四要素之四,
#   1024 w1 实测句尾; 方向型逐向给)。
CAMERA_LAW: dict[str, dict[str, Any]] = {
    "固定": {"dirs": [], "static": True, "en": "holds a static shot",
             "v2": "", "family": "", "amp_speed": None, "domain": [], "close": ""},
    "推":   {"dirs": [], "en": "pushes in", "v2": "拉近", "family": "push",
             "amp_speed": "free", "domain": [],
             "close": ("toward the center of the action; the whole scene keeps its "
                       "exact shape; no lateral panning, no orbit, no roll")},
    "摇":   {"dirs": ["左", "右", "上", "下"],
             "en": {"左": "pans left", "右": "pans right",
                    "上": "tilts up", "下": "tilts down"},
             "v2": {"左": "左摇", "右": "右摇", "上": "上摇", "下": "下摇"},
             "family": "pan",
             "amp_speed": {"amp": "大", "speed": "慢"}, "domain": ["特写", "近景"],
             "close": {
                 "左": ("the framing travels past the original left edge of "
                        "Picture 1 while the subjects stay where they are in the "
                        "world; the horizon stays level and straight lines stay "
                        "straight"),
                 "右": ("the framing travels past the original right edge of "
                        "Picture 1 while the subjects stay where they are in the "
                        "world; the horizon stays level and straight lines stay "
                        "straight"),
                 "上": ("the view lifts past the original top edge of Picture 1 "
                        "while the subjects stay where they are in the world; "
                        "vertical lines stay straight"),
                 "下": ("the view drops past the original bottom edge of Picture 1 "
                        "while the subjects stay where they are in the world; "
                        "vertical lines stay straight")}},
    # 升降: 官方词 pedestal 0/3 从未执行 → 律按实测投影 tilt (v2 同词); 模型
    # 升级有新证据改回 pedestal = 改一行。
    "升降": {"dirs": ["上", "下"],
             "en": {"上": "tilts up", "下": "tilts down"},
             "v2": {"上": "上摇", "下": "下摇"}, "family": "tilt",
             "amp_speed": {"amp": "大", "speed": "慢"}, "domain": ["特写", "近景"],
             "close": {
                 "上": ("the view lifts past the original top edge of Picture 1 "
                        "while the subjects stay where they are in the world; "
                        "vertical lines stay straight"),
                 "下": ("the view drops past the original bottom edge of Picture 1 "
                        "while the subjects stay where they are in the world; "
                        "vertical lines stay straight")}},
}
# 消费端只读视图 (词面 + 律档): ④ 页 API 下发 / 枚举注入 / 校验同表
CAM_DEFS: dict[str, dict[str, Any]] = {
    k: {"dirs": list(m.get("dirs") or []), "static": bool(m.get("static")),
        "en": m["en"], "amp_speed": m["amp_speed"], "domain": list(m["domain"] or [])}
    for k, m in CAMERA_LAW.items()
}
NO_MOVE = ("待定", "固定")
SIZES = ("大场景", "全景", "中景", "近景", "特写")
BIG_SIZES = ("大场景", "全景")

_known_family = {"push", "pan", "tilt", "track", "arc"}
# import 期穷尽执法 (1106 同则, 1107 扩律字段): 漂移当场崩, 不可能静默
for _k, _m in CAMERA_LAW.items():
    if _m.get("static"):
        assert not _m["v2"] and not isinstance(_m["en"], dict), f"{_k} 固定镜无执行位"
        continue
    assert _m["family"] in _known_family, f"{_k} 未知执行族: {_m['family']}"
    _dirs = _m.get("dirs") or []
    if isinstance(_m["v2"], dict):
        assert set(_m["v2"]) == set(_dirs), f"{_k} v2 词表与 dirs 不齐"
    else:
        assert _m["v2"], f"{_k} 缺 v2 机位词"
        assert not _dirs, f"{_k} 方向型必须给 dict 词表"
    assert _m["amp_speed"] in (None, "free") or (
        isinstance(_m["amp_speed"], dict)
        and _m["amp_speed"].get("amp") in ("小", "中", "大")
        and _m["amp_speed"].get("speed") in ("慢", "中", "快")), \
        f"{_k} 幅速配方非法"
    for _d in (_m.get("domain") or []):
        assert _d in SIZES, f"{_k} 适用域「{_d}」不在景别枚举"
    if isinstance(_m["close"], dict):
        assert set(_m["close"]) == set(_dirs), f"{_k} 构图收口与 dirs 不齐"
    else:
        assert _m["close"], f"{_k} 缺构图收口 (四要素之四)"


def v2_camera_word(cam: dict[str, Any] | None) -> str:
    """v3 四元组 → v2 机位词 (⑥ 盖章投影与 ⑧ 运镜注入共用, 单源)。
    固定/待定/未配/律外旧型 (拉/跟/弧… 存量迁移走批 5) → ""。"""
    c = cam or {}
    m = CAMERA_LAW.get(str(c.get("kind") or ""))
    if not m:
        return ""
    v2 = m["v2"]
    if isinstance(v2, dict):
        return str(v2.get(str(c.get("dir") or ""), ""))
    return str(v2)


def cam_family(cam: dict[str, Any] | None) -> str:
    """v3 四元组 → 执行族 (h3.py 对症句选择; 固定/律外 → "")。"""
    m = CAMERA_LAW.get(str((cam or {}).get("kind") or ""))
    return str(m["family"]) if m else ""


def law_cam(cam: dict[str, Any] | None) -> dict[str, Any]:
    """四元组归一 (律执法): 固定型幅速清空; dict 配方型幅速强制档; free 型原样。
    律外旧型原样透传 (存量数据不在此处擅改, 迁移走批 5)。"""
    c = dict(cam or {})
    m = CAMERA_LAW.get(str(c.get("kind") or ""))
    if not m:
        return c
    if m.get("static"):
        c["amp"] = c["speed"] = ""
    elif isinstance(m["amp_speed"], dict):
        c["amp"] = m["amp_speed"]["amp"]
        c["speed"] = m["amp_speed"]["speed"]
    return c

MIN_MOVE_SLOT_S = 6.0    # 运镜镜槽长下限 (1024 ep6 s12 三连: 幅度天花板=槽长)
MAX_MOVE_PER_ARC = 2     # 每弧运镜镜上限 (运镜是叙事工具, 不是默认)

_AMP_EN = {"小": "small", "大": "large"}
_SPD_EN = {"慢": "slow", "快": "fast"}


def cam_clause(cam: dict[str, Any] | None, motion_en: str = "") -> str:
    """四元组 (+事件句) → 官方四要素运镜句 (④ 预览/⑥ 拼合/⑧ 注入三端单源):
    ① 三维句法 verb+幅+速 ② 嵌动作流 as-从句 ③ 视点消歧 ④ 构图收口。
    motion_en 在场 → "As {motion_en}, the camera …" (官方嵌动作流形态, ⑥ 拼
    beats 行用); 缺 → "as the scene's own action continues" (1024 w1 实测形态)。
    幅速: dict 配方型装配即执法 (一律渲染律档); free 型 中=省略 (官方无词)。
    固定/律外旧型 → "" (固定镜无镜头句)。"""
    c = law_cam(cam)
    kind = str(c.get("kind") or "待定")
    m = CAMERA_LAW.get(kind)
    if not m or m.get("static"):
        return ""
    en = m["en"]
    head = en[str(c.get("dir"))] if isinstance(en, dict) else en
    verb = head
    if str(c.get("amp") or "") in _AMP_EN:
        verb += f" with {_AMP_EN[str(c['amp'])]} amplitude"
    if str(c.get("speed") or "") in _SPD_EN:
        verb += f" at {_SPD_EN[str(c['speed'])]} speed"
    close = m["close"]
    if isinstance(close, dict):
        close = str(close.get(str(c.get("dir") or ""), ""))
    mv = str(motion_en or "").strip().rstrip(".")
    if mv:
        return (f"As {mv}, the camera {verb} — one continuous viewpoint move, "
                f"not subject motion: {close}.")
    return (f"The camera {verb} as the scene's own action continues — one "
            f"continuous viewpoint move, not subject motion: {close}.")


# ── 执行参数 (程序独占, LLM 无感) ──────────────────────────────────────

def steps_for(shot: dict[str, Any]) -> int:
    """步数档: 素材参考镜 12 (1024 R2 定稿: 步数买细节保真), 其余 8
    (1016 实测 6 步大动态拖影→8 修好; 10/12 无感 — h3 步数与运镜无关)。"""
    if (shot.get("asset_ref") or {}).get("image"):
        return 12
    return 8


def station_for(cam: dict[str, Any] | None) -> str:
    """工位路由: 运镜镜锁 h3 — r2v 是 4 步弹匣+无 608/无补丁, 运镜词进去=裸奔 (挂账在案)。"""
    kind = str((cam or {}).get("kind") or "")
    return "h3" if kind and kind not in NO_MOVE else "auto"


# ── 校验 (确定性; 生成时执法 + 人闸复核同尺) ───────────────────────────

def validate(direct: dict[str, dict[str, Any]], shots: list[dict[str, Any]]
             ) -> dict[str, list[str]]:
    """导演层校验: 枚举/槽长/固定镜纪律/相邻景别/配额。返回 {sid: [issue…]}。"""
    out: dict[str, list[str]] = {}
    by_arc: dict[str, list[dict]] = {}
    prev_size = ""
    for s in shots:
        sid = str(s["sid"])
        d = direct.get(sid) or {}
        cam = d.get("cam") or {}
        kind = str(cam.get("kind") or "待定")
        size = str(d.get("size") or "中景")
        iss: list[str] = []
        if kind not in CAMERA_LAW and kind != "待定":
            iss.append(f"运镜型「{kind}」不在枚举内 — 只准 {'/'.join(CAMERA_LAW)}")
        if kind in CAMERA_LAW:
            m = CAMERA_LAW[kind]
            dirs = m["dirs"]
            if dirs and cam.get("dir") not in dirs:
                iss.append(f"运镜「{kind}」方向只准 {'/'.join(dirs)} (现: {cam.get('dir')})")
            if not dirs and str(cam.get("dir") or "") not in ("", "无"):
                iss.append(f"运镜「{kind}」无方向 (现: {cam.get('dir')})")
            if m.get("static") and (cam.get("amp") or cam.get("speed")):
                iss.append("固定镜不带幅速 (官方口径)")
            if isinstance(m["amp_speed"], dict):
                w = m["amp_speed"]
                if (cam.get("amp"), cam.get("speed")) != (w["amp"], w["speed"]):
                    iss.append(f"运镜「{kind}」幅速只准 {w['amp']}+{w['speed']} "
                               f"(模型稳定档; 现 {cam.get('amp') or '空'}+{cam.get('speed') or '空'})")
            if m["domain"] and size not in m["domain"]:
                iss.append(f"运镜「{kind}」只准 {'/'.join(m['domain'])} — 大场景复杂图"
                           f"实测零命中 (现 {size}); 换景别或改固定")
        if float(s.get("est_s") or 0) > 15.0:      # 槽位硬上限 (1106 用户令: 导演层必须有概念)
            iss.append(f"槽长 {s.get('est_s')}s 超 H3 15s 上限 — 拆行或拆镜 (K2/TTS/H3 共同槽位轴)")
        if kind not in NO_MOVE and float(s.get("est_s") or 0) < MIN_MOVE_SLOT_S:
            iss.append(f"运镜镜槽长 {s.get('est_s')}s < {MIN_MOVE_SLOT_S:g}s — "
                       "幅度天花板=槽长, 只准固定")
        if size not in SIZES:
            iss.append(f"景别「{size}」不在枚举内")
        if size == prev_size:
            iss.append(f"与相邻镜景别相同 ({size}) — 至少换一档")
        prev_size = size
        if iss:
            out[sid] = iss
        by_arc.setdefault(str(s.get("arc_id") or ""), []).append(
            {"sid": sid, "size": size, "kind": kind})
    # 配额 (全集口径, 与 final_gate 同尺)
    n = len(shots) or 1
    big = sum(1 for s in shots
              if str((direct.get(str(s["sid"])) or {}).get("size")) in BIG_SIZES)
    need_big = max(1, -(-n // 4))
    if big < need_big:
        out.setdefault("弧级", []).append(
            f"景深配额: 大场景+全景 {big}/{n} (需≥{need_big})")
    micro = sum(1 for s in shots
                if str((direct.get(str(s["sid"])) or {}).get("size")) == "特写")
    max_micro = max(2, -(-n // 2))
    if micro > max_micro:
        out.setdefault("弧级", []).append(f"特写配额: {micro}/{n} 超上限 {max_micro}")
    for aid, ss in by_arc.items():
        n_move = sum(1 for x in ss if x["kind"] not in NO_MOVE)
        if n_move > MAX_MOVE_PER_ARC:
            out.setdefault("弧级", []).append(
                f"弧 {aid} 运镜镜 {n_move} 个 > {MAX_MOVE_PER_ARC} (运镜是叙事工具不是默认)")
    return out


# ── LLM: 四元组 + 运动光影 (规范用语前移; 枚举/档位/域由 CAMERA_LAW 派生) ─

SIZES_STR = "/".join(SIZES)


def _law_lines() -> list[str]:
    """律条提示行 (派生自 CAMERA_LAW — 改律一处, ④ 提示自动跟随)。"""
    out = []
    for k, m in CAMERA_LAW.items():
        if m.get("static"):
            continue
        bits = []
        if m.get("dirs"):
            bits.append("方向 " + "/".join(m["dirs"]))
        if isinstance(m["amp_speed"], dict):
            bits.append(f"幅速固定 {m['amp_speed']['amp']}+{m['amp_speed']['speed']}"
                        " (模型稳定档, 不准选其他)")
        elif m["amp_speed"] == "free":
            bits.append("幅速自由")
        if m.get("domain"):
            bits.append("只准 " + "/".join(m["domain"]) + " (大场景复杂图实测零命中)")
        out.append(f"- {k}: {'; '.join(bits)}")
    return out


_LAW_BLOCK = "\n".join(_law_lines())

_SYS = f"""你是短视频的动画导演。输入镜清单 (镜号|槽长秒|景别预填|视觉世界|画面句摘要),
为每镜定: 景别 / 运镜 / 主体运动 / 光影。

运镜律 (封闭枚举 + 实测档位, 只准从中选): {'/'.join(CAMERA_LAW)}
{_LAW_BLOCK}
幅度: 小|中|大; 速度: 慢|中|快 (中=官方无词, 程序自动省略; 固定镜幅速给空)。

创作铁律 (违反即打回):
1. **固定是默认** — 运镜是叙事工具: 场景揭示/规模呈现/跟随主体才用。
2. 槽长 <{MIN_MOVE_SLOT_S:g}s 的镜只准 固定 (短槽物理上折成小幅, 写了也白写)。
3. 摇/升降 幅速只准律档 (大+慢 — 模型稳定档); 推幅速自由; **每弧运镜镜至多 2 个**。
4. 景别**封闭枚举, 只准从中选**: {SIZES_STR} — 不准自创词 (大全景/中近景/远景 都非法)。
5. 景别节奏: 大场景+全景 约每 4 镜 1 镜; 特写不过半; **相邻镜景别必须换一档**。
6. 主体运动只动画面句里已有的实体, 禁添画面外动物/器物。
7. 光影与相邻镜连续 (同一场光源方向/色温不变), 写一句 (如 窗光侧逆)。
每镜一条 JSON: {{"shots":[{{"sid":"s1","size":"中景",
  "cam":{{"kind":"固定","dir":"","amp":"","speed":""}},
  "motion":"一句中文","light":"一句中文"}}]}}"""


def _batch_prompt(batch: list[dict[str, Any]], arc_of: dict[str, dict],
                  guess_of: dict[str, str]) -> str:
    rows = []
    for s in batch:
        a = arc_of.get(str(s.get("arc_id")) or "") or {}
        rows.append(f'{s["sid"]}|{s.get("est_s")}s|{guess_of.get(s["sid"], "中景")}'
                    f'|{str(a.get("world") or "")[:20]}|{str(a.get("narrative") or "")[:30]}'
                    f'|{str(s.get("keyframe_zh") or "")[:80]}')
    return "镜号|槽长|景别预填|视觉世界|本弧讲什么|画面句摘要\n" + "\n".join(rows)


def _llm_fill(shots: list[dict[str, Any]], arcs: list[dict[str, Any]],
              guess_of: dict[str, str], job: dict[str, Any] | None = None
              ) -> dict[str, dict[str, Any]]:
    """按批生成四元组; 批内自检 (枚举/槽长/相邻景别) 不达标整批补投一次。"""
    from app.services.llm_service import get_llm_service, _parse_json
    arc_of = {a["arc_id"]: a for a in arcs}
    _SIZE_FIX = {"大全景": "大场景", "远景": "大场景", "中近景": "近景",
                 "全远": "大场景", "特近": "特写"}
    direct: dict[str, dict[str, Any]] = {}
    total_b = -(-len(shots) // 8)
    for bi, i in enumerate(range(0, len(shots), 8)):
        batch = shots[i:i + 8]
        if job is not None:
            if job.get("cancel"):
                raise SystemExit("已取消")          # 批间生效 (面板 ⛔)
            job["progress"] = {"done": bi, "total": total_b,
                               "current": f"导演参数 {batch[0]['sid']}.."}
        prompt_b = _batch_prompt(batch, arc_of, guess_of)
        raw = get_llm_service().chat(_SYS, prompt_b, model="pro", temperature=0.4,
                                     response_format={"type": "json_object"},
                                     tag="v3_direct")
        got = {str(r.get("sid")): r for r in (_parse_json(raw).get("shots") or [])}
        bad = validate(got, batch)
        if any(k != "弧级" for k in bad):
            logger.info("[v3:direct] 批 %s 不达标, 补投一次", batch[0]["sid"])
            try:
                fb = "; ".join(f"{k}: {v[0][:40]}" for k, v in bad.items() if k != "弧级")
                raw2 = get_llm_service().chat(
                    _SYS, prompt_b + f"\n【上一版不达标: {fb}。整批重写。】",
                    model="pro", temperature=0.3,
                    response_format={"type": "json_object"}, tag="v3_direct")
                got2 = {str(r.get("sid")): r
                        for r in (_parse_json(raw2).get("shots") or [])}
                if not any(k != "弧级" for k in validate(got2, batch)):
                    got = got2
            except Exception as exc:  # noqa: BLE001
                logger.warning("[v3:direct] 批补投失败 (沿用首版): %s", exc)
        for s in batch:
            r = got.get(s["sid"])
            if not r:
                continue
            cam = law_cam(r.get("cam") or {})     # 律归一: 固定清幅速/配方型锁档
            kind = str(cam.get("kind") or "固定")
            direct[s["sid"]] = {
                "size": _SIZE_FIX.get(str(r.get("size")), str(r.get("size") or "中景")),
                "cam": {"kind": kind,
                        "dir": str(cam.get("dir") or ""),
                        "amp": "" if kind in NO_MOVE else str(cam.get("amp") or ""),
                        "speed": "" if kind in NO_MOVE else str(cam.get("speed") or "")},
                "motion": str(r.get("motion") or "")[:120],
                "light": str(r.get("light") or "")[:80],
            }
    # 每弧运镜 ≤MAX_MOVE_PER_ARC: 超额确定性降级为固定 (按镜序保留前 N 个)
    per_arc: dict[str, list[str]] = {}
    for s in shots:
        sid = str(s["sid"])
        d = direct.get(sid)
        if not d:
            continue
        aid = str(s.get("arc_id") or "")
        if str((d.get("cam") or {}).get("kind")) not in NO_MOVE:
            per_arc.setdefault(aid, []).append(sid)
    for aid, sids in per_arc.items():
        for sid in sids[MAX_MOVE_PER_ARC:]:
            direct[sid]["cam"] = {"kind": "固定", "dir": "", "amp": "", "speed": ""}
            logger.info("[v3:direct] 弧 %s 运镜超额, %s 降级固定", aid, sid)
    _fix_adjacency(direct, shots)
    return direct


def _fix_adjacency(direct: dict[str, dict[str, Any]], shots: list[dict[str, Any]]) -> int:
    """相邻镜景别相同 → 确定性换档 (挑与前后镜都不同的最近邻档)。

    批内自检看不见跨批边界 (s8/s9 相邻但不同批) — 整集终检在此修。
    """
    n = 0
    for i in range(1, len(shots)):
        sid = str(shots[i]["sid"])
        d = direct.get(sid)
        prev = direct.get(str(shots[i - 1]["sid"]))
        if not d or not prev:
            continue
        if d.get("size") != prev.get("size"):
            continue
        nxt = direct.get(str(shots[i + 1]["sid"])) if i + 1 < len(shots) else None
        cur = SIZES.index(d["size"])
        forbid = {prev.get("size"), (nxt or {}).get("size")}
        for delta in (1, -1, 2, -2):
            j = cur + delta
            if 0 <= j < len(SIZES) and SIZES[j] not in forbid:
                d["size"] = SIZES[j]
                n += 1
                break
    if n:
        logger.info("[v3:direct] 相邻同景别确定性换档 %d 处", n)
    return n


def reroll_one(shots: list[dict[str, Any]], arcs: list[dict[str, Any]],
               sid: str) -> dict[str, Any] | None:
    """单镜重掷 (④ 版, v2 cartRedo 对应位): 只重生成该镜四元组, 一镜一批 LLM。"""
    from app.services.llm_service import get_llm_service, _parse_json
    from app.services.anim_pipeline.v3_pipeline.shots import suggest_size
    s = next((x for x in shots if str(x["sid"]) == sid), None)
    if not s:
        return None
    arc_of = {a["arc_id"]: a for a in arcs}
    guess = {sid: suggest_size(s.get("keyframe_zh"))}
    got = _llm_fill([s], arcs, guess)
    return got.get(sid)


# ── 放行钩子 = 英文化批 (ID-131; 窗口唯一: ⑤要吃/放行前翻=改稿作废) ────

_EN_SYS = """You translate animation shot descriptions to English. Input lines:
sid|中文画面句|中文主体运动. Output strict JSON:
{"shots":[{"sid":"s1","keyframe_en":"...","motion_en":"..."}]}
Rules: keyframe_en is the SAME composition in English (no text/words may appear
in the described image); motion_en describes the same subject motion in English,
never mentioning any on-screen words. Plain faithful prose, no camera moves."""


def finalize(doc: dict[str, Any]) -> dict[str, Any]:
    """④ 放行: 批量翻译 keyframe_en/motion_en + opening_desc(=keyframe_en) + 冻结戳。

    幂等: 已 frozen 直接返回。冻结后 ③④ 改稿即作废翻译 → 页面转只读 (前端按
    v3.frozen_at 判), 重放行须先解冻 (当前设计: 重跑 ③ 即清戳)。
    """
    import json as _json
    from app.services.llm_service import get_llm_service, _parse_json
    v3 = doc.setdefault("v3", {})
    if v3.get("frozen_at"):
        return {"frozen": True, "already": True,
                "translated": sum(1 for s in (v3.get("shots") or []) if s.get("keyframe_en"))}
    shots = v3.get("shots") or []
    direct = v3.get("direct") or {}
    issues = v3.get("direct_issues") or {}
    if any(k != "弧级" for k in issues) or len(direct) < len(shots):
        raise SystemExit("导演未达标 — 先清镜级问题并填满 34 镜再放行")
    todo = [s for s in shots if not s.get("keyframe_en")]
    rows = [f'{s["sid"]}|{str(s.get("keyframe_zh") or "")[:400]}'
            f'|{str((direct.get(s["sid"]) or {}).get("motion") or "")[:100]}'
            for s in todo]
    done = 0
    for i in range(0, len(rows), 12):
        batch = rows[i:i + 12]
        raw = get_llm_service().chat(_EN_SYS, "\n".join(batch), model="pro",
                                     temperature=0.2,
                                     response_format={"type": "json_object"},
                                     tag="v3_en")
        got = {str(r.get("sid")): r for r in (_parse_json(raw).get("shots") or [])}
        for s in todo:
            r = got.get(s["sid"])
            if r:
                s["keyframe_en"] = str(r.get("keyframe_en") or "")[:800]
                s["opening_desc"] = s["keyframe_en"]     # v2 锚定链: 逐字同构
                d = direct.get(s["sid"])
                if d is not None and r.get("motion_en"):
                    d["motion_en"] = str(r.get("motion_en"))[:400]
                done += 1
    v3["frozen_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    v3.setdefault("gates", {})["direct"] = True     # 放行=开闸 (轨/⑤ 的 direct 闸点亮)
    logger.info("[v3:direct] 放行英文化 %d/%d 镜, frozen_at=%s", done, len(todo), v3["frozen_at"])
    return {"frozen": True, "translated": done, "total": len(todo)}


def run(job: dict[str, Any]) -> None:
    """v3_direct 相位本体: 读 v3.shots → LLM 四元组 → 校验 → v3.direct 落盘。"""
    from app.services.anim_pipeline import shots as shots_mod
    from app.services.anim_pipeline.v3_pipeline.shots import suggest_size

    doc = shots_mod.load_doc(job["book_title"], job["ep"])
    v3 = doc.setdefault("v3", {})
    shots = v3.get("shots") or []
    if not shots:
        raise SystemExit("未切镜 — 先跑 ③ 分镜文字")
    job["progress"] = {"done": 0, "total": 1, "current": "导演参数生成中"}
    guess_of = {s["sid"]: suggest_size(s.get("keyframe_zh")) for s in shots}
    direct = _llm_fill(shots, v3.get("arcs") or [], guess_of, job)
    issues = validate(direct, shots)
    # 步数/工位/英文句是执行参数, 随镜固化 (H3 壳直接取, 不再判)
    for s in shots:
        d = direct.get(s["sid"])
        if d:
            d["steps"] = steps_for(s)
            d["station"] = station_for(d.get("cam"))
            d["clause"] = cam_clause(d.get("cam"), str(d.get("motion_en") or ""))
    v3["direct"] = direct
    v3["direct_issues"] = issues
    v3.setdefault("gates", {})["direct"] = False      # 人闸: 前端确认放行
    v3["direct_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    shots_mod.save(doc)
    job["result"] = {"shots": len(direct),
                     "issues": sum(len(v) for v in issues.values()),
                     "moves": sum(1 for d in direct.values()
                                  if str((d.get("cam") or {}).get("kind"))
                                  not in NO_MOVE)}
    logger.info("[v3:direct] %s ep%d: %d 镜导演参数 (问题 %d) → shots.json v3 ✓",
                job["book_title"], job["ep"], len(direct), job["result"]["issues"])
