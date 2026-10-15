# -*- coding: utf-8 -*-
"""对标视频阶段判定器 (0911 SOP): 抓到的账号+视频数据 → 爆款/阶段/排除 归架.

用法 (两种):
  A. 交互补数: python scripts/bench_classify.py  (逐项问你要数字)
  B. 管道:     echo '{...}' | python scripts/bench_classify.py --json
     JSON: {"likes": 该视频点赞, "followers": 粉丝, "total_likes": 获赞总量,
            "recent_likes": [最近N条点赞...], "account_age_weeks": 注册周数(近似可null),
            "publish_date": "YYYY-MM-DD"}

输出: 判定 + 依据链 + 置信度 + 入库建议 (hits/stage/mid/exclude + 排除原因)
"""
from __future__ import annotations

import io
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TH = json.loads((ROOT / "config" / "bench_thresholds.json").read_text(encoding="utf-8"))



# ── 赛道判别 (0911): 乱链接先分流 — 拆书/科技/地缘/财经/其他, 决定入哪个产线的对标库 ──
_TRACK_KW = {
    "book": ["读书", "拆书", "书里", "这本书", "书评", "书单", "作者认为", "荐书", "读后"],
    "tech": ["AI", "科技", "芯片", "算力", "大模型", "机器人", "新能源", "数码", "半导体", "互联网", "算法", "开源"],
    "geo": ["地缘", "国际", "美国", "俄罗", "中东", "台海", "制裁", "外交", "军事", "全球", "贸易战", "欧洲", "日本"],
    "finance": ["财经", "股市", "基金", "经济", "投资", "金融", "房价", "货币", "通胀", "财报"],
}
_TRACK_CN = {"book": "拆书", "tech": "科技", "geo": "地缘", "finance": "财经"}


def detect_track(title: str, author: str = "", desc: str = "") -> tuple[str, float]:
    """标题/作者名/简介 → (赛道, 置信0~1)。关键词命中计分, 无命中→other。"""
    text = f"{title} {author} {desc}"
    scores = {}
    for tr, kws in _TRACK_KW.items():
        hit = sum(1 for k in kws if k in text)
        if hit:
            scores[tr] = hit
    if not scores:
        return "other", 0.0
    best = max(scores, key=scores.get)
    conf = min(1.0, scores[best] / 3.0)  # 3+命中满置信
    return best, round(conf, 2)


def classify(d: dict) -> dict:
    """确定性判定: 每个结论必须带依据链 (哪指标哪规则)."""
    likes = d.get("likes") or 0
    followers = d.get("followers") or 0
    recent = [x for x in (d.get("recent_likes") or []) if isinstance(x, (int, float))]
    med = statistics.median(recent) if recent else None
    age_w = d.get("account_age_weeks")  # 可能 None (平台不暴露, 近似)

    chain = []
    conf = "high"

    # 1. 爆款 (最优先 — 大流量验证)
    if likes >= TH["hit_min_likes"]:
        chain.append(f"点赞 {likes:,} ≥ 爆款线 {TH['hit_min_likes']:,}")
        return {"verdict": "hit", "shelf": "hits", "chain": chain, "confidence": conf,
                "note": "结构经大流量验证 — 全篇 teardown 入爆款架"}

    # 2. 死号排除
    if med is not None and med < TH["dead_median_max"]:
        chain.append(f"近10条点赞中位 {med} < 死号线 {TH['dead_median_max']} — 没活下来, 无可学")
        return {"verdict": "exclude_dead", "shelf": None, "chain": chain, "confidence": conf,
                "note": "排除: 死号"}

    # 3. 阶段样本 (需全部满足; 注册时间缺失则降置信)
    if med is None:
        return {"verdict": "insufficient", "shelf": None,
                "chain": ["缺最近视频点赞列表"], "confidence": "low",
                "note": "数据不足, 无法判定 — 补 recent_likes"}
    ok_stage = []
    if followers < TH["stage_max_followers"]:
        ok_stage.append(f"粉丝 {followers:,} < {TH['stage_max_followers']:,}")
    if TH["stage_median_min"] <= med <= TH["stage_median_max"]:
        ok_stage.append(f"近10条中位 {med} 在 {TH['stage_median_min']}~{TH['stage_median_max']} (活号)")
    lift = likes / med if med else 0
    if lift >= TH["stage_lift_ratio"]:
        ok_stage.append(f"该条 {likes:,} ≥ 中位×{TH['stage_lift_ratio']} ({lift:.1f}x) — 乱流里活下来了")
    if age_w is not None and age_w <= TH["stage_max_age_weeks"]:
        ok_stage.append(f"注册 ~{age_w}周 ≤ {TH['stage_max_age_weeks']}周")
    elif age_w is not None and age_w > TH["old_account_weeks"]:
        chain.append(f"注册 ~{age_w}周 > {TH['old_account_weeks']}周 — 老号")
        return {"verdict": "exclude_old", "shelf": None, "chain": chain, "confidence": "mid",
                "note": "排除: 老号 (注册时间过滤器), 偶发几百赞不构成冷启动样本"}
    # 阶段四条件: 粉丝/中位/该条lift/注册 — 前三硬性, 注册缺失降置信不否决
    if len(ok_stage) >= 3:
        chain.extend(ok_stage)
        if age_w is None:
            conf = "mid"
            chain.append("注册时间缺失 (近似不可得) — 置信降级, 建议人工补最早视频日期")
        return {"verdict": "stage", "shelf": "stage", "chain": chain, "confidence": conf,
                "note": "冷启动存活样本 — 轻拆解 (钩子+前3秒+完播结构) 入阶段架"}

    # 4. 中间态
    chain.append(f"点赞 {likes:,} 未达爆款线; 阶段条件仅满足 {len(ok_stage)}/3: {ok_stage or '无'}")
    return {"verdict": "mid", "shelf": "mid", "chain": chain, "confidence": conf,
            "note": "中间态 — 记录观察, 不入两架"}


def main() -> int:
    if "--json" in sys.argv:
        raw = io.open(0, encoding="utf-8").read() if not sys.stdin.isatty() else ""
        d = json.loads(raw)
    else:
        d = {}
        d["likes"] = int(input("该视频点赞数: ") or 0)
        d["followers"] = int(input("作者粉丝数: ") or 0)
        d["total_likes"] = int(input("作者获赞总量: ") or 0)
        rl = input("最近10条视频点赞 (逗号分隔): ")
        d["recent_likes"] = [int(x) for x in rl.replace("，", ",").split(",") if x.strip().isdigit()]
        aw = input("注册周数 (近似, 回车跳过): ")
        d["account_age_weeks"] = int(aw) if aw.strip().isdigit() else None
    r = classify(d)
    tr, tc = detect_track(d.get("title") or "", d.get("author") or "", d.get("desc") or "")
    r["track"] = tr
    r["track_cn"] = _TRACK_CN.get(tr, "其他")
    r["track_confidence"] = tc
    r["shelf"] = f"{tr}/{r['shelf']}" if r.get("shelf") and r["shelf"] not in (None,) and tr != "other" else r.get("shelf")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
