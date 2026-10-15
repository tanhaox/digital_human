"""Audio router: TTS generation and downloads."""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.services.job_events import _publish

from ..database import db_session, get_db, get_session_maker
from ..models import AudioFile, AudioJob, Episode, Host, Persona, Script, Segment, Voice
from ..schemas import AudioFileOut, AudioJobOut
from ..services.gpu_service_manager import get_gpu_service_manager
from ..services.tts_service import TTSService

router = APIRouter(prefix="/api/audio", tags=["audio"])

logger = logging.getLogger(__name__)

# ── TTS 任务停止 (2026-09-02): 段间软取消 ──
# cancel 端点把 job_id 放入集合; _do_tts 在段间检查点 (情绪标注后 / 每段 progress 回调)
# 抛 _TTSCancelled 中断合成。已生成段 wav 保留, 重新生成走 _skip_batch 断点续传。
# 不强杀 GPU 服务: 进行中的批次 (一次 HTTP 合成调用) 做完后才中断, 前端提示"当前批次完成后中断"。
_cancel_requested: set[str] = set()
_cancel_lock = threading.Lock()


class _TTSCancelled(Exception):
    """用户点了停止按钮 (audio.html), TTS 任务在段间检查点主动中断。"""


def _check_cancel(job_id: str) -> None:
    with _cancel_lock:
        if job_id in _cancel_requested:
            raise _TTSCancelled()


def _cancel_done(job_id: str) -> None:
    with _cancel_lock:
        _cancel_requested.discard(job_id)


def get_tts() -> TTSService:
    from ..config import get_config

    cfg = get_config()
    return TTSService(cfg.defaults)


@router.post("/scripts/{script_id}/generate-audio", response_model=AudioJobOut)
def generate_audio(
    script_id: str,
    voice_id: str | None = None,
    selected_only: bool = True,
    background_tasks: BackgroundTasks = ...,
    db: Session = Depends(get_db),
    tts: TTSService = Depends(get_tts),
):
    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    voice = None
    if voice_id:
        voice = db.query(Voice).filter(Voice.id == voice_id).first()
    # 未显式选音色 → 人物即账号 (2026-08-08): 优先 persona.voice_id (人物页编辑的音色),
    # 其次 host.default_voice_id (老陈默认=大学教授 indextts), 最后 host 下第一条 voice。
    if not voice and script.host_id:
        persona = (
            db.query(Persona).filter(Persona.host_id == script.host_id).first()
        )
        if persona and persona.voice_id:
            voice = db.query(Voice).filter(Voice.id == persona.voice_id).first()
    if not voice and script.host_id:
        host = db.query(Host).filter(Host.id == script.host_id).first()
        if host and host.default_voice_id:
            voice = db.query(Voice).filter(Voice.id == host.default_voice_id).first()
    if not voice and script.host_id:
        voice = db.query(Voice).filter(Voice.host_id == script.host_id).first()

    query = db.query(Segment).filter(Segment.script_id == script_id)
    if selected_only:
        query = query.filter(Segment.selected_for_host == True)
    segments = query.order_by(Segment.host_order).all()

    if not segments:
        raise HTTPException(status_code=400, detail="No segments to synthesize")

    project_root = Path(__file__).resolve().parents[2]
    if script.project_dir:
        output_dir = Path(script.project_dir) / "audio"
    else:
        today = datetime.date.today().isoformat()
        voice_label = voice.name if voice else "default"
        voice_id_short = voice.id[:8] if voice else "default"
        output_dir = Path(r"E:\数字人计划\outputs\audio") / today / voice_label / f"{voice_id_short}_{int(time.time())}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 过期音频清理 (方案A, 2026-08-12) ──
    # 文稿改动 (update_script) 已把旧 completed AudioJob 标记为 stale。
    # 点"生成音频"时: 清空 stale job 的磁盘 wav + 删除 DB 记录 → 强制重新合成,
    # 杜绝 `_skip_batch` 断点续传误复用旧音频。
    stale_jobs = (
        db.query(AudioJob)
        .filter(AudioJob.script_id == script_id, AudioJob.status == "stale")
        .all()
    )
    for stale_job in stale_jobs:
        stale_dir = Path(stale_job.output_dir)
        if stale_dir.exists():
            # 只清 wav/manifest, 不误删目录 (目录可能被其他 job 共享)
            for pat in ("*.wav", "manifest.json", "full_paragraph.wav", ".emo_*.json"):
                try:
                    for p in stale_dir.glob(pat):
                        p.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "generate_audio: 清理 stale job %s 的 %s 失败", stale_job.id[:8], pat,
                        exc_info=True,
                    )
        db.delete(stale_job)  # 连带 audio_files (cascade delete-orphan)
    if stale_jobs:
        logger.info(
            "generate_audio: script %s 清理 %d 个 stale AudioJob, 强制重新生成音频",
            script_id[:8], len(stale_jobs),
        )
        db.commit()

    job = AudioJob(
        script_id=script_id,
        voice_id=voice.id if voice else None,
        output_dir=str(output_dir),
        status="pending",
        total_segments=len(segments),
        completed_segments=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_do_tts, job.id)
    return job


