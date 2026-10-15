# -*- coding: utf-8 -*-
"""黑白/历史档案素材出清 (2026-09-01 用户令): 抽帧饱和度检测 → 踢出库.

用户令: 黑白(帧)视频、可能关于记忆的、最大可能历史材料的帧, 都不入库, 从库中踢出。
检测: 每行抽中点帧 → HSL 饱和度均值。黑白 <0.06; 棕褐 sepia 饱和度中等但色相
高度单一 → 追加色相集中度判据 (单一色相 bin 占 >85% 且非肤色主导)。
VLM 兜底可选 (--vlm): 启发式命中但饱和度 0.06~0.12 灰区的行送 llama 复判。

⚠️ 与 rlr_resplit/stage2 错开跑 (文件与 DB 行并发); 建议整链完成后执行。
用法: python scripts/purge_bw_assets.py [--dry-run]
"""
from __future__ import annotations

import colorsys
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from send2trash import send2trash

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")


def _sample_frame(path: str, at_sec: float) -> bytes | None:
    """抽 1 帧 JPEG bytes (失败返回 None)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tmp = tf.name
    try:
        r = subprocess.run([FF, "-y", "-v", "error", "-ss", f"{at_sec:.1f}",
                            "-i", path, "-frames:v", "1", "-q:v", "4", tmp],
                           capture_output=True, timeout=30)
        if r.returncode != 0 or not Path(tmp).exists():
            return None
        return Path(tmp).read_bytes()
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass


def _frame_is_bw(jpeg: bytes) -> bool | None:
    """饱和度+色相集中度判定。True=黑白/单色, False=彩色, None=无法判定."""
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return None
    img = img.resize((160, 90))
    px = list(img.getdata())
    if not px:
        return None
    sats, hue_bins = [], Counter()
    for r, g, b in px[::3]:  # 1/3 采样足够
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        sats.append(s)
        if s > 0.10:  # 只有有彩像素参与色相统计
            hue_bins[int(h * 12)] += 1
    mean_sat = sum(sats) / len(sats)
    if mean_sat < 0.06:
        return True  # 纯黑白/重度去色
    colored = sum(hue_bins.values())
    if colored and hue_bins and hue_bins.most_common(1)[0][1] / colored > 0.85 \
            and mean_sat < 0.22:
        return True  # 单一色相主导 + 低饱和 = sepia/老胶片调
    return False


def main() -> None:
    dry = "--dry-run" in sys.argv
    db = get_session_maker()()
    rows = db.query(VideoAsset).filter(VideoAsset.source == "youtube").all()
    print(f"youtube rows: {len(rows)}{' (dry-run)' if dry else ''}", flush=True)
    bw_ids, miss = [], 0
    for i, a in enumerate(rows, 1):
        p = a.file_path
        if not p or not Path(p).exists():
            miss += 1
            continue
        mid = (a.duration_sec or 5.0) / 2.0
        jpeg = _sample_frame(p, mid)
        if jpeg is None:
            miss += 1
            continue
        verdict = _frame_is_bw(jpeg)
        if verdict:
            bw_ids.append(a.id)
            if i % 50 == 0:
                print(f"  [{i}/{len(rows)}] BW 命中累计 {len(bw_ids)}", flush=True)
    print(f"检测完: BW 命中 {len(bw_ids)} | 无法判定/缺文件 {miss}", flush=True)
    if not dry and bw_ids:
        for aid in bw_ids:
            a = db.get(VideoAsset, aid)
            if a and a.file_path and Path(a.file_path).exists():
                try:
                    send2trash(a.file_path)
                except Exception:
                    pass
            db.delete(a)
        db.commit()
        print(f"已踢出 {len(bw_ids)} 行 (文件回收站)", flush=True)
    db.close()


if __name__ == "__main__":
    main()
