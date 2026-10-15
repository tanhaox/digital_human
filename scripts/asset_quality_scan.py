# -*- coding: utf-8 -*-
"""素材库 VLM 内容质量打分 — 烂素材治理③ (2026-08-16).

与旧维度打标的区别: 那次问的是"素材是什么"(色调/动态/密度), 这次问的是
"素材好不好" — 专业剪辑师验收标准, 含 generic-stock 惩罚。

用法:
    python scripts/asset_quality_scan.py              # 全量扫 (跳过已打分)
    python scripts/asset_quality_scan.py --limit 20   # 先试 20 个看效果
    python scripts/asset_quality_scan.py --top-bad 30 # 看已打分里最差的
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_config, load_config, set_config

set_config(load_config())
from app.database import init_db, get_session_maker

init_db("sqlite:///data/pipeline.db")
db = get_session_maker()()
from app.models import VideoAsset

# ── 深度验收 rubric (治"规则太浅": 不再列维度, 直接按剪辑师验收标准裁决) ──
QUALITY_SYSTEM_PROMPT = """你是给知识类短视频供片的资深剪辑师, 正在验收 B-roll 素材库。
逐帧看图后, 按"这条素材敢不敢放进成片"打分。判断标准从严:

【一票否决 (≤3 分)】
- 画面涂抹/ Upscale 泥糊感、大面积噪点、严重压缩伪影
- 手持乱抖、失焦、过曝死白/欠曝死黑
- 内容空洞的 generic stock 货色: 千篇一律的城市空镜/路人不看镜头走路/
  摆拍的"商务人士握手微笑"/假装打字的键盘特写/地球网络科技动画
  (这类素材观众一眼识破"图库感", 是烂片感的最大来源)

【中等 (4-6 分)】
- 技术合格但平庸: 构图平常、光线平、信息量低
- 有明显瑕疵但不致命 (轻微偏色、地平线微斜)

【优良 (7-8 分)】
- 构图有主体有层次、光线有情绪、画面干净锐利
- 内容具体有信息量 (真实的机器运转/实验室/数据机房/人物在做具体的事)

【顶级 (9-10 分)】
- 电影感: 光影出色、色彩和谐、有叙事张力, 单帧就能留住观众

只输出一行 JSON:
{"quality_score": <1-10 整数>, "generic_stock": <true|false>, "verdict": "<不超过20字的中文裁决, 说清为什么>"}
不要输出任何其他文字。"""


def extract_two_frames(video: Path, tmpdir: Path) -> list[Path]:
    """首 1/3 与 2/3 处各抽一帧 (够判断质量, 比 smart_extract 快)."""
    import subprocess

    dur_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=20,
    )
    try:
        dur = float(dur_probe.stdout.strip())
    except ValueError:
        dur = 6.0
    frames = []
    for i, ratio in enumerate((0.33, 0.72)):
        out = tmpdir / f"q{i}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(dur * ratio - 0.1, 0):.2f}",
             "-i", str(video), "-vframes", "1", "-q:v", "3", str(out)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            frames.append(out)
    return frames


def parse_score(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        s = int(d.get("quality_score", 0))
        if 1 <= s <= 10:
            return {"score": s, "generic": bool(d.get("generic_stock")),
                    "verdict": str(d.get("verdict", ""))[:60]}
    except Exception:
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--top-bad", type=int, default=0)
    args = ap.parse_args()

    if args.top_bad:
        rows = (db.query(VideoAsset)
                .filter(VideoAsset.quality_score.isnot(None))
                .order_by(VideoAsset.quality_score.asc())
                .limit(args.top_bad).all())
        for a in rows:
            print(f"{a.quality_score:>2} generic={int(bool(a.quality_reason and 'generic' in a.quality_reason))} "
                  f"{a.asset_no} {(a.description_zh or '?')[:30]} | {a.quality_reason}")
        return

    # 目标: 未打分 + 未出局
    q = db.query(VideoAsset).filter(
        VideoAsset.quality_score.is_(None),
        VideoAsset.preference != "dislike",
        VideoAsset.file_path.isnot(None),
    )
    if args.limit:
        q = q.limit(args.limit)
    todo = q.all()
    print(f"待打分: {len(todo)} 个")

    # 拉起本地视觉模型
    from app.services.local_llm_client import LocalLLMClient
    from app.services.video_tagging_service.llama import _ensure_llama_server

    if not _ensure_llama_server():
        sys.exit("llama-server 拉起失败")
    client = LocalLLMClient(get_config().local_llm)

    done = fail = 0
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        for a in todo:
            video = Path(a.file_path)
            if not video.exists():
                a.quality_score = -1  # 文件丢失标记, 不再重试
                a.quality_reason = "文件不存在"
                db.commit()
                continue
            sub = tmpdir / a.id[:8]
            sub.mkdir(exist_ok=True)
            try:
                frames = extract_two_frames(video, sub)
                if not frames:
                    raise RuntimeError("抽帧失败")
                raw = ""
                for attempt in range(2):  # 空响应/解析失败重试一次
                    raw = client.chat_with_images(
                        image_paths=frames,
                        system_prompt=QUALITY_SYSTEM_PROMPT,
                        user_prompt="给这条 B-roll 素材打质量分。",
                    )
                    parsed = parse_score(raw)
                    if parsed:
                        break
                if not parsed:
                    raise RuntimeError(f"解析失败: {raw[:80]}")
                a.quality_score = float(parsed["score"])
                a.quality_reason = ("generic;" if parsed["generic"] else "") + parsed["verdict"]
                db.commit()
                done += 1
                if done % 10 == 0:
                    print(f"  进度 {done}/{len(todo)}")
            except Exception as exc:
                fail += 1
                print(f"  ✗ {a.asset_no}: {str(exc)[:60]}")
            finally:
                for f in sub.glob("*.jpg"):
                    f.unlink(missing_ok=True)

    print(f"\n完成: {done} 打分 / {fail} 失败")
    # 分布速览
    rows = db.query(VideoAsset.quality_score).filter(VideoAsset.quality_score > 0).all()
    if rows:
        from collections import Counter
        dist = Counter(int(r[0]) for r in rows)
        print("分数分布:", dict(sorted(dist.items())))
        low = sum(1 for r in rows if r[0] <= 3)
        print(f"≤3 分 (建议出局): {low}")


if __name__ == "__main__":
    main()
