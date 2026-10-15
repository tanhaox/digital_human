# -*- coding: utf-8 -*-
"""非 RLR 存量 youtube 素材实拍复判 (2026-09-01 用户令兜底).

背景: 入库判据收敛为 is_real_footage (只收摄像机实拍); RLR 5859 行经重切+stage2
重生自动过滤, 但 362 行非 RLR 早期存量不走重生 — 本脚本 VLM 复判踢出。
判定: 抽中点帧 → tools.vision.analyze (llama, 内建 GPU-VPN 守卫) →
is_real_footage != true 即踢 (删行 + 文件回收站)。

⚠️ GPU 任务: 与 rlr_stage2_parallel 错开, 整链完成后再跑。
用法: python scripts/recheck_real_footage.py [--dry-run]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
ROOT = Path(".").resolve()

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")

PROMPT = """你是视频素材审核员。看这一帧。只输出 JSON:
{"is_real_footage": true/false, "reason": "≤10字"}
is_real_footage=true 当且仅当: 摄像机实拍的真实世界画面
(真实人物/街景/城市/自然/建筑/工厂/交通/器物/真实事件现场)。
is_real_footage=false 当画面是任何自制/合成/非实拍产物:
地图/地形图/国旗叠加/图表/3D渲染/CG动画/游戏画面/AI生成感/
剪影+纯色渐变/商品棚拍/截图/文字卡/黑白老胶片/历史档案。"""


def _mid_frame(path: str, dur: float) -> Path | None:
    tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tf.close()
    out = Path(tf.name)
    r = subprocess.run([FF, "-y", "-v", "error", "-ss", f"{max(0.0, dur / 2):.1f}",
                        "-i", path, "-frames:v", "1", "-vf", "scale=768:-2",
                        "-q:v", "3", str(out)],
                       capture_output=True, timeout=30)
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        return None
    return out


def main() -> None:
    dry = "--dry-run" in sys.argv
    import glob
    rlr_vids = set()
    for mf in glob.glob(str(ROOT / "data/materials/youtube/*/manifest.json")):
        m = json.loads(Path(mf).read_text(encoding="utf-8"))
        if m.get("entity") == "RealLifeLore":
            rlr_vids.add(m["video_id"])
    import os
    from tools.vision import analyze
    db = get_session_maker()()
    rows = [a for a in db.query(VideoAsset).filter(VideoAsset.source == "youtube").all()
            if a.file_path and Path(a.file_path).exists()
            and a.file_path.split(os.sep)[-3] not in rlr_vids]
    print(f"待复判 (非 RLR 存量): {len(rows)}{' (dry-run)' if dry else ''}", flush=True)
    kick: list[str] = []
    for i, a in enumerate(rows, 1):
        frame = _mid_frame(a.file_path, a.duration_sec or 5.0)
        if frame is None:
            continue
        try:
            raw = analyze([str(frame)], PROMPT)
            m = re.search(r"\{.*\}", str(raw), re.S)
            verdict = json.loads(m.group(0)) if m else {}
        except Exception as exc:
            print(f"  {a.asset_no} analyze 失败: {str(exc)[:60]}", flush=True)
            verdict = {}
        finally:
            frame.unlink(missing_ok=True)
        if verdict.get("is_real_footage") is not True:
            kick.append(a.id)
            print(f"  [{i}/{len(rows)}] {a.asset_no} 踢: {verdict.get('reason', '?')} "
                  f"| {a.description_zh[:20]}", flush=True)
    print(f"复判完: 踢 {len(kick)} / {len(rows)}", flush=True)
    if not dry and kick:
        for aid in kick:
            a = db.get(VideoAsset, aid)
            if a and a.file_path and Path(a.file_path).exists():
                try:
                    send2trash(a.file_path)
                except Exception:
                    pass
            db.delete(a)
        db.commit()
        print(f"已踢出 {len(kick)} 行 (文件回收站)", flush=True)
    db.close()


if __name__ == "__main__":
    main()
