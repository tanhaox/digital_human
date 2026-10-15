# -*- coding: utf-8 -*-
"""yt_ingest — 油管素材入库管线 (2026-08-27, 素材层 2.0 源侧).

用户需求: 油管成片 → 剪切成片段 → 入库 → 用时调取**无字幕**的相关片段。
(英伟达宣传片这类真实画面实体, Pexels 图库没有 — 实体素材的来源。)

四段:
  split    切点检测(scene 0.20) → 短段合并(≥2.5s) → ffmpeg 切片 → 暂存 JSON
  tag      llama 逐片段打标 (GPU! 内建 GPU-VPN 互斥守卫): 内容描述/画面词/
           实体(英伟达/产品名)/**画面内文字字幕检测**(有无烧录字幕)
  register 逐片段注册 VideoAsset (source=youtube, tags 带实体+no_subtitle 标)
  素材目录: data/materials/youtube/<视频id>/clips/clip_NNN.mp4

用法:
  python sandbox/yt_ingest.py split <video.mp4> <视频id> [--entity 英伟达] [--title "..."]
  python sandbox/yt_ingest.py ocr --all        # OCR 快筛 (CPU, 先于 LLM)
  python sandbox/yt_ingest.py tag --all        # 需 GPU — VPN 必须已断; 脏片自动跳过
  python sandbox/yt_ingest.py register <视频id>
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
STAGE_DIR = ROOT / "data" / "materials" / "youtube"

MIN_CLIP = 2.5   # 短于此并入邻段 (B-roll 可用下限)
MAX_CLIP = 12.0  # 长于此在切点再分


def _stage(video_id: str) -> Path:
    d = STAGE_DIR / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return (r.stdout or "") + (r.stderr or "")


def detect_cuts(video: str, threshold: float = 0.05) -> list[float]:
    """scene 切点 + 叠化簇合并 (2026-09-01 校准): RLR 式叠化转场边界 score 仅
    0.05~0.08 (0.20 全漏 → 跨镜头拼接片); 0.05 档叠化过程 0.2s 间隔连命中,
    相邻 <0.6s 只取簇首。"""
    out = _run([FF, "-i", video, "-vf", f"select='gt(scene,{threshold})',metadata=print",
                "-f", "null", "-"])
    cuts: list[float] = []
    for t in sorted(float(m.group(1)) for m in re.finditer(r"pts_time:([0-9.]+)", out)):
        if cuts and t - cuts[-1] < 0.6:
            continue
        cuts.append(round(t, 2))
    return cuts


def build_clip_ranges(video: str, cuts: list[float], total: float) -> list[tuple[float, float]]:
    """切点 → [start,end] 段表; 短段(<MIN_CLIP)并入前段, 超长段按 MAX_CLIP 均分."""
    bounds = [0.0] + cuts + [total]
    ranges = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
              if bounds[i + 1] - bounds[i] > 0.4]
    # 合并短段 (并入前段 — 段表天然连续, 间隙恒 0, 只看本段长度)
    merged: list[tuple[float, float]] = []
    for s, e in ranges:
        if merged and e - s < MIN_CLIP:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # 超长段均分
    out: list[tuple[float, float]] = []
    for s, e in merged:
        if e - s <= MAX_CLIP:
            out.append((s, e))
            continue
        n = int((e - s) // MAX_CLIP) + 1
        step = (e - s) / n
        out += [(round(s + i * step, 2), round(s + (i + 1) * step, 2)) for i in range(n)]
    return out


def cmd_split(video: str, video_id: str, entity: str, title: str) -> None:
    stage = _stage(video_id)
    dur_out = _run([r"C:\Programs\ffmpeg\bin\ffprobe.exe", "-v", "error",
                    "-show_entries", "format=duration", "-of", "csv=p=0", video])
    total = float(dur_out.strip())
    cuts = detect_cuts(video)
    ranges = build_clip_ranges(video, cuts, total)
    clips_dir = stage / "clips"
    clips_dir.mkdir(exist_ok=True)
    print(f"切点 {len(cuts)} → 片段 {len(ranges)} 段 (总长 {total:.0f}s)")
    manifest = {"video_id": video_id, "source": str(Path(video).resolve()),
                "title": title, "entity": entity, "total_sec": total,
                "clips": []}
    for i, (s, e) in enumerate(ranges):
        out = clips_dir / f"clip_{i:03d}.mp4"
        _run([FF, "-y", "-v", "error", "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
              "-i", video, "-c", "copy", str(out)])
        if out.exists() and out.stat().st_size > 50000:
            manifest["clips"].append({"n": i, "start": round(s, 2), "end": round(e, 2),
                                      "file": str(out.relative_to(ROOT))})
            print(f"  clip_{i:03d}  {s:6.1f}-{e:6.1f}s")
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print(f"→ {stage / 'manifest.json'} ({len(manifest['clips'])} 片段待打标)")


TAG_PROMPT = """你是视频素材打标员。看这个视频片段的一帧。只输出 JSON:
{"desc_zh": "画面内容中文描述(≤20字)",
 "keywords_en": ["英文画面词2-4个"],
 "is_real_footage": true/false,
 "has_burned_text": true/false,
 "text_content": "画面里烧录的文字内容, 无则空"}
