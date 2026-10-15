# -*- coding: utf-8 -*-
"""material_ingest_service — 素材摄入产线 v2 (2026-08-30, 用户三需求:
①UI 推进不再靠对话 ②链接→下载→识别→切片全流程 ③VPN 检测+等待+自动续跑).

六段流水 (2026-08-30 用户纠序: 先筛后切, 脏片段不产生):
  download → probe(抽帧+切点探测) → ocr(整片1fps时间轴) → split(仅干净窗内切片)
  → tag(每片一帧 LLM 内容识别) → register(入库+9维回填)
  先切后筛的浪费: 切片半净半脏 → 整片陪葬; 先筛后切把文字检测变成时间轴,
  切点 ∩ 干净窗 = 零浪费, 且省去脏片占盘。
VPN 门控: download 需代理 ON / tag (GPU-VPN 互斥) 需代理 OFF —
  探针 = 经 9876 代理访问 youtube generate_204; 不满足进 waiting_* 轮询,
  满足自动续; 30min 超时进 paused_* (UI resume 续)。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..database import db_session
from ..models import MaterialIngestJob, VideoAsset

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROXY = "http://127.0.0.1:9876"
VPN_PROBE_URL = "https://www.youtube.com/generate_204"
VPN_POLL_SEC = 5
VPN_WAIT_TIMEOUT_SEC = 30 * 60
STAGE_DIR = ROOT / "data" / "materials" / "youtube"
FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
OCR_FRAME_SCALE = 1280  # v2.2 管道抽帧宽度 (83ms/帧与 720/960 持平, 取最高召回档)

# 阶段序 (resume 从当前 stage 重入)
STAGES = ["download", "probe", "ocr", "split", "tag", "register"]


# ── VPN 探针/门控 ──────────────────────────────────────────────
def vpn_state(timeout: int = 4) -> dict:
    """代理探针: 经 9876 访问 youtube → on/off + 延迟."""
    t0 = time.time()
    handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open(VPN_PROBE_URL, timeout=timeout) as resp:
            ok = resp.status in (200, 204)
    except Exception:
        ok = False
    return {"state": "on" if ok else "off",
            "latency_ms": int((time.time() - t0) * 1000),
            "checked_at": time.strftime("%H:%M:%S")}


def _wait_vpn(db: Session, job: MaterialIngestJob, want_on: bool) -> bool:
    """门控等待: 轮询到目标态 → True; 超时 → False (调用方置 paused)."""
    waiting_stage = f"waiting_vpn_{'on' if want_on else 'off'}"
    job.stage = waiting_stage
    stats = dict(job.stats_json or {})
    stats["wait_until"] = time.strftime("%H:%M:%S", time.localtime(time.time() + VPN_WAIT_TIMEOUT_SEC))
    job.stats_json = stats
    db.commit()
    deadline = time.time() + VPN_WAIT_TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(VPN_POLL_SEC)
        st = vpn_state()
        if (st["state"] == "on") == want_on:
            return True
    return False


# ── 实体字典 ───────────────────────────────────────────────────
def load_entities() -> list[dict]:
    import yaml
    p = ROOT / "config" / "geo_entities.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("entities") or []


def entity_entry(name: str) -> dict | None:
    return next((e for e in load_entities() if e["name"] == name), None)


def stock_count(db: Session, name: str) -> int:
    """库内该实体干净素材数 (tags 含实体名 — Python 层精确匹配, JSON 列 cast 陷阱)."""
    rows = db.query(VideoAsset).all()
    return sum(1 for r in rows
               if name in (r.tags or []) and "no_subtitle" in (r.tags or []))


# ── 摄取核心段 (复用 yt_ingest) ────────────────────────────────
def _yt_dlp() -> str:
    return str(Path(sys.executable))  # python -m yt_dlp


def _download(job: MaterialIngestJob) -> str:
    """yt-dlp 经代理下载 → 返回 mp4 路径."""
    out = STAGE_DIR / f"yt_{job.video_id}.mp4"
    if out.exists():
        return str(out)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--proxy", PROXY,
         "-f", "bv*[height<=1080]+ba/b[height<=1080]",
         "--merge-output-format", "mp4", "-o", str(out),
         f"https://www.youtube.com/watch?v={job.video_id}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    if not out.exists():
        raise RuntimeError(f"下载失败: {(r.stderr or '')[-200:]}")
    return str(out)


def _fetch_info(video_id: str) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--proxy", PROXY, "--skip-download",
         "--print", "%(title)s", f"https://www.youtube.com/watch?v={video_id}"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    return {"title": (r.stdout or "").strip()[:200]}


def _split(job: MaterialIngestJob) -> dict:
    """干净窗切片 (v2.1 用户纠序): 切点 ∩ OCR 干净窗 → 只切干净段."""
    mf = STAGE_DIR / job.video_id / "manifest.json"
    video = str(STAGE_DIR / f"yt_{job.video_id}.mp4")
    tl = json.loads((STAGE_DIR / job.video_id / "timeline.json").read_text(encoding="utf-8"))
    cuts = tl.get("cuts") or []
    windows = tl.get("clean_windows") or []
    total = tl.get("total_sec") or 0.0
    if not windows:
        return {"clips": 0, "note": "全片带文字, 无干净窗"}

    # 候选段 = 切点分段; 无切点(静态机位演讲) → 干净窗内均匀 ~10s 切
    bounds = sorted(set([0.0] + [c for c in cuts if 0 < c < total] + [total]))
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
            if bounds[i + 1] - bounds[i] > 0.4]
    final: list[tuple[float, float]] = []
    for s, e in segs:
        # 找覆盖该段的干净窗, 求交集 (只保留净部分)
        for ws, we in windows:
            ns, ne = max(s, ws), min(e, we)
            if ne - ns >= 4.0:  # 最小可用片段
                final.append((round(ns, 2), round(ne, 2)))

    def _fill_uniform(dest: list, hs: float, he: float) -> None:
        """长镜头内部均匀 ~10s 分块 (同镜头内均分, 不跨切点). ceil: 16s→2×8s 而非 1 块."""
        import math
        n = max(1, math.ceil((he - hs) / 10))
        step = (he - hs) / n
        dest += [(round(hs + i * step, 2), round(hs + (i + 1) * step, 2))
                 for i in range(n)]

    # 洞填补 (2026-09-01 修): 只补 final 未覆盖的窗部分。原版无条件把所有 >4s 窗
    # 均匀再切一遍追加 → 同素材双份 (clip_005≡clip_024 实测) + 均匀切跨镜头拼接
    # (V20260901-5496 起批次 "2-3 素材拼在一片" 根因)。
    covered = sorted(final)
    for ws, we in windows:
        if we - ws < 4.0:
            continue
        cur = ws
        for fs, fe in covered:
            if fe <= ws or fs >= we:
                continue
            if fs - cur >= 4.0:
                _fill_uniform(final, cur, min(fs, we))
            cur = max(cur, fe)
        if we - cur >= 4.0:
            _fill_uniform(final, cur, we)
    final = sorted(set(final))
    # seg∩win 交集段也可能超 10s (切点稀疏的长镜头) — 统一 ceil 均分, 片长封顶 ~10s
    capped: list[tuple[float, float]] = []
    for s, e in final:
        if e - s > 10.0:
            _fill_uniform(capped, s, e)
        else:
            capped.append((s, e))
    # 头尾收缩 (2026-09-01): 叠化转场中段的切点前 0.2~0.5s 已是混合画面 —
    # 尾让 0.25s; 头让 0.3s (对称, 抽验实证片头 0.0~0.6s 残留叠化尾段);
    # 收缩后 <4s 的碎窗丢弃
    covered = sorted({(s + 0.3, e - 0.25) for s, e in set(capped)
                      if e - 0.25 - (s + 0.3) >= 4.0})

    clips_dir = STAGE_DIR / job.video_id / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (s, e) in enumerate(covered):
        out = clips_dir / f"clip_{i:03d}.mp4"
        if not out.exists():
            # 重编码精确切 (2026-09-01 修): -c copy 受 GOP 关键帧对齐约束, 片尾会
            # 多出下镜头若干帧 ("最后几帧突然切其他画面" 用户实测) — veryfast/crf21
            # 实测 2.1s/10s片, 8000 片 4 进程 ~1h, 换精确帧切值得。
            # 输出 seek (-ss 在 -i 后, 2026-09-01 二修): 输入 seek 时音/视频流各自
            # seek 落点可差 0.1~0.3s → 重编码后容器 0/0 但内容错位 (V20260901-3973
            # 音画不同步实测); 输出 seek 从关键帧解码裁剪, A/V 严格同点。
            # -an (2026-09-01 用户令): 素材只要画面 — 草稿挂载本就 volume=0 (声音
            # 归 TTS 轨), 音轨是死重且是音画不同步的载体, 直接不带。
            # +faststart: moov 前置, web 播放器边下边播不起竞态。
            subprocess.run([FF, "-y", "-v", "error", "-i", video,
                            "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                            "-an", "-movflags", "+faststart", str(out)],
                           capture_output=True, timeout=120)
        if out.exists() and out.stat().st_size > 50000:
            clips.append({"n": i, "start": s, "end": e,
                          "file": str(out.relative_to(ROOT)),
                          "ocr_clean": True})  # 出自干净窗, 天然干净
    manifest = {"video_id": job.video_id, "title": job.title or "",
                "entity": job.entity or "", "clips": clips}
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"clips": len(clips)}


def _probe(job: MaterialIngestJob) -> dict:
    """抽帧+切点探测: 场景切点 → timeline.json (OCR 段消费)."""
    video = str(STAGE_DIR / f"yt_{job.video_id}.mp4")
    out = subprocess.run(
        [r"C:\Programs\ffmpeg\bin\ffprobe.exe", "-v", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True, text=True, timeout=60)
    total = float((out.stdout or "0").strip() or 0)
    r = subprocess.run([FF, "-i", video, "-vf",
                        # 0.05 + 簇合并 (2026-09-01 修, 实测校准): RLR 式叠化转场 +
                        # 地图低对比内容的镜头边界 scene score 仅 0.05~0.08, 0.12/0.20
                        # 全漏 (390s 实测 9 切点, 快剪频道应 80~150) → 切片跨镜头拼接。
                        # 0.05 档叠化过程会连续命中 (0.2s 间隔) → 下方 <0.6s 并簇取簇首。
                        "select='gt(scene,0.05)',metadata=print", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    raw_cuts = sorted(float(m.group(1)) for m in
                      re.finditer(r"pts_time:([0-9.]+)", r.stderr or ""))
    cuts: list[float] = []
    for t in raw_cuts:
        if cuts and t - cuts[-1] < 0.6:
            continue  # 同一叠化转场簇, 只取簇首
        cuts.append(round(t, 2))
    d = STAGE_DIR / job.video_id
    d.mkdir(parents=True, exist_ok=True)
    tl_path = d / "timeline.json"
    # merge 写 (2026-09-01): 保留 _ocr 已产出的 clean_windows/dirty_sec_list/sec_flags —
    # 原版覆盖写, 重切批 re-probe 时会把 OCR 结果全抹掉 (OCR 是全链最贵 CPU 段)
    tl: dict = {}
    if tl_path.exists():
        try:
            tl = json.loads(tl_path.read_text(encoding="utf-8"))
        except Exception:
            tl = {}
    tl.update({"total_sec": total, "cuts": cuts})
    tl_path.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    return {"total_sec": round(total, 1), "cuts": len(cuts)}


def _new_ocr():
    """RapidOCR CUDA 工厂 (2026-08-31): DLL 目录注入 + GPU EP + v3 模型路径.

    Windows 坑: Py3.8+ 不继承 PATH → cudnn/cublas 系列必须 add_dll_directory;
    RapidOCR 1.2.3 坑: rec_use_cuda=True 不生效, rec session 静默落 CPU-only
    (det 也可能未真启) → 构造后手动重建双 session 强制 CUDA EP。
    实测 (4090): det 960x960 原生 CUDA 10.3ms/帧; 修复前 rec 纯 CPU 逐行推理,
    缩帧尺寸不提速 (~300ms/帧固定开销) 即此病灶。
    """
    from rapidocr_onnxruntime import RapidOCR
    import rapidocr_onnxruntime as _r
    _models = Path(_r.__file__).parent / "models"
    _nv = ROOT / ".venv/Lib/site-packages/nvidia"
    for sub in ("cudnn/bin", "cublas/bin", "cuda_nvrtc/bin", "cufft/bin", "nvjitlink/bin"):
        d = _nv / sub
        if d.exists():
            import os as _os
            _os.add_dll_directory(str(d))
            _os.environ["PATH"] = str(d) + _os.pathsep + _os.environ.get("PATH", "")
    ocr = RapidOCR(det_use_cuda=True, rec_use_cuda=True,
                   det_model_path=str(_models / "ch_PP-OCRv3_det_infer.onnx"),
                   rec_model_path=str(_models / "ch_PP-OCRv3_rec_infer.onnx"))

    # 强制 CUDA: 只替换包装器内层的原生 session (包装器本身可调用, 不能整个换)
    import onnxruntime as _ort

    def _rebind(wrapper, model_path):
        try:
            s = _ort.InferenceSession(str(model_path), providers=[
                ("CUDAExecutionProvider", {"device_id": 0}),
                "CPUExecutionProvider"])
            if s.get_providers()[0] == "CUDAExecutionProvider":
                wrapper.session = s
        except Exception as _exc:
            logger.warning("[ocr] CUDA session 重建失败留原样: %s", _exc)

    _rebind(ocr.text_detector.infer, _models / "ch_PP-OCRv3_det_infer.onnx")
    _rebind(ocr.text_recognizer.session, _models / "ch_PP-OCRv3_rec_infer.onnx")
    return ocr


def _ocr(job: MaterialIngestJob) -> dict:
    """OCR 时间轴 (v2.2, 2026-08-31 瓶颈重构): 单遍 ffmpeg 管道 fps=1,scale →
    ndarray 直喂 GPU OCR。v2.1 每帧独立 spawn ffmpeg(-ss seek+jpg) 757ms/帧;
    v2.2 管道直喂 + _new_ocr 强制双 session CUDA (rec 此前静默落 CPU)。
    基准: sandbox/_ocr_bench_result.json; 秒号口径与 v2.1 一致 (第k帧=第k秒)。"""
    try:
        ocr = _new_ocr()
    except ImportError:
        return {"error": "rapidocr 未装"}
    except Exception as _exc:
        logger.warning("[ocr] CUDA 初始化失败退 CPU: %s", _exc)
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()

    import numpy as np
    video = Path(STAGE_DIR) / f"yt_{job.video_id}.mp4"
    if not video.exists():
        return {"error": "源文件缺失"}  # 护栏: 不写垃圾 timeline (v2.1 事故根源)
    tl_path = STAGE_DIR / job.video_id / "timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    total = tl.get("total_sec") or 0.0
    if total <= 0:
        return {"error": "total_sec 异常, 疑探测失败"}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    scale = OCR_FRAME_SCALE
    probe = subprocess.run([FF, "-v", "error", "-i", str(video), "-vf",
                            f"fps=1,scale={scale}:-2", "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                           capture_output=True)
    fb = len(probe.stdout)
    if fb == 0 or fb % (scale * 3) != 0:
        return {"error": "帧尺寸探测失败"}
    height = fb // (scale * 3)

    dirty_secs: set[int] = set()
    # fps=3 (2026-09-01 修, 实锤盲区): fps=1 每秒只看首帧, RLR 式"飞入即出"的
    # 动效闪字 (<1s) 两采样帧全错过 (V20260901-4417 第4s STEEL AND CONC 实测:
    # 同管道 sec 165-169 全空, 精确 seek 168.5 有 7% 大字) — 3 帧/秒把盲区缩到
    # 0.33s; 帧号 k → 秒 = k // 3, 窗合并口径不变。
    ocr_fps = 3
    p = subprocess.Popen([FF, "-v", "error", "-i", str(video), "-vf",
                          f"fps={ocr_fps},scale={scale}:-2", "-f", "rawvideo",
                          "-pix_fmt", "bgr24", "pipe:1"], stdout=subprocess.PIPE)
    try:
        for k in range(int(total) * ocr_fps):
            raw = p.stdout.read(fb)
            if len(raw) < fb:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, scale, 3)
            try:
                result, _ = ocr(frame)
            except Exception:
                result = None
            if result:
                for r in result:
                    if _f(r[2]) >= 0.55 and (r[0][3][1] - r[0][1][1]) >= 9:
                        dirty_secs.add(k // ocr_fps)
                        break
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.kill()

    # 干净窗合并 (脏秒即断窗; 连续干净段收窗) — 与 v2.1 同口径
    windows: list[list[float]] = []
    run_start = None
    for sec in range(int(total) + 1):
        if sec in dirty_secs:
            if run_start is not None:
                windows.append([run_start, float(sec)])
                run_start = None
        else:
            if run_start is None:
                run_start = float(sec)
    if run_start is not None:
        windows.append([run_start, total])
    # 内收 0.6s (2026-09-01 修, 方向反转): 原版向外扩 0.5s 把脏秒边缘的字幕半秒
    # 包进"干净窗" (V20260901-4787 窗首/4788 窗尾带字实测) — 脏边应向内让:
    # 0.6 同时覆盖帧采样(秒首 1 帧)的秒中段盲区。4s 下限在 _split 收缩后判, 不碎。
    windows = [[max(0.0, ws + 0.6), min(total, we - 0.6)] for ws, we in windows]
    windows = [[ws, we] for ws, we in windows if we - ws >= 2.0]
    tl["clean_windows"] = windows
    # 逐秒标记保留 (2026-08-31 用户设计要求: 第N秒有无字幕可查, 为切分/复核
    # 提供依据 — 之前只存 len(dirty_secs) 计数, 逐秒信息被扔掉)
    tl["dirty_sec_list"] = sorted(dirty_secs)
    tl["sec_flags"] = "".join("1" if s in dirty_secs else "0"
                              for s in range(int(total) + 1))  # "0110..." 逐秒位图
    tl_path.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    clean_total = sum(we - ws for ws, we in windows)
    return {"clean_windows": len(windows),
            "clean_sec": round(clean_total), "dirty_sec": len(dirty_secs)}


def _tag(job: MaterialIngestJob) -> dict:
    from sandbox.yt_ingest import cmd_tag
    cmd_tag(job.video_id)
    mf = STAGE_DIR / job.video_id / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    tagged = sum(1 for c in m.get("clips", []) if c.get("tags"))
    return {"tagged": tagged}


def _register(db: Session, job: MaterialIngestJob) -> dict:
    """双清入库 (ocr_clean + llm 干净) + 实体标签 + 9 维回填一次跑完."""
    mf = STAGE_DIR / job.video_id / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    ent = job.entity or ""
    existing = {a.file_path for a in db.query(VideoAsset).all()}
    today = f"V{time.strftime('%Y%m%d')}-"
    nums = [int(a.asset_no[-4:]) for a in db.query(VideoAsset)
            .filter(VideoAsset.asset_no.like(today + "%")).all()
            if a.asset_no[-4:].isdigit()]
    seq = max(nums, default=0)
    n = 0
    for c in m.get("clips", []):
        t = c.get("tags") or {}
        # is_real_footage (2026-09-01 用户令, 收敛判定): 只收摄像机实拍的真实世界
        # 画面; 博主自制/合成一律拒 — 地图/地形渲染/国旗叠加/图表/CG/AI生成感/
        # 剪影渐变/商品棚拍/截图/黑白老胶片 (原 is_map/is_bw 并入本判据)
        if not t or not t.get("is_real_footage") or t.get("has_burned_text") \
                or c.get("ocr_clean") is False:
            continue
        if c["end"] - c["start"] < 4.0:
            continue
        fp = str((ROOT / c["file"]).resolve())
        if fp in existing:
            continue
        seq += 1
        kw = [k for k in (t.get("keywords_en") or []) if k]
        tags = list(dict.fromkeys(kw + ([ent] if ent else []) + ["no_subtitle"]))
        db.add(VideoAsset(asset_no=f"{today}{seq:04d}", source="youtube",
                          file_path=fp, orientation="landscape",
                          width=1920, height=1080,
                          duration_sec=round(c["end"] - c["start"], 2),
                          description_zh=t.get("desc_zh") or "",
                          description_en=", ".join(kw),
                          raw_query=f"youtube:{(m.get('title') or '')[:80]}",
                          tags=tags, source_type="footage", location="foreign"))
        n += 1
    db.commit()
    # 词表包自动重建 (2026-09-02): 新素材入库即重建 — 此前钩子只挂在旧打标线,
    # yt 入库线不触发, 词表 stale 在 2026-08-09 (新批 1896 条从未进词表, 实锤)
    try:
        from app.services.director_prompt._vocabulary import rebuild_vocabulary_pack
        rebuild_vocabulary_pack(db)
    except Exception as exc:  # noqa: BLE001 — 重建失败不挡入库
        logger.warning("[ingest] 词表包重建失败: %s", exc)
    return {"registered": n}


def _backfill_dims(db: Session, job: MaterialIngestJob) -> int:
    """新入库片段 9 维回填 (flash, 同批内联跑 — 不再手动 backfill)."""
    rows = (db.query(VideoAsset)
            .filter(VideoAsset.source == "youtube",
                    VideoAsset.file_path.contains(job.video_id))
            .all())
    todo = [a for a in rows if not a.scenes or a.scenes in ("[]", "", "null")]
    if not todo:
        return 0
    from ..services.boost_service import _call, _extract_json
    import yaml as _yaml
    vocab = (_yaml.safe_load((ROOT / "data" / "vocabulary_pack.json")
            .read_text(encoding="utf-8")) or {}).get("dimensions") or {}
    batch, done = 10, 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        items = json.dumps([{"i": j, "desc_zh": a.description_zh or "",
                             "kw": (a.description_en or "")[:80]}
                            for j, a in enumerate(chunk)], ensure_ascii=False)
        prompt = (f"给视频素材补检索维度标签,只输出JSON数组。枚举: scenes={vocab.get('scenes')} "
                  f"shot_types={vocab.get('shot_types')} tone={vocab.get('tone')} "
                  f"motion_level={vocab.get('motion_level')} content_density={vocab.get('content_density')} "
                  f"time_of_day={vocab.get('time_of_day')}。与输入同序: "
                  f'[{{"i":0,"scenes":["科技"],"shot_types":["特写"],"tone":"cool",'
                  f'"motion_level":"slow","content_density":"moderate","time_of_day":"indoor"}}]。清单: {items}')
        try:
            data = _extract_json(_call(prompt, json_mode=True,
                                       max_tokens=6000, temperature=0.2)) or []
        except Exception:
            continue
        by_i = {d.get("i"): d for d in data if isinstance(d, dict)}
        for j, a in enumerate(chunk):
            d = by_i.get(j)
            if not d:
                continue
            # 直接赋 list (2026-09-01 修): 原版 json.dumps 手动转 str 存 JSON 列
            # → 6400+ 行 '["工业"]' 脏格式, VideoAssetOut 序列化 500 (library 翻页炸)
            def _as_list(v):
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except (ValueError, TypeError):
                        return []
                return v if isinstance(v, list) else []
            a.scenes = _as_list(d.get("scenes"))
            a.shot_types = _as_list(d.get("shot_types"))
            a.tone = d.get("tone") or "neutral"
            a.motion_level = d.get("motion_level") or "slow"
            a.content_density = d.get("content_density") or "moderate"
            a.time_of_day = d.get("time_of_day") or "undefined"
            done += 1
        db.commit()
    return done


# ── 任务执行器 ─────────────────────────────────────────────────
def _run_job(job_id: str) -> None:
    with db_session() as db:
        job = db.query(MaterialIngestJob).filter(MaterialIngestJob.id == job_id).first()
        if not job:
            return
        try:
            _advance(db, job)
        except Exception as exc:
            logger.exception("[ingest %s] failed", job_id[:8])
            job.stage = "failed"
            job.error = str(exc)[:500]
            db.commit()


def _advance(db: Session, job: MaterialIngestJob) -> None:
    """从当前 stage 起推进到 done (幂等, 各段可重入)."""
    order = {s: i for i, s in enumerate(STAGES)}
    start = order.get(job.stage, 0) if job.stage in order else 0
    if job.stage.startswith(("waiting", "paused")):
        start = 0 if job.stage.endswith("_on") else 4  # wait_on → 从头; wait_off → 从 tag
    stats = dict(job.stats_json or {})

    # URL → video_id 提前解析 (2026-08-31 流程测试揪出: 解析在下载段内部,
    # VPN 门控先于它执行 → 缓存感知永远拿不到 video_id, 已下载的片也在傻等 VPN)
    if not job.video_id and job.source_url:
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", job.source_url)
        if m:
            job.video_id = m.group(1)
            db.commit()

    for stage in STAGES[start:]:
        # VPN 门控 (缓存感知: 文件已在盘上则下载段免等 VPN — 2026-08-31)
        if stage == "download":
            cached = (job.video_id
                      and (STAGE_DIR / f"yt_{job.video_id}.mp4").exists())
            if not cached and vpn_state()["state"] != "on":
                if not _wait_vpn(db, job, want_on=True):
                    job.stage = "paused_vpn_on"
                    db.commit()
                    return
        if stage == "tag":
            if vpn_state()["state"] != "off":
                if not _wait_vpn(db, job, want_on=False):
                    job.stage = "paused_vpn_off"
                    db.commit()
                    return

        job.stage = {"download": "downloading", "probe": "probing", "ocr": "ocr",
                     "split": "splitting", "tag": "tagging",
                     "register": "registering"}[stage]
        db.commit()

        if stage == "download":
            if not job.video_id:
                raise ValueError(f"无法从 URL 解析视频 ID: {job.source_url}")
            if not job.title:
                try:
                    job.title = _fetch_info(job.video_id).get("title") or ""
                    db.commit()
                except Exception:
                    pass
            _download(job)
        elif stage == "probe":
            stats.update(_probe(job))
        elif stage == "ocr":
            stats.update(_ocr(job))
        elif stage == "split":
            stats.update(_split(job))
        elif stage == "tag":
            stats.update(_tag(job))
        elif stage == "register":
            stats.update(_register(db, job))
            stats["dims_backfilled"] = _backfill_dims(db, job)
        job.stats_json = stats
        db.commit()

    job.stage = "done"
    db.commit()
    logger.info("[ingest %s] done: %s", job.id[:8], stats)


# ── 搜索规划器 (2026-08-30 用户单: 源头干净度是最上游杠杆) ────────
# 领域知识固化进提示词: 官方政府频道/国会/C-SPAN 是 raw 无字幕; 新闻台
# (CNN/BBC/DW/日テレ等) 上传必带 chyron 字幕条。LLM 按"干净源优先"生成
# 搜索词 + 指定官方频道 + 片名避雷词, 下载前预筛掉注定脏的源。

QUERY_PLANNER_PROMPT = """你是 YouTube 素材采购专家。为下面的实体生成"能搜到无字幕干净素材"的搜索方案。只输出 JSON。

