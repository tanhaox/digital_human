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

# ── 官方运镜词表 (1105 网络核对: MiniMax H3 h3-prompt-writing; 指南附录) ──
# dirs 空 = 无方向型; static = 固定镜 (官方无幅速)。
CAM_DEFS: dict[str, dict[str, Any]] = {
    "固定":   {"dirs": [], "static": True, "en": "holds a static shot"},
    "推":     {"dirs": [], "en": "pushes in"},
    "拉":     {"dirs": [], "en": "pulls out"},
    "变焦推": {"dirs": [], "en": "zooms in"},
    "变焦拉": {"dirs": [], "en": "zooms out"},
    "摇":     {"dirs": ["左", "右", "上", "下"],
               "en": {"左": "pans left", "右": "pans right",
                      "上": "tilts up", "下": "tilts down"}},
    "移":     {"dirs": ["左", "右"], "en": {"左": "trucks left", "右": "trucks right"}},
    "升降":   {"dirs": ["上", "下"], "en": {"上": "pedestals up", "下": "pedestals down"}},
    "跟":     {"dirs": ["左", "右"], "en": {"左": "tracks left", "右": "tracks right"}},
    "弧":     {"dirs": [], "en": "arcs around the subject"},
}
NO_MOVE = ("待定", "固定")
SIZES = ("大场景", "全景", "中景", "近景", "特写")
BIG_SIZES = ("大场景", "全景")

MIN_MOVE_SLOT_S = 6.0    # 运镜镜槽长下限 (1024 ep6 s12 三连: 幅度天花板=槽长)
MAX_MOVE_PER_ARC = 2     # 每弧运镜镜上限 (运镜是叙事工具, 不是默认)


def cam_clause(cam: dict[str, Any] | None) -> str:
    """四元组 → H3 官方句 (幅/速 中=省略不写; 固定镜无幅速)。与 ④ 页预览同表。"""
    c = cam or {}
    kind = str(c.get("kind") or "待定")
    d = CAM_DEFS.get(kind)
    if not d:
        return ""
    if d.get("static"):
        return "The camera holds a static shot."
    en = d["en"]
    head = en[str(c.get("dir"))] if isinstance(en, dict) else en
    amp = {"小": "with small amplitude",
           "大": "with large amplitude"}.get(str(c.get("amp") or ""), "")
    spd = {"慢": "at slow speed",
           "快": "at fast speed"}.get(str(c.get("speed") or ""), "")
    return f"The camera {' '.join(x for x in (head, amp, spd) if x)}."


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
        if kind not in CAM_DEFS and kind != "待定":
            iss.append(f"运镜型「{kind}」不在枚举内 — 只准 {'/'.join(CAM_DEFS)}")
        if kind in CAM_DEFS:
            dirs = CAM_DEFS[kind]["dirs"]
            if dirs and cam.get("dir") not in dirs:
                iss.append(f"运镜「{kind}」方向只准 {'/'.join(dirs)} (现: {cam.get('dir')})")
            if not dirs and str(cam.get("dir") or "") not in ("", "无"):
                iss.append(f"运镜「{kind}」无方向 (现: {cam.get('dir')})")
            if CAM_DEFS[kind].get("static") and (cam.get("amp") or cam.get("speed")):
                iss.append("固定镜不带幅速 (官方口径)")
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


# ── LLM: 四元组 + 运动光影 (规范用语前移; 枚举由 CAM_DEFS 注入) ────────

_SYS = f"""你是短视频的动画导演。输入镜清单 (镜号|槽长秒|景别预填|视觉世界|画面句摘要),
为每镜定: 景别 / 运镜 / 主体运动 / 光影。

运镜词表 (封闭枚举, 只准从中选): {'/'.join(CAM_DEFS)}
方向: 摇(左/右/上/下), 移·跟(左/右), 升降(上/下); 其余型无方向 (给空)。
幅度: 小|中|大; 速度: 慢|中|快 (中=官方无词, 程序自动省略; 固定镜幅速给空)。

创作铁律 (违反即打回):
1. **固定是默认** — 运镜是叙事工具: 场景揭示/规模呈现/跟随主体才用。
2. 槽长 <{MIN_MOVE_SLOT_S:g}s 的镜只准 固定 (短槽物理上折成小幅, 写了也白写)。
3. 大幅配慢速是稳定组合; 快速是激进档, 少用。
4. 景别节奏: 大场景+全景 约每 4 镜 1 镜; 特写不过半; **相邻镜景别必须换一档**。
5. 主体运动只动画面句里已有的实体, 禁添画面外动物/器物。
6. 光影与相邻镜连续 (同一场光源方向/色温不变), 写一句 (如 窗光侧逆)。
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
            cam = r.get("cam") or {}
            kind = str(cam.get("kind") or "固定")
            direct[s["sid"]] = {
                "size": str(r.get("size") or "中景"),
                "cam": {"kind": kind,
                        "dir": str(cam.get("dir") or ""),
                        "amp": "" if kind in NO_MOVE else str(cam.get("amp") or ""),
                        "speed": "" if kind in NO_MOVE else str(cam.get("speed") or "")},
                "motion": str(r.get("motion") or "")[:120],
                "light": str(r.get("light") or "")[:80],
            }
    return direct


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
            d["clause"] = cam_clause(d.get("cam"))
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
