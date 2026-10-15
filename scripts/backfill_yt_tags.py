# -*- coding: utf-8 -*-
"""yt 素材 9 维回填 (2026-08-28): 471 条 youtube 官片素材 scenes/shot_types 等
门槛维全空 → 常规碰撞永远看不见它们 (用户令: yt 切片优先级太低 根因之一)。

flash 按 desc_zh+keywords+tags 映射词表包枚举 → 写回 9 维列。
可重跑 (已有标签的跳过)。
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

VOCAB = json.loads(Path("data/vocabulary_pack.json").read_text(encoding="utf-8"))["dimensions"]

PROMPT_TPL = """你是素材库管理员。给下面的视频素材补检索维度标签，只输出 JSON 数组。

# 枚举 (只能从中选)
scenes: {scenes}
shot_types: {shot_types}
tone: {tone}
motion_level: {motion_level}
content_density: {content_density}
time_of_day: {time_of_day}

# 规则
- scenes/shot_types 各选 1-2 个最贴切的; tone/motion_level/content_density/time_of_day 各选 1 个
- 产品特写→shot_types 选"特写"; 城市航拍→"航拍"+"城市"; 室内产品图→time_of_day 选 "indoor"
- 不确定的维度给枚举里最中性的值, 禁止编造枚举外的词
- 输出与输入同序: [{{"i": 0, "scenes": ["科技"], "shot_types": ["特写"], "tone": "cool", "motion_level": "slow", "content_density": "moderate", "time_of_day": "indoor"}}, ...]

# 素材清单
{items}"""


def main() -> None:
    init_db("sqlite:///data/pipeline.db")
    from app.services.boost_service import _call, _extract_json

    with get_session_maker()() as db:
        rows = (db.query(VideoAsset)
                .filter(VideoAsset.source == "youtube")
                .order_by(VideoAsset.asset_no).all())
        todo = [a for a in rows
                if not a.scenes or a.scenes in ("[]", "null", "")
                or not a.shot_types or a.shot_types in ("[]", "null", "")]
        print(f"youtube 素材 {len(rows)} 条, 待回填 {len(todo)}")

        batch = 10  # 20/批实测 thinking 截断近半 (120/471), 10/批稳
        done = 0
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            items = json.dumps([{
                "i": j, "desc_zh": a.description_zh or "",
                "kw": (a.description_en or "")[:80],
                "tags": (a.tags or [])[:6],
            } for j, a in enumerate(chunk)], ensure_ascii=False)
            prompt = PROMPT_TPL.format(
                scenes="/".join(VOCAB["scenes"]), shot_types="/".join(VOCAB["shot_types"]),
                tone="/".join(VOCAB["tone"]), motion_level="/".join(VOCAB["motion_level"]),
                content_density="/".join(VOCAB["content_density"]),
                time_of_day="/".join(VOCAB["time_of_day"]), items=items)
            try:
                raw = _call(prompt, json_mode=True, max_tokens=6000, temperature=0.2)
                data = _extract_json(raw) or []
            except Exception as exc:
                print(f"  批 {i//batch} 失败跳过: {str(exc)[:60]}")
                continue
            by_i = {d.get("i"): d for d in data if isinstance(d, dict)}
            for j, a in enumerate(chunk):
                d = by_i.get(j)
                if not d:
                    continue
                a.scenes = json.dumps(d.get("scenes") or [], ensure_ascii=False)
                a.shot_types = json.dumps(d.get("shot_types") or [], ensure_ascii=False)
                a.tone = d.get("tone") or "neutral"
                a.motion_level = d.get("motion_level") or "slow"
                a.content_density = d.get("content_density") or "moderate"
                a.time_of_day = d.get("time_of_day") or "undefined"
                done += 1
            db.commit()
            print(f"  批 {i//batch + 1}/{(len(todo)+batch-1)//batch}: 累计 {done}")
        print(f"回填完成 {done}/{len(todo)}")


if __name__ == "__main__":
    main()