is_real_footage=true 当且仅当: 摄像机实拍的真实世界画面 —
 真实人物/街景/城市/自然/建筑/工厂/交通/器物/真实事件现场 (照片感, 有生活质感)。
is_real_footage=false 当画面是任何博主自制/合成/非实拍产物 (2026-09-01 用户令,
 宁可错杀): 地图/地形图/国界/大面积国旗或旗帜画面(特写/飘扬/纯色块国旗也算)/
 国旗叠加/图表/数据可视化/3D地形渲染/CG动画/游戏画面/AI生成感画面/
 剪影+纯色渐变背景/纯色背景+构图元素示意图/商品棚拍展示图/截图/文字卡/
 分层合成画面(视频小窗垫在纯色或图形底上)/黑白老胶片/历史档案资料画面。
has_burned_text=true 当画面有: 字幕/台词文字/大标题/产品名大字/水印文字;
产品上的小logo(芯片上的NVIDIA刻字)不算。注意角落小字水印(Courtesy:/频道名)也算。"""


def ocr_screen(video_id: str, fps_sample: float = 1.0) -> dict:
    """OCR 快筛 (2026-08-30 用户单): 切片后每秒 1 帧 OCR, 有持久文字 → 脏片.

    先于 LLM: CPU 快筛砍掉带字幕/水印切片, 干净的才进 llama 内容识别。
    判定: 任一采样帧存在≥2 处文字框, 或任一帧文字框≥1 且面积占比>1.5%
    (字幕带/角标) → burned。结果写 manifest clips[].ocr_clean。
    Returns: {"clean": n, "dirty": n, "total": n}
    """
    import glob as _glob

    try:
        from app.services.material_ingest_service import _new_ocr
        ocr = _new_ocr()  # GPU CUDA (2026-08-31 用户令: 全系统统一 GPU OCR)
    except ImportError:
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()  # 退 CPU (服务模块不可用时 — CLI 独立场景)
        except ImportError:
            print("[ocr] rapidocr 不可用, 跳过快筛 (pip install rapidocr-onnxruntime)")
            return {"clean": 0, "dirty": 0, "total": 0, "skipped": True}
    except Exception as _cuda_err:
        from rapidocr_onnxruntime import RapidOCR
        print(f"[ocr] CUDA 初始化失败退 CPU: {_cuda_err}")
        ocr = RapidOCR()

    import subprocess as _sp
    import tempfile as _tf

    stage = _stage(video_id)
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    n_clean = n_dirty = 0
    for c in manifest["clips"]:
        if "ocr_clean" in c:
            n_clean += bool(c["ocr_clean"])
            n_dirty += not c["ocr_clean"]
            continue
        clip = ROOT / c["file"]
        dur = c["end"] - c["start"]
        n_frames = max(2, min(5, int(dur * fps_sample)))  # 每秒1帧, 上限5
        tmp = _tf.mkdtemp(prefix="ocr_")
        _sp.run([FF, "-y", "-v", "error", "-i", str(clip)],
                cwd=tmp, capture_output=True)  # noop 占位防引号问题
        frames = []
        for k in range(n_frames):
            t = dur * (k + 0.5) / n_frames
            fp = Path(tmp) / f"f{k}.jpg"
            _sp.run([FF, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(clip),
                     "-frames:v", "1", "-vf", "scale=1280:-2",
                     "-pix_fmt", "yuvj420p", str(fp)],
                    capture_output=True)
            if fp.exists():
                frames.append(str(fp))
        has_text_frames = 0
        for fp in frames:
            try:
                result, _ = ocr(fp)
            except Exception:
                result = None
            if result:
                # 文字框阈值: 高≥9px (1280宽下, 实测 640 会漏 IISS 署名小水印);
                # conf 偶发字符串类型 → float 容错
                def _f(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0
                boxes = [r for r in result if _f(r[2]) >= 0.55
                         and (r[0][3][1] - r[0][1][1]) >= 9]
                if boxes:
                    has_text_frames += 1
            Path(fp).unlink(missing_ok=True)
        c["ocr_clean"] = has_text_frames == 0
        n_clean += c["ocr_clean"]
        n_dirty += not c["ocr_clean"]
    (stage / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ocr] {video_id}: 干净 {n_clean} / 脏 {n_dirty} / 共 {n_clean + n_dirty}")
    return {"clean": n_clean, "dirty": n_dirty, "total": n_clean + n_dirty}


def cmd_tag(video_id: str) -> None:
    """llama 逐片段打标 — GPU 任务, VPN 互斥守卫在 llama 拉起内建.

    两段制 (2026-08-30 用户单): OCR 先筛 (ocr_clean=False 的跳过, CPU 免费),
    只干净片段进 LLM 内容识别 — GPU 用量砍半以上。
    """
    stage = _stage(video_id)
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    from tools.vision import analyze  # _ensure_llama_server 内建 GPU-VPN 守卫

    for c in manifest["clips"]:
        if c.get("tags"):
            continue
        if c.get("ocr_clean") is False:
            c["tags"] = {"desc_zh": "", "keywords_en": [],
                         "has_burned_text": True, "text_content": "[OCR快筛: 带文字]"}
            continue  # 脏片不烧 GPU
        clip = ROOT / c["file"]
        # 768px JPG (2026-08-31): 全分辨率 PNG 让 VLM 吃 ~2900 视觉token/帧 (~27s/条);
        # 缩图后 token÷4 payload÷10。烧录字幕已由 OCR 干净窗把关, VLM 只做内容描述+兜底复查。
        out = _run([FF, "-y", "-v", "error", "-ss", "0.5", "-i", str(clip),
                    "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "3",
                    str(stage / f"frame_{c['n']:03d}.jpg")])
        frame = stage / f"frame_{c['n']:03d}.jpg"
        if not frame.exists():
            continue
        raw = analyze([str(frame)], TAG_PROMPT)
        m = re.search(r"\{.*\}", str(raw), re.S)
        try:
            tags = json.loads(m.group(0))
        except Exception:
            tags = {"desc_zh": "", "keywords_en": [], "has_burned_text": None}
        c["tags"] = tags
        print(f"  clip_{c['n']:03d} {tags.get('desc_zh','')} | 字幕: {tags.get('has_burned_text')}")
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    n_clean = sum(1 for c in manifest["clips"] if not (c.get("tags") or {}).get("has_burned_text"))
    print(f"打标完成: 无字幕片段 {n_clean}/{len(manifest['clips'])}")


def cmd_register(video_id: str) -> None:
    """打标片段 → VideoAsset 入库 (source=youtube, tags 带实体+no_subtitle)."""
    from app.database import init_db, get_session_maker
    from app.models import VideoAsset

    stage = _stage(video_id)
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    entity = manifest.get("entity") or ""
    n = 0
    with get_session_maker()() as db:
        existing = {a.file_path for a in db.query(VideoAsset).all()}
        for c in manifest["clips"]:
            tags_obj = c.get("tags") or {}
            if not tags_obj:
                continue  # 未打标不入库
            fp = str((ROOT / c["file"]).resolve())
            if fp in existing:
                continue
            kw = [k for k in (tags_obj.get("keywords_en") or []) if k]
            tags = list(dict.fromkeys(kw + ([entity] if entity else []) +
                                      (["no_subtitle"] if not tags_obj.get("has_burned_text") else ["burned_text"])))
            db.add(VideoAsset(
                source="youtube", file_path=fp,
                orientation="landscape", width=1920, height=1080,
                duration_sec=round(c["end"] - c["start"], 2),
                description_zh=tags_obj.get("desc_zh") or "",
                description_en=", ".join(kw),
                raw_query=f"youtube:{manifest['title'][:80]}",
                tags=tags,
                source_type="footage", location="foreign",
            ))
            n += 1
        db.commit()
    print(f"入库 {n} 片段 (含实体标签「{entity}」; no_subtitle/burned_text 已标记)")


def cmd_batch(company: str, url: str, max_videos: int, max_duration: int) -> None:
    """官方频道批量: 平铺列出频道视频 → 筛宣传片(≤max_duration) → 下载→切片→排队.

    url: 频道 /videos 页 (原封未动保证) 或 ytsearch:N 搜索串。
    """
    proxy = "http://127.0.0.1:9876"
    ydl = [sys.executable, "-m", "yt_dlp", "--proxy", proxy, "--flat-playlist",
           "--print", "%(id)s|%(duration)s|%(title)s", url]
    r = subprocess.run(ydl, capture_output=True, text=True, timeout=300,
                       cwd=str(ROOT))
    picks = []
    for line in (r.stdout or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[0] or parts[0] == "NA":
            continue
        dur = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0
        if not dur or dur > max_duration or dur < 30:
            continue  # 宣传片口径: 30s~10min
        picks.append((parts[0], dur, parts[2]))
    print(f"[batch {company}] 频道 {len((r.stdout or '').splitlines())} 条 → 宣传片口径 {len(picks)} 条, 取前 {max_videos}")
    for vid, dur, title in picks[:max_videos]:
        stage = STAGE_DIR / vid
        if (stage / "manifest.json").exists():
            print(f"  {vid} 已处理过, 跳过")
            continue
        out = ROOT / "data/materials/youtube" / f"yt_{vid}.mp4"
        print(f"  ↓ {title[:50]} ({dur:.0f}s)")
        d = subprocess.run([sys.executable, "-m", "yt_dlp", "--proxy", proxy,
                            "-f", "bv*[height<=1080]+ba/b[height<=1080]",
                            "--merge-output-format", "mp4", "-o", str(out),
                            f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        if not out.exists():
            print(f"    ✗ 下载失败: {(d.stderr or '')[-120:]}")
            continue
        cmd_split(str(out), vid, entity=company, title=title[:80])
        print(f"    ✂ 切片完成")


def main() -> int:
    ap = argparse.ArgumentParser(description="油管素材入库 (split/tag/register/batch)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_s = sub.add_parser("split")
    p_s.add_argument("video"); p_s.add_argument("video_id")
    p_s.add_argument("--entity", default="")
    p_s.add_argument("--title", default="")
    p_t = sub.add_parser("tag")
    p_t.add_argument("video_id", nargs="?", default=None)
    p_t.add_argument("--all", action="store_true", help="所有未打标 manifest")
    p_o = sub.add_parser("ocr")
    p_o.add_argument("video_id", nargs="?", default=None)
    p_o.add_argument("--all", action="store_true", help="所有未筛 manifest")
    sub.add_parser("register").add_argument("video_id")
    p_b = sub.add_parser("batch")
    p_b.add_argument("--company", required=True)
    p_b.add_argument("--url", required=True)
    p_b.add_argument("--max", type=int, default=10)
    p_b.add_argument("--max-duration", type=int, default=600)
    a = ap.parse_args()
    if a.cmd == "split":
        cmd_split(a.video, a.video_id, a.entity, a.title)
    elif a.cmd == "ocr":
        if getattr(a, "all", False):
            for mf in sorted(STAGE_DIR.glob("*/manifest.json")):
                m = json.loads(mf.read_text(encoding="utf-8"))
                if any("ocr_clean" not in c for c in m.get("clips", [])):
                    ocr_screen(mf.parent.name)
        elif a.video_id:
            ocr_screen(a.video_id)
    elif a.cmd == "tag":
        if a.all:
            for mf in sorted(STAGE_DIR.glob("*/manifest.json")):
                m = json.loads(mf.read_text(encoding="utf-8"))
                if any(not c.get("tags") and c.get("ocr_clean") is not False
                       for c in m.get("clips", [])):
                    print(f"━━ {mf.parent.name} ({m.get('title','')[:40]}) ━━")
                    cmd_tag(mf.parent.name)
        else:
            cmd_tag(a.video_id)
    elif a.cmd == "batch":
        cmd_batch(a.company, a.url, a.max, a.max_duration)
    else:
        cmd_register(a.video_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