# 干净源知识 (核心依据)
- 官方政府/机构频道 (The White House, 首相官邸, Kremlin, UN, 国会官方, C-SPAN, NATO, 白宫档案馆) = raw 无字幕, 最优
- 新闻台上传 (CNN/BBC/FOX/DW/NHK/日テレ/TBS/ABS-CBN/ANC...) = 必带字幕条+台标, 尽量避开
- 有利词: "full speech" "raw" "no commentary" "press conference" 官方口径
- 不利词(片名含则脏): "subtitles" "subtitulado" "字幕" "CC" "hardcoded" "highlights"(新闻剪辑)

# 任务
给实体生成 3 路搜索 (queries) + 若知道其官方频道给出频道 handle (channels, 没有给空) + 片名避雷正则词 (avoid, 除上述通用词外加该实体特有的)

# 输出
{"queries": ["...", "...", "..."], "channels": ["@Kantei...", ...], "avoid": ["新闻台名", ...]}"""

# 通用片名避雷 (静态): 预筛注定脏的源, 不浪费下载
_STATIC_AVOID = re.compile(
    r"subtitl|subtitulado|字幕|hardcoded|\bCC\b|closed.?caption|"
    r"\bCNN\b|\bBBC\b|\bFOX\b|\bMSNBC\b|\bDW News\b|\bAl.?Jazeera\b|"
    r"\bNHK\b|日テレ|テレ朝|TBS.?NEWS|\bABS.?CBN\b|\bANC\b|"
    r"news.?live|breaking", re.I)


def plan_queries(entity: str, etype: str) -> dict:
    """LLM 搜索规划: 干净源导向的 queries + 官方频道 + 避雷词."""
    try:
        from ..services.boost_service import _call, _extract_json
        raw = _call(QUERY_PLANNER_PROMPT + f"\n\n【实体】{entity} ({etype})",
                    json_mode=True, max_tokens=600, temperature=0.3)
        data = _extract_json(raw) or {}
        return {"queries": [q for q in (data.get("queries") or []) if q][:3],
                "channels": [c for c in (data.get("channels") or []) if c][:2],
                "avoid": [a for a in (data.get("avoid") or []) if a][:6]}
    except Exception as exc:
        logger.warning("[ingest] 搜索规划失败(退字典默认): %s", exc)
        return {"queries": [], "channels": [], "avoid": []}


def _title_clean(title: str, extra_avoid: list[str]) -> bool:
    """片名预筛: 避雷词命中 → 弃 (不浪费下载)."""
    if _STATIC_AVOID.search(title or ""):
        return False
    low = (title or "").lower()
    return not any(a.lower() in low for a in extra_avoid)


def _run_entity_batch(parent_id: str) -> None:
    """实体批: 搜索 → 建子任务 → 顺序执行."""
    with db_session() as db:
        parent = db.query(MaterialIngestJob).filter(
            MaterialIngestJob.id == parent_id).first()
        if not parent:
            return
        ent = entity_entry(parent.entity or "")
        custom_search = (parent.stats_json or {}).get("custom_search")
        if not ent and not custom_search:
            parent.stage = "failed"
            parent.error = f"实体字典无此实体: {parent.entity}"
            db.commit()
            return
        max_dur = int((ent or {}).get("max_duration", 3600))
        max_videos = int((ent or {}).get("max_videos", 2))
        etype = (ent or {}).get("type", "person")

        # 🔮 LLM 搜索规划 (2026-08-30): 干净源导向 — 官方频道优先/新闻台避雷
        plan = plan_queries(parent.entity or "", etype)
        queries: list[str] = []
        channels: list[str] = []
        if custom_search:
            queries.append(f"ytsearch{max_videos + 2}:" + custom_search)
        if ent and ent.get("channel"):
            channels.append(f"https://www.youtube.com/{ent['channel']}/videos")
        channels += [f"https://www.youtube.com/{c}/videos" for c in plan["channels"]]
        # 字典默认搜索词兜底 (规划空/全失败时)
        if not queries and not channels:
            base = (ent or {}).get("search") or custom_search or parent.entity
            queries.append(f"ytsearch{max_videos + 2}:" + base)
        else:
            for q in plan["queries"][:2]:
                queries.append(f"ytsearch{max(2, max_videos - 1)}:" + q)
            if ent and not custom_search and channels:
                queries.append(f"ytsearch2:" + (ent.get("search") or parent.entity))

        avoid = plan["avoid"]
        kids: dict[str, str] = {}  # video_id -> title (跨路去重)
        for src in channels + queries:
            if len(kids) >= max_videos + 2:
                break
            r = subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--proxy", PROXY, "--flat-playlist",
                 "--print", "%(id)s|%(duration)s|%(title)s", src],
                cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            for line in (r.stdout or "").splitlines():
                if len(kids) >= max_videos + 2:
                    break
                parts = [p.strip() for p in line.split("|")]
                if len(parts) != 3 or not parts[0] or parts[0] == "NA":
                    continue
                dur = float(parts[1]) if parts[1].replace(".", "").isdigit() else 0
                if not dur or not (30 <= dur <= max_dur):
                    continue
                if parts[0] in kids:
                    continue
                if not _title_clean(parts[2], avoid):  # 📋 片名预筛
                    continue
                kids[parts[0]] = parts[2][:200]

        child_ids = []
        for vid, title in list(kids.items())[:max_videos + 1]:
            kid = MaterialIngestJob(mode="url", entity=parent.entity,
                                    source_url=f"https://www.youtube.com/watch?v={vid}",
                                    video_id=vid, title=title,
                                    stage="queued", parent_id=parent.id)
            db.add(kid)
            db.flush()
            child_ids.append(kid.id)
        parent.stats_json = {"children": len(child_ids),
                             "planned_queries": plan["queries"],
                             "planned_channels": plan["channels"]}
        parent.stage = "done"  # 父任务=调度器, 子任务各自跑
        db.commit()

    for cid in child_ids:
        _run_job(cid)


def start_job(job_id: str, mode: str) -> None:
    t = threading.Thread(
        target=_run_entity_batch if mode == "entity" else _run_job,
        args=(job_id,), daemon=True, name=f"ingest-{job_id[:8]}")
    t.start()
