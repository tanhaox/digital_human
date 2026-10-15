# -*- coding: utf-8 -*-
"""9B vs 30B 视觉打标交叉对比 (2026-08-28).

抽样 20 片 (分实体), 同帧同 TAG_PROMPT 跑 30B-A3B, 与已入库的 9B 标签对比:
- has_burned_text 一致率 (9B 可信度的机器侧估计)
- 描述质量并排展示
"""
import base64
import glob
import json
import random
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sandbox.yt_ingest import TAG_PROMPT  # noqa: E402

URL = "http://127.0.0.1:8081/v1/chat/completions"


def ask_30b(img_path: str, prompt: str) -> str:
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()
    body = {
        "model": "qwen3-vl",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": 600, "temperature": 0.2,
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return out["choices"][0]["message"]["content"]


def parse_tag(raw: str) -> dict:
    import re
    raw = re.sub(r"<think>.*?</think>", "", str(raw), flags=re.S)  # 剥思考链
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"parse_fail": True, "raw": raw[:120]}


def main() -> None:
    random.seed(42)
    pool = []  # (entity, clip, frame_path, tag_9b)
    for mf in sorted(glob.glob("data/materials/youtube/*/manifest.json")):
        m = json.loads(open(mf, encoding="utf-8").read())
        for c in m.get("clips", []):
            fr = f"data/materials/youtube/{m['video_id']}/frame_{c['n']:03d}.png"
            if c.get("tags") and glob.glob(fr):
                pool.append((m.get("entity") or "?", c, fr, c["tags"]))
    sample = random.sample(pool, 20)
    agree = disagree = 0
    for ent, c, fr, t9 in sample:
        t30 = parse_tag(ask_30b(fr, TAG_PROMPT))
        if t30.get("parse_fail"):
            print(f"[{ent}] clip_{c['n']:03d} 30B解析失败: {t30['raw'][:60]}")
            continue
        b9, b30 = t9.get("has_burned_text"), t30.get("has_burned_text")
        same = (b9 == b30)
        agree += same
        disagree += not same
        mark = "✓" if same else "✗✗✗"
        print(f"[{ent}] clip_{c['n']:03d} {mark} 字幕 9B={b9} 30B={b30}")
        print(f"    9B : {str(t9.get('desc_zh'))[:40]} | {str(t9.get('keywords_en'))[:50]}")
        print(f"    30B: {str(t30.get('desc_zh'))[:40]} | {str(t30.get('keywords_en'))[:50]}")
    n = agree + disagree
    print(f"\n字幕判定一致率: {agree}/{n} = {agree/n*100:.0f}%")


if __name__ == "__main__":
    main()