def _do_tts(job_id: str):
    if get_session_maker() is None:
        _publish(job_id, {"type": "tts_error", "error": "Database not initialized"})
        return
    with db_session() as db:
        try:
            job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
            if not job:
                _publish(job_id, {"type": "tts_error", "error": "Audio job not found"})
                return

            script = db.query(Script).filter(Script.id == job.script_id).first()
            voice = None
            if job.voice_id:
                voice = db.query(Voice).filter(Voice.id == job.voice_id).first()

            query = db.query(Segment).filter(Segment.script_id == job.script_id)
            query = query.filter(Segment.selected_for_host == True)
            segments = query.order_by(Segment.host_order).all()

            # 段数据快照 (2026-08-25): 必须在下方 status=running 的 db.commit() 之前提取。
            # commit 令 ORM expire; TTS 长任务 (GPU 冷启动+逐段合成) 期间若文稿被保存,
            # _reparse_segments 删旧建新行, 旧对象 refresh 不到行 → "Segment has been
            # deleted" → job 卡 failed 且 0 段完成 (audio 线实测)。之后全程只用纯数据。
            seg_pairs: list[tuple[str, str]] = [(s.id, s.text) for s in segments]

            # 内容指纹 (2026-09-07): 供完成时"合成期间稿子被改"检测。不能用
            # script.updated_at — 本函数自身的情绪标注写回 (emotion_annotations
            # + db.commit) 会触发 onupdate 刷新 updated_at, 时间戳比对每次必真,
            # 保存稿→生成→必现误判 stale (0907 实测)。段文本 hash 才是 TTS 真正
            # 消费的内容, 免疫一切非文本写入。
            import hashlib as _hl_seg
            seg_hash = _hl_seg.sha1(
                "\n".join(t for _, t in seg_pairs).encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]

            if not segments:
                job.status = "failed"
                job.error_message = "No segments to synthesize"
                db.commit()
                _publish(job_id, {"type": "tts_error", "error": "No segments to synthesize"})
                return

            job.status = "running"
            job.total_segments = len(segments)
            db.commit()

            tts = get_tts()

            # ── 情绪标注 (2026-08-25 从爆品改造拆出, 移至生成音频时刻) ──
            # 单一事实源: 用与 TTS 完全同源的文本现场标注 → 不存在"改稿后旧标注
            # 错配"窗口 (此前靠改稿清空+异步重跑兜)。失败落整篇兜底: 新闻线
            # surprised/3 (2026-08-27 惊讶打底定稿), 读书线 calm/2 (2026-09-03)。
            # 耗时 ~10-30s, 藏在 TTS 引擎冷启动 (~2min) 里零感知。
            emotion_segments: list | None = None
            from ..services.pinyin_fix import tts_adapt, scan_pinyin_hits
            from ..services.boost_service import annotate_emotions, _parse_emotion_annotations
            clean_text = "\n".join(t for _, t in seg_pairs)
            # 词表命中提示 (2026-09-03): 扫干净文本, 命中即推 SSE。
            hits = scan_pinyin_hits(clean_text)
            if hits:
                _publish(job_id, {"type": "tts_service",
                                  "message": f"纠音词表命中 {len(hits)} 处: {'、'.join(hits)}"})
            tts_text = tts_adapt(clean_text)
            # 2.5 通道 (0910): 情绪来自情感参考音频, P5 标注层整层跳过。
            # 0912 修复: 此前此处 raise RuntimeError("skip_p5") 把 2.5 任务整单炸死
            # (0 段 completed, 0910 投产后产线 job 链从未真正跑通); 真正的跳过
            # 逻辑在下方 annotate_emotions 的 _skip_p5 分支, 此处只提前算标志。
            _skip_p5 = voice is not None and getattr(voice, "backend", "") == "indextts25"
            if not _skip_p5:
                _publish(job_id, {"type": "tts_service", "message": "情绪标注中…"})
            # 读书线分流 (2026-09-03): 拆书稿 prompt_template 含 "book" → 读书版情绪;
            # 2026-09-07 双人物: "laotan-book" → 老谭读书版 (confident+serious 打底,
            # P5_SPAN_PROMPT_BOOK_LAOTAN), 其余 book (静读书) calm+confident 不动,
            # 惊讶打底铁律只属新闻线。
            _tpl_name = (script.prompt_template if script else "") or ""
            if "laotan-book" in _tpl_name:
                style = "book_laotan"
            elif "book" in _tpl_name:
                style = "book"
            else:
                style = "news"
            fallback = ({"emotion": "surprised", "strength": 3, "text": tts_text}
                        if style == "news"
                        else ({"emotion": "confident", "strength": 3, "text": tts_text}
                              if style == "book_laotan"
                              else {"emotion": "calm", "strength": 2, "text": tts_text}))
            try:
                persona_name = "老谭"
                if script and script.host:
                    # 0907 去硬编码: 优先 persona 名 (人物即账号), host 名兜底
                    try:
                        _p = db.query(Persona).filter(
                            Persona.host_id == script.host_id).first()
                        if _p:
                            persona_name = _p.stamp_name or _p.name
                    except Exception:
                        pass
                    if persona_name == "老谭":
                        persona_name = (
                            getattr(script.host, "stamp_name", None) or script.host.name
                        ) or "老谭"
                raw_anno = (None if _skip_p5 else
                            annotate_emotions(tts_text, persona_name, style=style))
                parsed = _parse_emotion_annotations(raw_anno) if raw_anno else None
                if parsed:
                    emotion_segments = parsed
                    script.emotion_annotations = raw_anno  # 写回供前端/诊断显示
                    db.commit()
                    _publish(job_id, {"type": "tts_service",
                                      "message": f"情绪标注完成: {len(parsed)} 段"})
                else:
                    emotion_segments = [dict(fallback)]
                    _publish(job_id, {"type": "tts_service",
                                      "message": ("2.5 情感参考通道, 跳过情绪标注" if _skip_p5
                                                  else "情绪标注解析失败, 整篇打底兜底")})
            except Exception as exc:
                logger.warning("[tts %s] emotion annotate failed, fallback: %s",
                               job_id[:8], exc)
                emotion_segments = [dict(fallback)]
                _publish(job_id, {"type": "tts_service",
                                  "message": "情绪标注失败, 整篇打底兜底"})

            # 停止检查点 1/2 (2026-09-02): 情绪标注 (~10-30s) 期间收到的停止请求在此生效
            _check_cancel(job_id)

            def _progress(completed: int, total: int, text: str | None, audio_file: AudioFile | None = None) -> None:
                # 停止检查点 2/2: 每段 progress 回调 (合成循环的段间边界)
                _check_cancel(job_id)
                job.completed_segments = completed
                db.commit()
                event: dict = {
                    "type": "tts_progress",
                    "completed": completed,
                    "total": total,
                    "text": text,
                }
                if audio_file:
                    db.add(audio_file)
                    db.commit()
                    db.refresh(audio_file)
                    event["audio_file"] = {
                        "id": audio_file.id,
                        "filename": audio_file.filename,
                        "duration": audio_file.duration,
                        "segment_id": audio_file.segment_id,
                    }
                _publish(job_id, event)

            # ── GPU 服务托管: 排队 + 按需拉起 TTS 后端 + 空闲自动关停腾显存 ──
            from ..config import get_config

            backend = (voice.backend if voice and voice.backend else get_config().defaults.backend)

            def _svc_notify(message: str) -> None:
                _publish(job_id, {"type": "tts_service", "message": message})

            manager = get_gpu_service_manager()
            with manager.session(backend, status_callback=_svc_notify):
                # 六拍模块表 (0917 模块总线): script_id → episode → module_json —
                # TTS 模块墙断包/大气口/manifest module_id 的结构真相源; 查不到
                # (非拆书线 script) 传 None, 行为不变。
                _ep = db.query(Episode).filter(
                    Episode.script_id == job.script_id).first()
                _mods = list(_ep.module_json) if (
                    _ep and isinstance(_ep.module_json, list) and _ep.module_json) else None
                # 0930 重构: 节拍配置数据化 — Host.config_json.pacing (persona.get_pacing,
                # 老谭懒种 LAOTAN_PACING)。拍点/气口/变速/分区映射全从 pacing 解析;
                # 无 pacing 人格 (静读书/新闻线) = None → 全部保持无节拍现状。
                # 在 tts.generate 之前解析 (ORM expiry: 生成后对象过期不可再读)
                from app.services.book_service.persona import get_pacing as _get_pacing
                from app.services import audio_post
                _job_script = db.query(Script).filter(Script.id == job.script_id).first()
                _pacing = _get_pacing(db, getattr(_job_script, "host", None)) if _job_script else None
                _beat_profile = (_pacing or {}).get("beats") if _pacing else None
                _zone_map = [r for r in ((_pacing or {}).get("zone_map") or [])
                             if isinstance(r, dict) and r.get("zone")]
                _zbreath = None
                if _pacing and _mods:
                    _bt = (_pacing.get("breath") or {})
                    _zbreath = {m.get("idx"): dict(_bt.get(audio_post.zone_of_module(str(m.get("name") or ""), _zone_map), {}))
                                for m in _mods if m.get("idx") is not None}
                result = tts.generate(
                    job, seg_pairs, voice, progress_callback=_progress,
                    zone_breath=_zbreath,
                    beat_profile=_beat_profile,
                    emotion_annotations=emotion_segments,  # 现场标注 (同源文本), 不读库
                    status_callback=lambda m, lv: _publish(
                        job_id, {"type": "tts_service", "message": m}),
                    module_json=_mods,
                    # 1004 段落墙: 稿件原文 (段落/节标结构的唯一载体) 传入 —
                    # 断包墙+段落气口都从它派生 (合成文本无空行, 结构在 Segment
                    # 拼接时已丢)。非拆书线无 script 照旧。
                    script_text=(getattr(_job_script, "script_text", None) if _job_script else None),
                )
                # ── 0930 后处理栈外置 (app/services/audio_post.py, 逻辑逐字节照抄) ──
                # 顺序铁律: 语速筛(重合成) → 五分区节拍器(模块级) → 锚定协调(行级,
                # 用户锚最高意志后跑后赢); 每段只在自身有变更时落盘。
                _od = Path(job.output_dir)
                from app.services.speech_anchor import read_anchor as _read_anchor
                _anchors = _read_anchor(voice) if voice else {}
                try:
                    _fixed = audio_post.slow_line_filter(result, _od, voice, backend, _publish, job_id)
                    if _fixed:
                        audio_post.persist_manifest(result, _od)
                        logger.info("[tts %s] 语速筛: %d 行重合成", job_id[:8], _fixed)
                except Exception as exc:
                    logger.warning("[tts %s] 行级语速筛失败 (不阻断): %s", job_id[:8], exc)
                try:
                    _zadj = (audio_post.zone_tempo(result, _od, _mods, _pacing, _zone_map,
                                                   _publish, job_id)
                             if (_pacing and _mods) else 0)
                    if _zadj:
                        audio_post.persist_manifest(result, _od)
                        logger.info("[tts %s] 五分区节拍器: %d 包 atempo 调档", job_id[:8], _zadj)
                except Exception:
                    logger.exception("[tts %s] 五分区节拍器失败 (不阻断)", job_id[:8])
                try:
                    _harm = (audio_post.anchor_harmonize(result, _od, _anchors, _publish, job_id)
                             if _anchors else 0)
                    if _harm:
                        audio_post.persist_manifest(result, _od)
                        logger.info("[tts %s] 锚定协调: %d 行 atempo 拉回锚", job_id[:8], _harm)
                except Exception:
                    logger.exception("[tts %s] 批量锚定协调失败 (不阻断)", job_id[:8])
            # ── 底稿 sidecar (2026-09-10 双轨改造): 字幕层读底稿真实形 ──
            # (GPT-6/百分之三十六), TTS 适配 (GPT六/注音) 只进合成侧。
            # 行结构与 manifest 同源同切分 (_split_line_indices), 逐行 1:1。
            # 0913 包化: 块产物 display 行 = **按包分组**的底稿行 (与 manifest 条目
            # 1:1, jy 字幕按段序对齐才不串位); 行分组直接取 manifest line_indices
            # (单一事实源, 不在应用层复刻聚包规则)。
            try:
                from scripts.tts_lib.lines import _split_line_indices
                _disp_lines = _split_line_indices(clean_text)
                _mf = result.get("manifest") or {}
                _entries = _mf.get("segments") or []
                if _entries and _entries[0].get("chunk"):
                    _grouped = []
                    for _e in _entries:
                        _idxs = _e.get("line_indices") or [_e.get("index", 0)]
                        _grouped.append("\n".join(_disp_lines[i] for i in _idxs
                                                  if 0 <= i < len(_disp_lines)))
                    (Path(job.output_dir) / "display.json").write_text(
                        json.dumps({"lines": _grouped}, ensure_ascii=False),
                        encoding="utf-8")
                else:
                    (Path(job.output_dir) / "display.json").write_text(
                        json.dumps({"lines": _disp_lines}, ensure_ascii=False),
                        encoding="utf-8")
            except Exception:
                logger.warning("[tts %s] display.json 写入失败 (字幕回退 manifest 文)",
                               job_id[:8], exc_info=True)

            # audio_files rows are already committed one-by-one inside _progress,
            # so no add_all here — a second add would double-insert (P0-1 related).

            for af in result["audio_files"]:
                if af.segment_id and af.duration:
                    seg = db.query(Segment).filter(Segment.id == af.segment_id).first()
                    if seg:
                        seg.estimated_duration = af.duration

            # ── Save combined paragraph audio as AudioFile ──
            combined_info = result.get("combined_file")
            combined_audio_file = None
            if combined_info and combined_info.get("file_path"):
                combined_audio_file = AudioFile(
                    audio_job_id=job.id,
                    segment_id=None,
                    filename=combined_info["file"],
                    file_path=combined_info["file_path"],
                    duration=combined_info.get("duration"),
                    sample_rate=combined_info.get("sample_rate"),
                )
                db.add(combined_audio_file)

            # ── TTS 期间稿子被改 → 产物基于旧快照, 落 stale 而非 completed (2026-09-07) ──
            # 段快照 (上方 seg_pairs) 防 ORM 崩溃, 但也意味着 correct/保存编辑与 TTS
            # 并发时, 合成的永远是启动时的旧稿 (script 7fae947f 实锤: correct 07:18
            # 改稿, 07:22-24 仍落旧观点 wav)。stale 由下次「生成音频」清盘重合成。
            # 检测用段文本 hash (与快照同 query 条件), 不用 updated_at — 见上方
            # seg_hash 注释 (情绪写回会刷新它, 时间戳比对每次必真误判)。
            final_status = "completed"
            _notice = ""
            _fresh_texts = (
                db.query(Segment.text)
                .filter(
                    Segment.script_id == job.script_id,
                    Segment.selected_for_host == True,
                )
                .order_by(Segment.host_order)
                .all()
            )
            _fresh_hash = _hl_seg.sha1(
                "\n".join(r[0] for r in _fresh_texts).encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]
            if _fresh_hash != seg_hash:
                final_status = "stale"
                _notice = ("⚠ 合成期间文稿已被修改 — 本次音频基于旧稿, 已标记过期; "
                           "请再次点「生成音频」基于新稿重合成")
                logger.warning(
                    "tts: script %s 在 job %s 合成期间段文本变更 (hash %s → %s), 产物落 stale",
                    job.script_id[:8], job_id[:8], seg_hash, _fresh_hash,
                )
            job.status = final_status
            job.completed_segments = len(segments)
            db.commit()

            output_dir = Path(job.output_dir)
            done_event: dict = {"type": "tts_done", "job_id": job.id, "output_dir": str(output_dir)}
            if _notice:
                done_event["stale"] = True
                done_event["notice"] = _notice
            if combined_audio_file:
                done_event["combined_audio"] = {
                    "id": combined_audio_file.id,
                    "filename": combined_audio_file.filename,
                    "duration": combined_audio_file.duration,
                }
            _publish(job_id, done_event)
        except _TTSCancelled:
            try:
                job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
                if job:
                    job.status = "cancelled"
                    job.error_message = "用户停止 — 已生成音频保留，重新生成将从断点继续"
                    db.commit()
            except Exception:
                logger.exception("[tts %s] cancelled 终态落库失败 — 任务将留在 running, 待启动清理兜底", job_id[:8])
            _publish(job_id, {"type": "tts_cancelled", "job_id": job_id})
        except Exception as exc:
            try:
                job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
                if job:
                    job.status = "failed"
                    # P0-1 方案 1: 失败不删盘 → 已生成 wav 保留, 可重试续传
                    job.error_message = f"{exc}（已生成的音频已保留，重新生成将从断点继续）"
                    db.commit()
            except Exception:
                logger.exception("[tts %s] failed 终态落库失败 — 任务将留在 running, 待启动清理兜底", job_id[:8])
            _publish(job_id, {"type": "tts_error", "error": str(exc)})
        finally:
            _cancel_done(job_id)


@router.get("/jobs", response_model=list[AudioJobOut])
def list_audio_jobs(
    script_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """查询音频任务，支持按 script_id / status 过滤."""
    query = db.query(AudioJob)
    if script_id:
        query = query.filter(AudioJob.script_id == script_id)
    if status:
        query = query.filter(AudioJob.status == status)
    return query.order_by(AudioJob.created_at.desc()).limit(limit).all()


@router.get("/jobs/{job_id}", response_model=AudioJobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Audio job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=AudioJobOut)
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """请求停止 TTS 任务 (段间软取消, 已生成段保留断点续传).

    首次点击: pending/running → cancelling, 合成线程在下个检查点中断。
    二次点击 (仍 cancelling): 强制落 cancelled — 兜底线程已死 (服务重启/HTTP 卡死)
    的僵尸 cancelling, 让前端立即复位。
    """
    job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Audio job not found")
    if job.status in ("completed", "failed", "cancelled"):
        return job  # 已终态, 幂等返回
    with _cancel_lock:
        _cancel_requested.add(job_id)
    if job.status == "cancelling":
        job.status = "cancelled"
        job.error_message = "用户停止（强制）— 已生成音频保留，重新生成将从断点继续"
        db.commit()
        db.refresh(job)
        _publish(job_id, {"type": "tts_cancelled", "job_id": job_id})
    else:
        job.status = "cancelling"
        db.commit()
        db.refresh(job)
        _publish(job_id, {"type": "tts_cancelling", "message": "已请求停止 — 当前批次完成后中断"})
    return job


@router.get("/jobs/{job_id}/files", response_model=list[AudioFileOut])
def list_job_files(job_id: str, db: Session = Depends(get_db)):
    """0912 逐字稿接线: 每行音频带对应段文本 + 回听嫌疑标记 —
    人耳复核 (回听) 需要逐字稿当参照物, 机器 ASR 校验的未解决行标红给人工。"""
    from app.models import Segment

    files = db.query(AudioFile).filter(AudioFile.audio_job_id == job_id).order_by(AudioFile.filename).all()
    job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
    # 回听报告的嫌疑/未解决行 (verify_report.json, 0912 落盘)
    flags: dict[str, str] = {}
    if job and job.output_dir:
        try:
            vr = json.loads((Path(job.output_dir) / "verify_report.json").read_text(encoding="utf-8"))
            for u in vr.get("unresolved") or []:
                fn = u.get("file")
                if fn:
                    flags[fn] = "unresolved"
            for s in vr.get("suspect_lines") or []:
                fn = s.get("file") if isinstance(s, dict) else None
                if fn and fn not in flags:
                    flags[fn] = "suspect"
            # 0913 语速地板: slow_lines → ⚠语速 徽标 (5.3字/s 用户令)
            for s in vr.get("slow_lines") or []:
                fn = s.get("file") if isinstance(s, dict) else None
                if fn and fn not in flags:
                    flags[fn] = "slow"
            # 0929 徽标锚化: 5.3 是新闻腔地板 — 说书腔 (锚1.86) 全线被误标 ⚠语速.
            # 音色一旦有锚: 5.3 老线整体作废 (同音色未标锚 zone 沿用 5.3 无意义),
            # 有同 zone 锚的行带内不亮 / 带外标 anchor_off; 无锚音色保持旧行为.
            try:
                from app.services.speech_anchor import measure_rate, read_anchor, zone_of
                _v = db.query(Voice).filter(Voice.id == job.voice_id).first()
                _anc = read_anchor(_v) if _v else {}
                if _anc:
                    for fn in [k for k, v in flags.items() if v == "slow"]:
                        flags.pop(fn)
                    _m2 = json.loads((Path(job.output_dir) / "manifest.json").read_text(encoding="utf-8"))
                    _off = 0
                    for e in (_m2.get("segments") or []):
                        fn = e.get("file")
                        txt = str(e.get("text") or "")
                        _a = _anc.get(zone_of(_off))
                        if fn and _a and _a.get("rate"):
                            _r = measure_rate(txt, float(e.get("duration") or 0))
                            if _r is not None and abs(_r / float(_a["rate"]) - 1) > 0.10:
                                flags.setdefault(fn, "anchor_off")
                        _off += len(txt)
            except Exception:
                pass
        except Exception:
            pass
    seg_texts = {s.id: s.text for s in db.query(Segment).filter(
        Segment.id.in_([f.segment_id for f in files if f.segment_id] or [""])).all()}
    # 0913 包化: 块产物的显示文本 = manifest 整包文本 (块跨多行); 老行级产物无此
    # 映射自然回退 segment 单行文本
    _mf_texts: dict[str, str] = {}
    try:
        if job and job.output_dir:
            _m = json.loads((Path(job.output_dir) / "manifest.json").read_text(encoding="utf-8"))
            _mf_texts = {e.get("file"): e.get("text") for e in _m.get("segments") or []
                         if e.get("file") and e.get("chunk")}
    except Exception:
        pass
    out = []
    for f in files:
        d = AudioFileOut.model_validate(f)
        d.text = _mf_texts.get(f.filename) or seg_texts.get(f.segment_id)
        d.verify_flag = flags.get(f.filename)
        out.append(d)
    return out


@router.get("/files/{file_id}/download")
def download_file(file_id: str, db: Session = Depends(get_db)):
    af = db.query(AudioFile).filter(AudioFile.id == file_id).first()
    if not af:
        raise HTTPException(status_code=404, detail="Audio file not found")
    path = Path(af.file_path)
    if not path.exists():
        raise HTTPException(status_code= 404, detail="File not found on disk")
    return FileResponse(path, media_type="audio/wav", filename=af.filename)


def _redistribute_lines(new_lines: list[str], n_slots: int) -> list[str]:
    """n_slots 个段槽无损装下 new_lines: 行数多时末槽吸收余行 (\\n 连), 行数少时
    尾槽置空; 返回长度恒等于 n_slots。

    0917 根治: 旧同步按位置 1:1 — 行数变了 (用户重分行 7→10) 就错位覆盖+丢行
    (033 实锤: 后 3 行直接从 DB 消失)。段数不能动: line_index 全局序是 manifest
    line_indices/显示分组/断点续传的地基, 插删行级联太深; 下一轮 produce 从
    script_text 重建段自然归位。
    """
    if n_slots <= 0:
        return []
    if len(new_lines) >= n_slots:
        return new_lines[:n_slots - 1] + ["\n".join(new_lines[n_slots - 1:])]
    return new_lines + [""] * (n_slots - len(new_lines))


def _sync_chunk_edit(
    db: Session, seg: Segment, chunk_lines: list, new_text: str,
    manifest_path: Path | None, filename: str | None,
) -> None:
    """改字四处同步 (0917 根治, 033 实锤驱动): ① manifest text/inference_text —
    页面显示/✎编辑基准/剪映字幕层全读 manifest, 不回写则改字后页面原文永旧;
    ② Script/Episode script_text 整块替换 — 旧版逐行 splice, 稿件无该行时静默
    no-op (导演层永远拿旧文) 且 splice 会把新旧行混成重复段落; ③ Segment 无损
    重排 (见 _redistribute_lines)。不 commit — 调用方负责。
    """
    from scripts.tts_lib.text import _tts_text
    new_lines = [l.strip() for l in new_text.splitlines() if l.strip()]
    # 尾部空槽是 _redistribute_lines 收缩时的产物, 不是稿件行 — 块拼接只认非空行
    # (含空行拼出的块在稿件里永远失配, 会退化到行级兜底丢新增行)
    old_lines = [l for l in ((s2.text or "").strip() for s2 in chunk_lines) if l]
    old_block = "\n".join(old_lines)

    # ① manifest 回写 (chunk 真相文本)
    if manifest_path and filename:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            for e in m.get("segments") or []:
                if e.get("file") == filename:
                    e["text"] = new_text
                    e["inference_text"] = _tts_text(new_text)
            manifest_path.write_text(
                json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("[reroll] manifest 改字回写失败: %s", exc)

    # ② 稿件侧整块替换 (Script=导演层/再产源, Episode=书稿)
    sc = db.query(Script).filter(Script.id == seg.script_id).first()
    be = db.query(Episode).filter(Episode.script_id == seg.script_id).first()
    replaced = False
    for holder in (sc, be):
        if holder and holder.script_text and old_block in holder.script_text:
            holder.script_text = holder.script_text.replace(old_block, new_text, 1)
            replaced = True
    if not replaced:
        # 兜底: 块对不上 (稿件被重生成过/此前已被旧版 splice 写花) — 能配对的行
        # 各自替换, 并留痕 (旧版此处静默 no-op, 稿件与语音静默分叉实锤)
        _n_hit = 0
        for old_line, want in zip(old_lines, new_lines):
            if old_line and want != old_line:
                for holder in (sc, be):
                    if holder and holder.script_text and old_line in holder.script_text:
                        holder.script_text = holder.script_text.replace(old_line, want, 1)
                        _n_hit += 1
        if not _n_hit:
            logger.warning(
                "[reroll] 改字同步: 稿件中找不到原块 (可能已被重生成), 稿件未更新 — "
                "音频/manifest 已是新文, 下轮 produce 需以 manifest 为准")

    # ③ Segment 无损重排
    for s2, want in zip(chunk_lines, _redistribute_lines(new_lines, len(chunk_lines))):
        if want != (s2.text or "").strip():
            logger.info("[reroll] 改字同步: %s → %s", (s2.text or "")[:20], want[:20])
            s2.text = want


@router.post("/files/{file_id}/anchor")
def anchor_line(file_id: str, db: Session = Depends(get_db)):
    """登记语速基准行 (0929 用户令: 019 舒服含气口 → 整集向它对齐).

    实测该行含气口字/s (汉字÷wav时长, 气口不剥离), 按行首偏移 zone
    (hook<60/transition<150/cruise≥150) 写入音色 config_json.params.speech_anchors.
    重roll/改稿重生成的同 zone 行由协调器自动校正回锚 (app/services/speech_anchor.py).
    """
    af = db.query(AudioFile).filter(AudioFile.id == file_id).first()
    if not af or not af.segment_id:
        raise HTTPException(404, detail="AudioFile 不存在或无对应段")
    job = db.query(AudioJob).filter(AudioJob.id == af.audio_job_id).first()
    seg = db.query(Segment).filter(Segment.id == af.segment_id).first()
    if not (job and seg):
        raise HTTPException(404, detail="job/segment 不存在")
    from app.services.speech_anchor import measure_rate, write_anchor, zone_of
    # 文本源 = manifest 块文本 (一个 wav 念整块多行 — 0913 包化; 用 segment 单行
    # 文本会错配块时长, 019 首登 1.86 实为 87字/19.4s=4.48 的错值实锤)
    _txt = seg.text or ""
    try:
        _m3 = json.loads((Path(job.output_dir) / "manifest.json").read_text(encoding="utf-8"))
        for e in (_m3.get("segments") or []):
            if e.get("file") == af.filename and e.get("text"):
                _txt = str(e["text"])
                break
    except Exception:
        pass
    rate = measure_rate(_txt, af.duration)
    if rate is None:
        raise HTTPException(409, detail="该行汉字过少/时长异常, 不适合做基准")
    _segs_all = (db.query(Segment)
                 .filter(Segment.script_id == seg.script_id)
                 .order_by(Segment.line_index).all())
    off = sum(len(s.text or "") for s in _segs_all if s.line_index < seg.line_index)
    zone = zone_of(off)
    voice = db.query(Voice).filter(Voice.id == job.voice_id).first()
    if not voice:
        raise HTTPException(404, detail="音色不存在")
    write_anchor(voice, zone, rate, f"行{seg.line_index:03d}")
    db.commit()
    return {"zone": zone, "rate": round(rate, 2), "voice": voice.name,
            "line": seg.line_index, "duration": af.duration}


@router.post("/files/{file_id}/reroll")
def reroll_line(file_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    """单行重roll + 自动回听复检 (0912 用户令: 页面闭环 — 人耳发现问题→重生成→再回听→入库).

    payload: {"annotate": {"炸": "ZHA4"}} 可选 — 字级拼音标注 (人工终审回流);
             缺省干净重roll (采样重roll, 语气词/插字/吞字类大概率消失)。
    流程: 重合成(产线同参+loudnorm) → 时长回写(DB+manifest) → whisper 复检
    (语气词/插字/发音) → verify_report.json 更新 → full_paragraph 重拼。
    """
    import shutil as _sh
    import subprocess as _sp
    import wave as _wave

    af = db.query(AudioFile).filter(AudioFile.id == file_id).first()
    if not af or not af.segment_id:
        raise HTTPException(404, detail="AudioFile 不存在或无对应段")
    job = db.query(AudioJob).filter(AudioJob.id == af.audio_job_id).first()
    seg = db.query(Segment).filter(Segment.id == af.segment_id).first()
    if not job or not seg:
        raise HTTPException(404, detail="job/segment 不存在")
    wav = Path(af.file_path)
    if not wav.exists():
        raise HTTPException(404, detail=f"音频文件缺失: {wav}")

    # 0913 包化: AudioFile 可能对应一个 chunk (2-4行) — 文本/行跨从 manifest 取,
    # 行文本按当前 Segment 实时重组 (改字同步后包文本随之正确); 老行级产物无
    # line_indices 字段自然回退单行, 全兼容。
    # 0917 根治: chunk 文本真相 = manifest text (改字四处同步后回写 manifest;
    # 页面显示/✎编辑基准/字幕层全读 manifest) — 旧版基准从 Segment 重组, 改字
    # 只写 Segment → 编辑基准与显示永旧 (033 实锤)。line_indices 全失配时回退
    # 单锚段 (旧版回退"全部段" — 位置映射同步会把整稿写坏)。
    _chunk_lines: list[Segment] = [seg]
    _mf_chunk_text: str | None = None
    try:
        _mf = json.loads((Path(job.output_dir) / "manifest.json").read_text(encoding="utf-8"))
        for _e in _mf.get("segments") or []:
            if _e.get("file") == af.filename and _e.get("chunk"):
                _idxs = _e.get("line_indices") or [_e.get("index")]
                _hit = [s2 for s2 in (db.query(Segment)
                                      .filter(Segment.script_id == seg.script_id)
                                      .order_by(Segment.line_index).all())
                        if s2.line_index in _idxs]
                if _hit:
                    _chunk_lines = _hit
                _mf_chunk_text = (_e.get("text") or "").strip() or None
                break
    except Exception:
        pass

    text = (_mf_chunk_text
            or "\n".join((s2.text or "").strip() for s2 in _chunk_lines).strip()
            or (seg.text or ""))
    _plain_chunk = text  # 未加注音的包文本 (改字比较基准)
    _ann_map = {ch: pin for ch, pin in ((payload or {}).get("annotate") or {}).items() if ch in text}
    for ch, pin in _ann_map.items():
        text = text.replace(ch, f"<{ch}|{pin}>", 1)
    # ✏改字 (0912): 行级文字消歧 — 断句/语义歧义类病 (如"核辐射死人"被逐字拖读)
    # 改字后 Segment/Script/书稿 三处同步, 防下次 produce 误重建
    # (0913 包化: 包级改字 — 比较基准是整包文本; 同步按"包内哪行变了"定位)
    _new_text = str((payload or {}).get("text") or "").strip()
    _text_changed = bool(_new_text) and _new_text != _plain_chunk and 2 <= len(_new_text) <= 400
    if _text_changed:
        from app.services.script_parser import clean_episode_script as _ces
        _new_text = _ces(_new_text)[0].strip() or _new_text
        text = _new_text

    voice = db.query(Voice).filter(Voice.id == job.voice_id).first()
    vp = ((voice.config_json or {}).get("params") or {}) if voice and voice.config_json else {}
    # 0912 行级语速覆盖: 加速重roll (认领问句等天然拖的行) — 0.5-2.0 合法区间
    _df = (payload or {}).get("duration_factor")
    if not isinstance(_df, (int, float)) or not (0.5 <= _df <= 2.0):
        _df = None
    params = {
        "duration_factor": _df or vp.get("duration_factor", 1.16),
        "emo_alpha": vp.get("emo_alpha", 0.8),
        "emo_audio_prompt": (vp.get("emo_audio_prompt")
                             or getattr(get_tts().defaults, "indextts25_emo_ref", "")),
        "do_sample": True, "top_p": 0.8, "top_k": 30, "temperature": 0.8,
        "max_text_tokens_per_segment": 300,  # 0913 用户令 120→300 (服务端自分段接缝劣化在案; 单段解码~30s截断悬崖由客户端包上限兜)
    }
    # 行首字符偏移 (区参/地板窗口共用): 本行之前所有段文本长度累计
    _line_off = 0
    try:
        _segs_all = (db.query(Segment)
                     .filter(Segment.script_id == seg.script_id)
                     .order_by(Segment.line_index).all())
        _line_off = sum(len(s2.text or "") for s2 in _segs_all
                        if s2.line_index < seg.line_index)
    except Exception:
        pass
    # 0913 三区同参修复: 批合成 speed_zones 按行首字符偏移取 df (tts_lib/lines.py),
    # reroll 此前恒用巡航速 — 锤区/过渡区行重roll后变慢 (job 2adcf9b7 行003 实锤
    # 3.55字/s vs 邻行5.4-6.1)。与批侧共用 zone_duration_factor (0913 斜坡化:
    # 区内线性过渡, 边界无缝); 用户显式 duration_factor 覆盖时不落区。
    if _df is None:
        try:
            from scripts.tts_lib.lines import zone_duration_factor
            _zones = vp.get("speed_zones") or [
                {"upto_chars": 60, "duration_factor": 0.95},
                {"upto_chars": 150, "duration_factor": 1.05},
            ]
            params["duration_factor"] = zone_duration_factor(
                _zones, _line_off, float(params["duration_factor"]))
        except Exception:
            pass  # 落区失败退回巡航速, 不阻断重roll
    # 0913 ⏩累加改版 (用户令: 固定0.95与重roll没区别): accel=在当前df基础上×0.9
    # (逐次快~10%, 下限0.5); 当前df优先读行级 sidecar (上次reroll实际用的值),
    # 无 sidecar = 批合成落区值。任何 reroll 成功后写 sidecar 作下次累加锚。
    _df_sidecar = wav.with_suffix(".df.json")
    if (payload or {}).get("accel"):
        try:
            _df_prev = float(json.loads(_df_sidecar.read_text(encoding="utf-8"))["df"])
        except Exception:
            _df_prev = float(params["duration_factor"])
        params["duration_factor"] = max(round(_df_prev * 0.9, 3), 0.5)
    from scripts.tts_lib.orchestrator import _synthesize_single
    from app.services.pinyin_fix import tts_adapt
    # 0914→0917 根治: -Xs- 停顿标记在 reroll 被念成"负一S"。0914 只修了行尾
    # (剥标记+尾垫); 句中标记 (编辑按钮光标处插入, 033 包实锤) 三层失守 —
    # _pause_after 判非行尾 → 剥离不执行 → 标记直达引擎直念。语义升级:
    # -Xs- = 合成硬边界, _split_by_pause_marks 任意位置切片, 每片独立解码+
    # 归一, 片间(含片尾)垫静音拼接 — reroll 不走 _apply_breath_pauses,
    # 停顿全部在此垫。
    from scripts.tts_lib.text import _split_by_pause_marks
    _reroll_pieces = _split_by_pause_marks(text) or [(text, 0.0)]

    bak = wav.with_suffix(".reroll_bak")
    _sh.copy2(wav, bak)
    try:
        # 0914 修: reroll 包 GPU 服务托管 — 批量生成有 manager.session() 自动拉起
        # TTS 后端, 单行 reroll 裸调 _synthesize_single, 后端不在线直接连接拒绝。
        # 统一走托管: 排队 → VPN 查杀 → 确保服务在线 → 执行 → 空闲关停。
        from ..services.gpu_service_manager import get_gpu_service_manager as _gsm
        _backend = (voice.backend if voice else "indextts25")
        _mgr = _gsm()
        with _mgr.session(_backend):
            def _synth_piece(_ptext: str, _pout: Path) -> None:
                _synthesize_single(
                    text=tts_adapt(_ptext), output_path=_pout,
                    backend=_backend,
                    voice_id=(voice.name if voice else ""),
                    reference_audio=None, reference_text="", base_url_fish="", base_url_f5="",
                    base_url_indextts=(voice.base_url_indextts if voice and voice.base_url_indextts else "http://127.0.0.1:7866"),
                    master_audio=Path(voice.reference_audio_path) if voice and voice.reference_audio_path else None,
                    master_text=(voice.master_text or "") if voice else "", master_style="calm",
                    params=params)

            # 1001 修: reroll 的换名也过 Win 锁护盾 — 页面 <audio> 流式拉 wav 持读
            # 句柄, 裸 replace WinError 5 (055.norm.wav->055.wav 实锤); 同 0921
            # tempo 路径教义: 浏览器按块拉完即放, 短重试必过
            def _swap_in(_src: Path, _dst: Path) -> None:
                import os as _os
                import time as _time
                for _i in range(12):
                    try:
                        _os.replace(_src, _dst)
                        return
                    except PermissionError:
                        if _i == 11:
                            raise
                        _time.sleep(0.25)

            def _loudnorm(_pw: Path) -> None:
                _tmp = _pw.with_suffix(".norm.wav")
                _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(_pw),
                         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(_tmp)], check=True)
                _swap_in(_tmp, _pw)

            if len(_reroll_pieces) == 1:
                _ptext, _ppause = _reroll_pieces[0]
                _synth_piece(_ptext, wav)
                _loudnorm(wav)
                # 气口垫静音 (归一之后 — 静音地板不被 loudnorm 碰); 0914 行为保持
                if _ppause > 0:
                    _tmp2 = wav.with_suffix(".pad.wav")
                    _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                             "-af", f"apad=pad_dur={_ppause}", str(_tmp2)],
                            check=True, timeout=60)
                    _swap_in(_tmp2, wav)
            else:
                # 多片: 每片独立解码+归一, 片间(含片尾)垫静音拼接 — 静音在归一
                # 之后垫入, 不被 loudnorm 碰 (同 0914 教义)
                from scripts.tts_lib.audio import _concat_wavs_with_pauses
                _piece_wavs = []
                for _j, (_ptext, _ppause) in enumerate(_reroll_pieces):
                    _pw = wav.with_suffix(f".p{_j:02d}.wav")
                    _synth_piece(_ptext, _pw)
                    _loudnorm(_pw)
                    _piece_wavs.append((_pw, _ppause))
                _concat_wavs_with_pauses(_piece_wavs, wav)
                for _pw, _ in _piece_wavs:
                    _pw.unlink(missing_ok=True)
    except Exception as exc:
        _sh.copy2(bak, wav)
        bak.unlink(missing_ok=True)
        raise HTTPException(500, detail=f"重roll失败已回滚: {exc}")

    with _wave.open(str(wav), "rb") as w:
        dur = round(w.getnframes() / w.getframerate(), 3)
    af.duration = dur
    out_dir = Path(job.output_dir)
    mpath = out_dir / "manifest.json"
    try:
        m = json.loads(mpath.read_text(encoding="utf-8"))
        for s in m.get("segments", []):
            if s.get("file") == af.filename:
                s["duration"] = dur
        mpath.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("[reroll %s] manifest 时长回写失败 — 后续加速/对齐将基于旧时长", job.id[:8])
    # 0913 累加加速锚: 记录本行实际使用 df (下次 ⏩ 在此基础上再 ×0.9)
    try:
        _df_sidecar.write_text(
            json.dumps({"df": params["duration_factor"]}), encoding="utf-8")
    except Exception:
        logger.exception("[reroll %s] .df.json 加速锚点写失败 — 下次⏩将基于旧df计算", job.id[:8])
    # 0921 新 take = 变速旋钮清零: reroll 换了 take, 旧 accel_bak/tempo 锚全部作废
    # (不清零的话 ⟲还原 会复活已废弃的旧 take)
    wav.with_suffix(".tempo.json").unlink(missing_ok=True)
    wav.with_suffix(".accel_bak.wav").unlink(missing_ok=True)

    # 0929 语速锚定协调: 新 take 实测 vs 同 zone 锚 — 带内不动 / 帽内 atempo 拉回 / 超帽报 far
    # (在 0921 清锚之后: 协调器现场重建 accel_bak/tempo, 与 ⏩/⟲ 无缝接力)
    _anchor_verdict = None
    try:
        from app.services.speech_anchor import harmonize, read_anchor, zone_of
        _az = read_anchor(voice).get(zone_of(_line_off))
        if _az and _az.get("rate"):
            _anchor_verdict = harmonize(wav, _plain_chunk, dur, float(_az["rate"]))
            if _anchor_verdict.get("changed"):
                dur = _anchor_verdict["duration"]
                af.duration = dur
                try:
                    m2 = json.loads(mpath.read_text(encoding="utf-8"))
                    for s3 in m2.get("segments", []):
                        if s3.get("file") == af.filename:
                            s3["duration"] = dur
                    mpath.write_text(json.dumps(m2, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    logger.exception("[reroll %s] 协调后 manifest 回写失败", job.id[:8])
    except Exception:
        logger.exception("[reroll %s] 锚定协调失败 (不阻断)", job.id[:8])

    # whisper 复检本行 (语气词/插字/发音) — 不通过自动再roll一次
    verdict = {"filler": None, "insert": None, "diff": None, "slow": None}
    if _anchor_verdict:
        verdict["anchor"] = _anchor_verdict
    try:
        from app.services.tts_verify import (
            _whisper_transcribe, _diff_readings, _FILLER_CHARS, _INSERT_WORDS,
            SPEED_FLOOR,
        )
        heard = _whisper_transcribe(wav)
        # 0917: 停顿标记是控制符不是念白 — 音频里(正确地)没有它, 基准文本不剥
        # 会 diff 误报 ("-","1","s" 当疑点字); 正则与切片器同源 (text.py)
        from scripts.tts_lib.text import _PAUSE_MARK_ANY_RE as _PMARK
        clean = _PMARK.sub("", _plain_chunk).replace("<", "").replace(">", "").replace("|", "")
        verdict["filler"] = [c for c in _FILLER_CHARS if c in heard and c not in clean]
        verdict["insert"] = [w2 for w2 in _INSERT_WORDS if w2 in heard and w2 not in clean]
        verdict["diff"] = [d["char"] for d in (_diff_readings(clean, heard) or [])][:4]
    except Exception as exc:
        verdict = {"error": str(exc)[:80]}
    # 0913 语速地板 (首分钟窗口): 重roll 结果即时体检 — 窗口内低于地板 toast 亮警;
    # 正文自然方差 (气口/拖腔) 不报警
    try:
        from app.services.tts_verify import SPEED_FLOOR as _SF, SPEED_FLOOR_WINDOW_CHARS as _SW
        import re as _re_mod
        from scripts.tts_lib.text import _PAUSE_MARK_ANY_RE as _PMARK
        _n = len(_re_mod.sub(r"\s", "", _PMARK.sub("", _plain_chunk)))
        if _n >= 4 and dur > 0.3 and _line_off < _SW and _n / dur < _SF:
            verdict["slow"] = round(_n / dur, 2)
    except Exception:
        logger.exception("[reroll %s] 语速地板体检失败 — verdict.slow 缺失", job.id[:8])

    # verify_report 更新: 本行移出 unresolved, 记入 manual_fixed
    try:
        vr_path = out_dir / "verify_report.json"
        vr = json.loads(vr_path.read_text(encoding="utf-8"))
        vr["unresolved"] = [u for u in vr.get("unresolved", []) if u.get("file") != af.filename]
        vr.setdefault("manual_fixed", []).append(
            {"file": af.filename, "duration": dur, "verdict": verdict})
        vr_path.write_text(json.dumps(vr, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        logger.exception("[reroll %s] verify_report 更新失败 — 回听判决未落盘", job.id[:8])

    # 1010 治理栈单包过站 (幂等): reroll 的新包旧版不切头尾也不垫 (043 尾 0.13
    # 实锤, 接缝裸奔) — 切头尾留 0.03 + 垫 max(0.3 地板, -Xs- 标记值);
    # 段落/模块档位上下文不在手, 由下次 🔄重拼/批重跑的 manifest 档位修正。
    # 放 whisper 复检之后: 垫的静音不进复检与语速计算。
    try:
        from scripts.tts_lib.lines import _apply_breath_pauses
        m3 = json.loads(mpath.read_text(encoding="utf-8"))
        _segs3 = [s3 for s3 in m3.get("segments", []) if s3.get("file") == af.filename]
        _apply_breath_pauses(out_dir, None, set(), _segs3, 0.0, 0.0)
        if _segs3 and _segs3[0].get("duration"):
            dur = float(_segs3[0]["duration"])
            af.duration = dur
        mpath.write_text(json.dumps(m3, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("[reroll %s] 治理栈过站失败 — 包头尾未治理 (重拼时会补)", job.id[:8])

    # full_paragraph 重拼
    try:
        from scripts.tts_client import _concat_wavs_with_ffmpeg
        _concat_wavs_with_ffmpeg(sorted(out_dir.glob("[0-9][0-9][0-9].wav")),
                                 out_dir / "full_paragraph.wav")
    except Exception:
        pass

    db.commit()
    bak.unlink(missing_ok=True)
    ok = not any([verdict.get("filler"), verdict.get("insert")])
    # 改字落库: Segment + Script + 书稿 三处同步
    # (0914 修: 旧版 `and ok` 把改字同步绑在 ASR 回听判决上 — 回听有杂音(语气词/
    # 插字)就不同步, 用户改的字丢了(音频用新字但稿面仍旧字, 字幕全错)。
    # 改字是用户的明确意图, 合成成功(走到这行=wav已产出)就该持久化; ok 只管
    # 音频要不要再 roll, 不该管文字要不要存。)
    if _text_changed:
        try:
            # 0917 根治: 旧版按位置 1:1 映射 Segment + 逐行 splice 稿件 — 行数变了
            # 就错位覆盖+丢行 (033 实锤), 稿件找不到行时静默 no-op (导演层永旧文);
            # manifest text 从不回写 (页面/编辑器/字幕层永旧文)。见 _sync_chunk_edit。
            _sync_chunk_edit(db, seg, _chunk_lines, _new_text,
                             Path(job.output_dir) / "manifest.json", af.filename)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[reroll] 改字同步失败: %s", exc)
    # 自动沉淀 (0912 用户令: 注音确定→先加字典→重跑 一条龙): 注音字符在原稿中
    # 连续成词 (≥2字) → 词表热增 (免重启即时生效); 孤立单字不进词表 (词级恒定原则)
    dict_added = None
    if _ann_map and ok:
        try:
            _plain = _plain_chunk
            _best, _i0 = "", -1
            i = 0
            while i < len(_plain):
                if _plain[i] in _ann_map:
                    j = i
                    while j < len(_plain) and _plain[j] in _ann_map:
                        j += 1
                    if j - i > len(_best):
                        _best, _i0 = _plain[i:j], i
                    i = j
                else:
                    i += 1
            if len(_best) >= 2:
                from app.services.pinyin_fix import add_rule
                _marked_word = "".join(f"<{c}|{_ann_map[c]}>" for c in _best)
                dict_added = _best if add_rule(_best, _marked_word) else None
        except Exception as exc:
            logger.warning("[reroll] 词表沉淀失败: %s", exc)
    return {"ok": ok, "file": af.filename, "duration": dur, "verdict": verdict,
            "dict_added": dict_added, "df": params["duration_factor"]}



@router.post("/files/{file_id}/tempo")
def tempo_line(file_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    """单行技术变速 (0921): atempo 变速不变调。

    根因: duration_factor 是 IndexTTS2.5 的 API 字段, 产线 TTS2 (7862) 服务端
    pydantic 直接丢弃 — df 调速全线哑参数 (job 58fe2615 027 行 7 连点实测
    越点越慢 +19%; 000 行 df 0.95→0.692 时长纹丝不动)。此端点不碰引擎:
    首次变速前存原始 take (accel_bak), 之后每次从原始按累计倍率一次成型 —
    小步多点和一步到位等价, 无累积伪影; 停顿静音一并按比例压缩。

    payload: {"op": "faster"(x1/0.95) / "slower"(x0.95) / "reset"(还原)}
    倍率域 [1.0, 1.5]; 回到 1.0 自动还原原始文件并清锚。
    """
    import os as _os
    import shutil as _sh
    import subprocess as _sp
    import time as _time
    import wave as _wave

    def _swap_in(_src: Path) -> None:
        # 0921 Win 锁坑: 页面 <audio> 流式拉 wav (206 Range) 期间服务端
        # FileResponse 持读句柄, 而 os.replace 需 DELETE 共享 (CPython open
        # 不设 FILE_SHARE_DELETE) → WinError 5。浏览器按块拉完即放, 短重试
        # 必过; 前端已配合变速前 pause+释放全部播放器 (双保险)。
        for _i in range(12):
            try:
                _os.replace(_src, wav)
                return
            except PermissionError:
                if _i == 11:
                    raise HTTPException(
                        409, detail="音频被播放器占用 (Windows 文件锁) — 请暂停播放后重试")
                _time.sleep(0.25)

    op = (payload or {}).get("op") or "faster"
    if op not in ("faster", "slower", "reset"):
        raise HTTPException(400, detail=f"未知 op: {op}")
    af = db.query(AudioFile).filter(AudioFile.id == file_id).first()
    if not af or not af.segment_id:
        raise HTTPException(404, detail="AudioFile 不存在或无对应段")
    job = db.query(AudioJob).filter(AudioJob.id == af.audio_job_id).first()
    if not job:
        raise HTTPException(404, detail="job 不存在")
    wav = Path(af.file_path)
    if not wav.exists():
        raise HTTPException(404, detail=f"音频文件缺失: {wav}")

    sidecar = wav.with_suffix(".tempo.json")
    bak = wav.with_suffix(".accel_bak.wav")
    try:
        factor = float(json.loads(sidecar.read_text(encoding="utf-8"))["factor"])
    except Exception:
        factor = 1.0

    STEP, FMAX = 1.0 / 0.95, 1.5
    capped = False
    if op == "reset":
        factor = 1.0
    elif op == "faster":
        factor = round(factor * STEP, 4)
        if factor >= FMAX:
            factor, capped = FMAX, True
    else:
        factor = max(round(factor / STEP, 4), 1.0)

    # 0929 还原条件收紧: 只有显式 reset 或减速回底才还原 —
    # 协调器可能写入 <1.0 的初始 factor (快行减速校正), faster 从那里起步
    # 连点几步仍 <1.0, 旧条件会误判"已还原"直接复活原始 take
    if factor <= 1.0 and op != "faster":
        # 还原 (显式 reset 或减速回底): 原始 take 复位 + 清锚
        # (restore 同样走 _swap_in — copy 直写被占文件同样 WinError 5)
        if bak.exists():
            _rst = wav.with_suffix(".restore.tmp.wav")
            _sh.copy2(bak, _rst)
            _swap_in(_rst)
            bak.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
    else:
        if not bak.exists():
            _sh.copy2(wav, bak)
        _tmp = wav.with_suffix(".atempo.tmp.wav")
        _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(bak),
                 "-af", f"atempo={factor:.4f}", str(_tmp)],
                check=True, timeout=120)
        _swap_in(_tmp)
        sidecar.write_text(json.dumps({"factor": factor}), encoding="utf-8")

    with _wave.open(str(wav), "rb") as w:
        dur = round(w.getnframes() / w.getframerate(), 3)
    af.duration = dur

    # manifest 时长回写 + 语速读数 (与 verdict.slow 同口径: 字数剥停顿标记/空白,
    # 时长含停顿静音) + full_paragraph 重拼
    out_dir = Path(job.output_dir)
    _n = 0
    try:
        mpath = out_dir / "manifest.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))
        for s in m.get("segments", []):
            if s.get("file") == af.filename:
                s["duration"] = dur
                try:
                    import re as _re_mod
                    from scripts.tts_lib.text import _PAUSE_MARK_ANY_RE as _PMARK
                    _n = len(_re_mod.sub(r"\s", "", _PMARK.sub("", s.get("text") or "")))
                except Exception:
                    pass
        mpath.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("[tempo %s] manifest 回写失败 — 时长/语速显示将基于旧值", job.id[:8])
    try:
        from scripts.tts_client import _concat_wavs_with_ffmpeg
        _concat_wavs_with_ffmpeg(sorted(out_dir.glob("[0-9][0-9][0-9].wav")),
                                 out_dir / "full_paragraph.wav")
    except Exception:
        pass
    db.commit()
    return {"ok": True, "file": af.filename, "op": op, "factor": factor,
            "duration": dur, "capped": capped,
            "cps": round(_n / dur, 2) if _n and dur > 0 else None}


@router.post("/jobs/{job_id}/rebuild-combined")
def rebuild_combined(job_id: str, db: Session = Depends(get_db)):
    """完整段落音频重拼 (0912): 段级 reroll/修复后从当前段 wav 重建 full_paragraph,
    并回写 combined AudioFile 时长。"""
    import wave as _wave

    job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
    if not job:
        raise HTTPException(404, detail="job 不存在")
    out_dir = Path(job.output_dir)
    wavs = sorted(out_dir.glob("[0-9][0-9][0-9].wav"))
    if not wavs:
        raise HTTPException(400, detail="无段音频可拼")
    # 1010 治理栈全量过站 (幂等 — 用户令"对齐时机"): 改过的包(reroll/改字/变速)
    # 与老包统一 切头尾留0.03 + 按 manifest 档位垫 max(0.3, 已存档位, -Xs-标记);
    # 重跑恒定 (切头尾必然切掉上次垫的纯零), 从机制上消灭批 resume 双垫累积。
    _gov_n = 0
    try:
        from scripts.tts_lib.lines import _apply_breath_pauses
        mpath = out_dir / "manifest.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))
        segs = [s for s in m.get("segments", []) if s.get("file") != "full_paragraph.wav"]
        _apply_breath_pauses(out_dir, None, set(), segs, 0.0, 0.0)
        mpath.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        _dmap = {s.get("file"): s.get("duration") for s in segs}
        for af2 in db.query(AudioFile).filter(AudioFile.audio_job_id == job_id).all():
            if af2.filename in _dmap and _dmap[af2.filename]:
                af2.duration = float(_dmap[af2.filename])
        _gov_n = len(segs)
        db.commit()
    except Exception:
        logger.exception("[rebuild %s] 治理栈过站失败 — 直接拼装 (包未治理)", job_id[:8])
    from scripts.tts_client import _concat_wavs_with_ffmpeg

    combined = out_dir / "full_paragraph.wav"
    _concat_wavs_with_ffmpeg(wavs, combined)
    with _wave.open(str(combined), "rb") as w:
        dur = round(w.getnframes() / w.getframerate(), 3)
    af = (db.query(AudioFile)
          .filter(AudioFile.audio_job_id == job_id, AudioFile.segment_id.is_(None),
                  AudioFile.filename == "full_paragraph.wav").first())
    if af:
        af.duration = dur
        db.commit()
    return {"ok": True, "segments": len(wavs), "duration": dur, "governed": _gov_n}


@router.post("/files/{file_id}/mute")
def mute_line(file_id: str, db: Session = Depends(get_db)):
    """行屏蔽 (0912 用户令: 听到就想让它消失的行): 本单移出播放+大段,
    并反勾选对应文稿段 (selected_for_host=False) — 未来重生成也不再出镜;
    恢复 = 文稿区重新勾选该段 (复用既有段落选择机制)。wav 改名 .muted 留底。
    """
    import shutil as _sh

    af = db.query(AudioFile).filter(AudioFile.id == file_id).first()
    if not af or not af.segment_id:
        raise HTTPException(404, detail="AudioFile 不存在或无对应段")
    job = db.query(AudioJob).filter(AudioJob.id == af.audio_job_id).first()
    if not job:
        raise HTTPException(404, detail="job 不存在")
    wav = Path(af.file_path)
    if wav.exists():
        wav.rename(wav.with_suffix(".muted"))  # 留底可救 (回收式)
    seg = db.query(Segment).filter(Segment.id == af.segment_id).first()
    line = seg.text if seg else None
    if seg:
        seg.selected_for_host = False  # 文稿侧反勾选 — 重生成不再合成
    db.delete(af)
    db.commit()

    out_dir = Path(job.output_dir)
    try:
        mp = out_dir / "manifest.json"
        m = json.loads(mp.read_text(encoding="utf-8"))
        m["segments"] = [s for s in m.get("segments", []) if s.get("file") != af.filename]
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        from scripts.tts_client import _concat_wavs_with_ffmpeg
        wavs = sorted(out_dir.glob("[0-9][0-9][0-9].wav"))
        _concat_wavs_with_ffmpeg(wavs, out_dir / "full_paragraph.wav")
    except Exception as exc:
        logger.warning("[mute] manifest/大段重建失败: %s", exc)
    return {"ok": True, "file": af.filename, "muted_line": (line or "")[:40]}


@router.get("/jobs/{job_id}/manifest")
def download_manifest(job_id: str, db: Session = Depends(get_db)):
    job = db.query(AudioJob).filter(AudioJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Audio job not found")
    manifest_path = Path(job.output_dir) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found")
    return FileResponse(manifest_path, media_type="application/json", filename="manifest.json")


@router.post("/scripts/{script_id}/replace-char")
def replace_char(
    script_id: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    """错别字音频替换 (2026-08-11): 把含生僻字的段改字后重做 TTS.

    背景: indextts 不认识生僻字(如"昇"), 遇到就读错/乱读, 污染整段音频.
    方案: 找到含原字的 segments → text 替换 → 单段重做 TTS → 替换 wav → 重合成.

    payload: {"from_char": "昇", "to_char": "升"}
    """
    from scripts import tts_client  # noqa: F401
    from ..services.tts_service import TTSService

    from_char = (payload.get("from_char") or "").strip()
    to_char = (payload.get("to_char") or "").strip()
    if not from_char or not to_char:
        raise HTTPException(status_code=400, detail="from_char/to_char 必填")

    script = db.query(Script).filter(Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    # 1. 找含原字的 segments
    segments = db.query(Segment).filter(
        Segment.script_id == script_id,
        Segment.text.contains(from_char),
    ).all()
    if not segments:
        return {"ok": True, "replaced": 0, "msg": f"未找到含'{from_char}'的段"}

    # 2. 找到该 script 的 audio_job (最新 completed)
    job = (
        db.query(AudioJob)
        .filter(AudioJob.script_id == script_id, AudioJob.status == "completed")
        .order_by(AudioJob.created_at.desc())
        .first()
    )
    if not job:
        return {"ok": False, "msg": "未找到已完成音频任务"}

    voice = db.query(Voice).filter(Voice.id == job.voice_id).first()
    output_dir = Path(job.output_dir)

    # 3. 确保 TTS 引擎 (indextts) 在线, 逐段重做 + 替换 wav
    from ..services.gpu_service_manager import get_gpu_service_manager

    manager = get_gpu_service_manager()
    replaced = 0
    # 词表校正 (2026-09-03): 此路径直调 synthesize_lines 绕过了 tts_service 的
    # apply_pinyin_marks 注入 — 改错字重合成时词表词 (铟/昇) 会退回错读, 补上。
    # 标注只进 TTS 输入, DB 稿件存干净文本。
    from ..services.pinyin_fix import tts_adapt

    with manager.session(voice.backend if voice else "auto"):
        for seg in segments:
            seg.text = seg.text.replace(from_char, to_char)
            db.commit()
            new_text = tts_adapt(seg.text)

            temp_dir = output_dir / f"replace_{seg.id[:8]}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                tts_client.synthesize_lines(
                    text=new_text,
                    output_dir=temp_dir,
                    backend=voice.backend if voice else "auto",
                    voice_id=voice.name if voice else "default",
                    reference_audio=Path(voice.reference_audio_path) if voice and voice.reference_audio_path else None,
                    reference_text=voice.reference_text or "" if voice else "",
                    base_url_fish=voice.base_url_fish if voice else None,
                    base_url_f5=voice.base_url_f5 if voice else None,
                    base_url_indextts=voice.base_url_indextts if voice else None,
                    master_audio=Path(voice.master_audio_path) if voice and voice.master_audio_path else None,
                    master_text=voice.master_text or "" if voice else "",
                    params=(
                        json.loads(voice.config_json) if isinstance(voice.config_json, str)
                        else (voice.config_json or {}).get("params")
                    ) if voice and voice.config_json else None,
                )
                new_wavs = sorted(temp_dir.glob("*.wav"))
                if not new_wavs:
                    continue
                new_wav = new_wavs[0]

                af = (
                    db.query(AudioFile)
                    .filter(AudioFile.audio_job_id == job.id, AudioFile.segment_id == seg.id)
                    .first()
                )
                if af:
                    old_path = Path(af.file_path)
                    if old_path.exists():
                        old_path.unlink()
                    new_dest = old_path if old_path.parent.exists() else temp_dir / new_wav.name
                    import shutil
                    shutil.copy2(new_wav, new_dest)
                    af.file_path = str(new_dest)
                    af.duration = None
                    db.commit()
                replaced += 1
            except Exception as exc:
                db.rollback()
                raise HTTPException(status_code=500, detail=f"段 {seg.id[:8]} 重做失败: {exc}")

    # 4. 重合成 full_paragraph.wav (concat 所有段)
    try:
        from scripts.tts_client import _concat_wavs_with_ffmpeg

        all_afs = (
            db.query(AudioFile)
            .filter(AudioFile.audio_job_id == job.id, AudioFile.segment_id.isnot(None))
            .order_by(AudioFile.filename)
            .all()
        )
        wavs = [Path(af.file_path) for af in all_afs if Path(af.file_path).exists()]
        if wavs:
            combined = output_dir / "full_paragraph.wav"
            _concat_wavs_with_ffmpeg(wavs, combined)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"重合成失败: {exc}")

    return {"ok": True, "replaced": replaced, "msg": f"替换完成, 重做 {replaced} 段"}