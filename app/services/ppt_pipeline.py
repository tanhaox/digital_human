# -*- coding: utf-8 -*-
"""PPT 出片后台产线 — 任务状态 / SSE 事件 / TTS→对齐→渲染→拼片编排.

拆包自 routers/ppt.py (2026-09-01), 函数体原样搬运零行为变更;
router 层只留 HTTP handler, 编排与任务状态全部在本模块 (两者共享 _JOBS dict).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from app.config import get_config
from app.database import db_session
from app.models import AudioFile, AudioJob, Script, Segment, Voice

logger = logging.getLogger(__name__)

__all__ = ["_JOBS", "_Cancelled", "_evt", "_job_workdir", "_run_ppt_pipeline"]

# 在跑任务: job_id → 状态 (与蒸馏 _BATCH 同模式, 供轮询 + SSE)
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class _Cancelled(Exception):
    """产线取消信号 (cancel 标志检查抛出)."""


def _evt(job_id: str, msg: str, level: str = "info", **extra) -> None:
    from app.services.director_events import publish
    e = {"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg, **extra}
    j = _JOBS.setdefault(job_id, {})
    j.setdefault("events", []).append(e)
    if len(j["events"]) > 500:
        j["events"] = j["events"][-500:]
    publish(job_id, e)


def _job_workdir(job_id: str) -> Path:
    root = Path(get_config().defaults.ppt_work_root)
    return root / job_id


# ── 后台产线 ─────────────────────────────────────────────────
def _run_ppt_pipeline(job_id: str) -> None:
    """PPT → 逐页台词 TTS → 对齐 → 渲染 → 拼片."""
    try:
        with db_session() as db:
            from app.models import Article, Host, Persona
            from app.services.book_service.persona import ensure_book_account
            from app.services.ppt_service import (parse_pptx, build_slide_html,
                                                  render_slide_png, png_to_zoompan_mp4,
                                                  render_slide_mp4)
            from app.services.director_service._alignment import _align_fast_or_whisper

            cfg = get_config()
            workdir = _job_workdir(job_id)
            # bs1 页单产线 (2026-09-08 老谭读书): slides 不来自 pptx, 来自确认版页单
            # (episodes.bs1_pages_json / job pages); 每页 narration=segment, 渲染用页 PNG。
            bs1_pages = None
            if _JOBS.get(job_id, {}).get("mode") == "bs1":
                from app.services.book_service.slide_render import load_pages
                bs1_pages = load_pages(workdir)
                if not bs1_pages:
                    raise RuntimeError("bs1 页单缺失 (workdir/pages.json), 请重新规划")
                from app.services.ppt_service import Slide as _Slide
                slides = [_Slide(index=i + 1, notes=pd.get("narration") or "")
                          for i, pd in enumerate(bs1_pages)]
                _evt(job_id, f"bs1 页单 {len(slides)} 页, 开始建稿", "ok")
            else:
                slides = parse_pptx(workdir / "source.pptx")
                _evt(job_id, f"解析完成 {len(slides)} 页, 开始建稿", "ok")

            # ── 1. 建 Script: 每页备注台词 = 一个 segment ──
            # bs1 (0908): 老谭读书账号 (老谭音色) — pptx 链路才是静读书账号
            from app.services.book_service.persona import ensure_laotan_book_account
            if _JOBS.get(job_id, {}).get("mode") == "bs1":
                host = ensure_laotan_book_account(db)
            else:
                host = ensure_book_account(db)
            persona = db.query(Persona).filter(Persona.host_id == host.id).first()
            article = Article(title="[拆书PPT]", raw_text="", track="tech")
            db.add(article); db.flush()
            script = Script(
                article_id=article.id, host_id=host.id,
                script_text="\n".join(s.notes for s in slides if s.notes),
                video_format="landscape",
                prompt_template=persona.prompt_template if persona else "jingshu-book",
            )
            db.add(script); db.flush()

            # 每页一个 Segment (selected_for_host=True 全部参与 TTS)
            segments: list[Segment] = []
            for s in slides:
                seg = Segment(
                    script_id=script.id, line_index=s.index - 1,
                    text=s.notes or f"(第{s.index}页无台词)", segment_type="body",
                    selected_for_host=True, host_order=s.index - 1,
                )
                db.add(seg)
                segments.append(seg)
            db.commit()
            # refresh 拿每个 segment id (对齐按 segment_id 匹配)
            for seg in segments:
                db.refresh(seg)
            db.refresh(script)

            # ── 2. TTS: 复用 audio 链路 ──
            # 音色选择 (2026-08-22): 前端选的 voice_id 优先 (PPT 一键成片);
            # 否则回退书账号 persona 音色 (静读书/静姐)。
            voice = None
            sel_voice_id = _JOBS.get(job_id, {}).get("voice_id")
            if sel_voice_id:
                voice = db.get(Voice, sel_voice_id)
            if not voice:
                persona_v = db.query(Persona).filter(Persona.host_id == host.id).first()
                if persona_v and persona_v.voice_id:
                    voice = db.get(Voice, persona_v.voice_id)
            if not voice:
                voice = db.query(Voice).filter(Voice.host_id == host.id).first()
            voice_id = voice.id if voice else None

            # TTS 缓存 key (2026-08-21): 音色+全段台词哈希, 稿没变复用音频免重跑合成
            # 引擎因子 (2026-08-25): 换引擎语速/音色风格全变, 旧缓存复用会拿到异引擎
            # 音频混拼; key 掺引擎版号, 升引擎自动失效全部缓存。(产线现 IndexTTS2)
            # 情绪因子 (2026-09-03): PPT 线接入情绪标注后合成产物不同 (旧缓存=整篇
            # calm), key 掺 emo 版号自动失效旧无情绪音频。
            # 拼音因子 (2026-09-03): <字|PINYIN> 引擎边界改裸拼音输出 (旧缓存=字拼音
            # 双读坏音频), key 掺 py1 自动失效旧标注音频。
            import hashlib as _hl
            # 0910 修: 引擎因子取真实 backend (旧硬编码 indextts2 令 2.5 与 2 缓存互串)
            _eng = ("indextts25" if (voice is not None and voice.backend == "indextts25")
                    else str(getattr(voice, "backend", "") or "indextts2"))
            cache_key = _hl.sha256(
                (_eng + "+emo+py1" + "\x00" + str(voice_id or "") + "\x00"
                 + "\x00".join(seg.text for seg in segments)).encode()
            ).hexdigest()
            cached_job = (
                db.query(AudioJob)
                .filter(AudioJob.status == "completed", AudioJob.tts_cache_key == cache_key)
                .order_by(AudioJob.completed_at.desc()).first()
            )

            combined_path = None
            combined_af = None
            timings_cache: list[dict] | None = None
            audio_files: list[AudioFile] = []
            if cached_job:
                # 0910 修: 命中先验产物在盘 (清场后旧 job 残留 → 复用 = 零音轨草稿)
                _cj_dir = Path(cached_job.output_dir)
                _cj_wavs = sorted(_cj_dir.glob("0*.wav")) if _cj_dir.is_dir() else []
                if len(_cj_wavs) < len(segments):
                    _evt(job_id, f"TTS 缓存命中但产物缺失 ({len(_cj_wavs)}/{len(segments)} wav), 重新合成", "warn")
                    cached_job = None
            if cached_job:
                # 缓存命中: 复用已有音频 + 按时长排时间线 (台词相同, 顺序一致)
                # 2026-08-21 修复: 之前按 af.id(UUID 随机序) 排序 → 缓存音频段被打乱
                # (P3 装成 P12 的 011.wav, 字幕与音频错位 → "字幕丢失很多")。
                # 正确序 = 缓存音频对应 segment 的 line_index (页序), 非 UUID.
                seg_afs = [af for af in cached_job.audio_files if af.segment_id and af.duration]
                if seg_afs:
                    _line_map = {
                        s.id: s.line_index
                        for s in db.query(Segment).filter(
                            Segment.id.in_([af.segment_id for af in seg_afs])
                        ).all()
                    }
                    seg_afs.sort(key=lambda af: _line_map.get(af.segment_id, 0))
                combined_af = next((af for af in cached_job.audio_files if af.segment_id is None), None)
                combined_path = combined_af.file_path if combined_af else None
                audio_files = seg_afs
                cum = 0.0
                timings_cache = []
                for i, seg in enumerate(segments):
                    dur = float(seg_afs[i].duration) if i < len(seg_afs) and seg_afs[i].duration else 0.0
                    timings_cache.append({"segment_id": seg.id,
                                          "start": round(cum, 3), "end": round(cum + dur, 3)})
                    cum += dur
                _evt(job_id, f"TTS 缓存命中 ({len(segments)} 段, 复用已有音频, 免重跑合成)", "ok",
                     type="tts_done")
            else:
                _evt(job_id, f"开始 TTS ({len(segments)} 段, 音色={voice.name if voice else '默认'})", "info")
                from app.services.tts_service import TTSService
                from app.services.gpu_service_manager import get_gpu_service_manager
                tts = TTSService(cfg.defaults)
                job = AudioJob(
                    script_id=script.id, voice_id=voice_id,
                    output_dir=str(workdir / "audio"), status="pending",
                    total_segments=len(segments), completed_segments=0,
                    tts_cache_key=cache_key,
                )
                db.add(job); db.commit(); db.refresh(job)

                def _progress(completed, total, text, af):
                    if _JOBS.get(job_id, {}).get("cancel"):
                        raise _Cancelled()
                    audio_files.append(af)
                    # 落库: 分段 wav 需进 AudioFile 表, 对齐快路径按 segment_id 匹配
                    if af:
                        job.completed_segments = completed
                        db.add(af)
                        db.commit()
                        db.refresh(af)
                    _evt(job_id, f"TTS {completed}/{total}: {text[:20]}…", "info",
                         type="tts_progress", progress=f"{completed}/{total}")

                # ── 情绪标注 (2026-09-03 复用新闻线 audio._do_tts 同款) ──
                # PPT 线此前直调 tts.generate 未传情绪 → IndexTTS 整篇 calm。
                # 同源现场标注 (与 TTS 输入完全同文本, 零错配窗口)。拆书线走
                # 读书版规则 (calm+confident 混合打底 + melancholic 共情,
                # 2026-09-03 用户定稿); 失败落整篇 calm/2 兜底 (读书人设)。
                from app.services.pinyin_fix import tts_adapt, scan_pinyin_hits
                from app.services.boost_service import (
                    _parse_emotion_annotations,
                    annotate_emotions,
                )
                clean_text = "\n".join(seg.text for seg in segments)
                # 词表命中提示 (2026-09-03): 扫干净文本 (标注后原文子串已不存在),
                # 让用户在 SSE 流里看到哪些词被系统纠音。
                hits = scan_pinyin_hits(clean_text)
                if hits:
                    _evt(job_id, f"纠音词表命中 {len(hits)} 处: {'、'.join(hits)}", "ok")
                tts_text = tts_adapt(clean_text)
                # 情绪 profile 分流 (0908): 静读书=book (calm 打底); 老谭读书=book_laotan
                # (confident+serious 混合打底 0907 定稿 — 此前硬编码 book, 老谭线拿到
                # 静读书平静版, 用户实听「基本没什么情绪」实锤)
                _emo_style = "book_laotan" if _JOBS.get(job_id, {}).get("mode") == "bs1" else "book"
                emotion_segments = [{"emotion": "calm", "strength": 2, "text": tts_text}]
                if _emo_style == "book_laotan":
                    emotion_segments = [{"emotion": "confident", "strength": 3, "text": tts_text}]
                _skip_p5 = voice is not None and getattr(voice, "backend", "") == "indextts25"
                if _skip_p5:
                    _evt(job_id, "2.5 情感参考通道, 跳过情绪标注 (韵律=情感参考音频)", "info")
                else:
                    _evt(job_id, "情绪标注中…", "info")
                try:
                    persona_name = ((getattr(host, "stamp_name", None) or host.name)
                                    if host else "静姐")
                    raw_anno = (None if _skip_p5 else
                                annotate_emotions(tts_text, persona_name, style=_emo_style))
                    parsed = _parse_emotion_annotations(raw_anno) if raw_anno else None
                    if parsed:
                        emotion_segments = parsed
                        _evt(job_id, f"情绪标注完成: {len(parsed)} 段", "ok")
                    else:
                        _evt(job_id, ("2.5 通道已跳过标注" if _skip_p5 else
                                      f"情绪标注解析失败, 整篇兜底打底({_emo_style})"), "warn")
                except Exception as exc:
                    logger.warning("[ppt %s] emotion annotate failed, fallback calm: %s",
                                   job_id[:8], exc)
                    _evt(job_id, f"情绪标注失败, 整篇兜底: {exc}", "warn")
                if _JOBS.get(job_id, {}).get("cancel"):
                    raise _Cancelled()

                # 用 GPU 服务管理器会话: 自动拉起 IndexTTS (与 audio 端点一致)
                backend = voice.backend if voice and voice.backend else cfg.defaults.backend
                manager = get_gpu_service_manager()
                with manager.session(backend, status_callback=lambda m: _evt(job_id, m, "info")):
                    result = tts.generate(job, segments, voice,
                                          emotion_annotations=emotion_segments,
                                          progress_callback=_progress,
                                          status_callback=lambda m, lv: _evt(job_id, m, lv))
                # 整段拼接: tts.generate 已产出 full_paragraph.wav (combined_file)
                combined = result.get("combined_file") or {}
                combined_path = combined.get("file_path")
                job.status = "completed"
                job.completed_segments = len(segments)
                db.commit()
                _evt(job_id, f"TTS 完成: {len(audio_files)} 段, 整段 {combined.get('duration') or '?'}s", "ok",
                     type="tts_done")

            # ── 3. 对齐: 每页台词 → start/end (缓存命中直接用时长排线, 否则 fast path) ──
            if timings_cache is not None:
                timings = timings_cache
                _evt(job_id, f"对齐(缓存时长)完成: {len(timings)} 段", "ok")
            else:
                db.commit()
                _evt(job_id, "开始对齐 (TTS 时长快路径)", "info")
                # audio 参数应为 AudioFile (combined); 用 AudioFile 对象
                if combined_path and Path(combined_path).exists():
                    if combined_af is None:
                        combined_af = db.query(AudioFile).filter(
                            AudioFile.audio_job_id == job.id,
                            AudioFile.segment_id.is_(None),
                        ).order_by(AudioFile.id.desc()).first()
                    if combined_af is None:
                        combined_af = AudioFile(
                            audio_job_id=job.id, segment_id=None,
                            filename=Path(combined_path).name, file_path=str(combined_path),
                            duration=None, sample_rate=24000,
                        )
                        db.add(combined_af); db.commit()
                aligned = _align_fast_or_whisper(
                    db, combined_af, script.id,
                    [{"id": seg.id, "segment_id": seg.id, "text": seg.text, "line_index": seg.line_index} for seg in segments],
                    job_id=job_id,
                )
                timings = aligned.get("segment_timings") or []
                _evt(job_id, f"对齐完成: {len(timings)} 段", "ok")

            # ── 4. 逐页渲染 ──
            # 系列皮肤对齐 (2026-08-21): 非母本集 apply 母本皮肤 (背景/色/字号)
            # bs1 页单自带版式, 不 apply 皮肤 (0908)
            bound = _JOBS.get(job_id, {})
            skin = None
            if bound.get("book_id") and bs1_pages is None:
                from app.services.skin_pack_service import load_skin, apply_skin_to_slides, extract_master
                skin = load_skin(bound["book_id"])
                if skin and bound.get("ep_index") == skin.master_ep:
                    # 母本重渲染: 当前 pptx 即新母本 → 重新抽取皮肤包
                    # (2026-08-21 修复: 旧皮肤是"上次上传的母本"抽的, 重跑 ep1 后作废,
                    #   ep2-6 后续渲染应对齐新皮肤; 提取失败则沿用旧包, 不阻断)
                    try:
                        skin = extract_master(workdir, bound["book_id"], bound["ep_index"])
                        _evt(job_id, f"母本重渲染, 已更新皮肤包 (第{bound['ep_index']}集为新母本)", "ok")
                    except Exception as exc:
                        _evt(job_id, f"母本皮肤更新失败(沿用旧包): {exc}", "warn")
                    skin = None  # 母本自身是源, 不 apply
                if skin:
                    apply_skin_to_slides(slides, skin)
                    _evt(job_id, f"母本皮肤载入 (保留原稿配色/背景, 仅字体动画 — 2026-08-22 改)", "ok")
            clips_dir = workdir / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            mode = bound.get("render_mode", "jy2")
            clip_paths: list[Path] = []
            clip_meta: list[dict] = []  # {path, image, start_sec, duration_sec, notes}
            # 段→wav (元素级草稿逐页音频)
            if timings_cache is not None:
                # TTS 缓存命中: 缓存音频顺序 = 当前段顺序 (台词相同), 按顺序映射
                seg_wav_map = {}
                for i, seg in enumerate(segments):
                    if i < len(audio_files) and audio_files[i].file_path \
                            and Path(audio_files[i].file_path).exists():
                        seg_wav_map[seg.id] = audio_files[i].file_path
            else:
                seg_wav_map = {af.segment_id: af.file_path for af in audio_files
                               if af.segment_id and af.file_path and Path(af.file_path).exists()}
            element_pages: list[dict] = []  # jy2: 逐页层+编排数据
            # 尾卡双卡 (2026-09-05 拍板): PPT 全页照旧 (原"下期见"尾页承载结尾
            # 口播) → B-Q1 书摘金句卡 (倒数第二, 4-5s 静音) → B-08 书籍信息卡
            # (垫底, 静音)。两卡入场挂 title_in whoosh (效仿财经末卡)。
            tail_cards: list[dict] = []
            if mode == "jy2" and bound.get("book_id"):
                from app.models import BookProject, Episode
                book = db.query(BookProject).filter(
                    BookProject.id == bound["book_id"]).first()
                ep_row = None
                if book:
                    ep_row = db.query(Episode).filter(
                        Episode.book_id == book.id,
                        Episode.ep_index == int(bound.get("ep_index") or 1),
                    ).first()
                total_end = max((float(t.get("end", 0)) for t in (timings or [])), default=0.0)
                # 尾卡模板分流 (0908): 老谭读书=bs1 族, 静读书=hf 纸墨系 (两线不互通)
                _is_lt = _book_brand(db, book)[0] == "老谭读书" if book else False
                _tpl_q = "bs1-bookquote-v1" if _is_lt else "hf-bookquote-v1"
                _tpl_i = "bs1-bookinfo-v1" if _is_lt else "hf-bookinfo-v1"
                if book and ep_row:
                    qi = _build_bookquote_input(db, book, ep_row, 5)
                    if qi:
                        png = _render_card_png(db, _tpl_q, qi, workdir, "quote_card.png")
                        if png:
                            tail_cards.append({"png": png, "dur": 4.5, "label": "B-Q1 书摘金句卡"})
                if book:
                    bi = _build_bookinfo_input(db, book, 5)
                    if bi:
                        png = _render_card_png(db, _tpl_i, bi, workdir, "book_card.png")
                        if png:
                            tail_cards.append({"png": png, "dur": 5.0, "label": "B-08 书籍信息卡"})
                if tail_cards:
                    _evt(job_id, "尾卡就绪: " + " + ".join(c["label"] for c in tail_cards), "ok")
            # bs1 页间大气口 (0910): 0.5s 段落呼吸 (config bs1_page_gap_sec, 0=关)
            _bs1_gap = (float(getattr(cfg.defaults if hasattr(cfg, "defaults") else cfg, "bs1_page_gap_sec", 0.5))
                        if mode == "jy2" and bs1_pages is not None else 0.0)
            _gap_cum = 0.0
            for i, s in enumerate(slides):
                if bound.get("cancel"):
                    raise _Cancelled()
                seg_id = segments[i].id
                timing = next((t for t in timings if t.get("segment_id") == seg_id), None)
                start = timing.get("start", 0.0) if timing else 0.0
                dur = (timing.get("end") - timing.get("start")) if timing else 6.0
                if mode == "jy2" and bs1_pages is not None:
                    # bs1 页: 整幅模板帧单层 base (确认版 PNG 直接消费; 缺失现场补渲染)
                    # 页间大气口 (0910 用户令): 页窗后移累计间隙, 页时长+间隙=PNG定格,
                    # 逐页音频照旧摆 start → 尾部自然静音呼吸 (段落级气口)
                    start += _gap_cum
                    pd = bs1_pages[i]
                    png = workdir / pd["png"] if pd.get("png") else None
                    if png is None or not png.exists():
                        from app.services.book_service.slide_render import (
                            build_context as _bc, render_bs1_pages as _rb,
                        )
                        _bc_db = db
                        _ctx = _bc(_bc_db, bound["book_id"], int(bound["ep_index"]))
                        if _ctx and _rb(bs1_pages, _ctx, workdir, only=[i]):
                            png = workdir / (bs1_pages[i].get("png") or "")
                    if png is None or not png.exists():
                        raise RuntimeError(f"bs1 第 {i+1} 页 PNG 缺失且补渲染失败")
                    element_pages.append({
                        "start_sec": round(start, 3),
                        "duration_sec": round(dur + _bs1_gap, 3),
                        "audio_file": seg_wav_map.get(seg_id),
                        "narration": s.notes or "",
                        "layers": [{"kind": "base", "file": str(png)}],
                    })
                    _gap_cum += _bs1_gap
                    _evt(job_id, f"bs1 页 {i+1}/{len(slides)} ({dur:.1f}s)", "info",
                         progress=f"{i+1}/{len(slides)}")
                    continue
                html = build_slide_html(s, skin=skin)
                page_dir = workdir / f"page_{s.index:02d}"
                png = clips_dir / f"page_{s.index:02d}.png"
                if mode == "anim":
                    # 旧路径: Playwright 逐帧捕获分层入场 (慢, 短片才值得)
                    out = clips_dir / f"page_{s.index:02d}.mp4"
                    render_slide_mp4(html, page_dir, out, dur)
                    clip_paths.append(out)
                    clip_meta.append({"path": str(out), "image": str(png), "start_sec": round(start, 3),
                                      "duration_sec": round(dur, 3), "notes": s.notes})
                elif mode == "jy2":
                    # 元素级拆解: 每页 base+文字/图透明层 → 剪映多轨草稿
                    from app.services.ppt_frame_capture import capture_element_png
                    from app.services.ppt_service import (build_base_html, build_text_element_html,
                                                          build_image_element_html)
                    from app.services.jy_draft_service import compute_page_timing
                    pd = workdir / "layers" / f"p{s.index:02d}"
                    pd.mkdir(parents=True, exist_ok=True)

                    def _cap(name: str, html_txt: str) -> Path:
                        (pd / f"{name}.html").write_text(html_txt, encoding="utf-8")
                        return capture_element_png(pd / f"{name}.html", pd / f"{name}.png")

                    _cap("base", build_base_html(s))
                    pg_layers: list[dict] = [{"kind": "base", "file": str(pd / "base.png")}]
                    for ti, tb in enumerate(s.text_blocks):
                        _cap(f"t{ti}", build_text_element_html(tb))
                        pg_layers.append({"kind": "text", "file": str(pd / f"t{ti}.png"),
                                          "text": tb.text, "order": tb.shape_id,
                                          "left": tb.left, "top": tb.top,
                                          "pt": tb.font_size_pt, "bold": tb.bold})
                    for ii, ib in enumerate(s.image_blocks):
                        _cap(f"im{ii}", build_image_element_html(ib))
                        pg_layers.append({"kind": "image", "file": str(pd / f"im{ii}.png"),
                                          "order": 100 + ib.shape_id, "left": ib.left, "top": ib.top})
                    timed = compute_page_timing(pg_layers, s.notes or "", start, dur)
                    base_l = [l for l in pg_layers if l["kind"] == "base"]
                    element_pages.append({
                        "start_sec": round(start, 3), "duration_sec": round(dur, 3),
                        "audio_file": seg_wav_map.get(seg_id), "narration": s.notes or "",
                        "layers": base_l + timed,
                    })
                    _evt(job_id, f"元素层 {s.index}/{len(slides)} ({dur:.1f}s)", "info",
                         progress=f"{s.index}/{len(slides)}")
                    continue
                else:
                    # 整页静态帧 (jy/auto), 动画交给渲染层 (剪映/zoompan)
                    render_slide_png(html, page_dir, png)
                    if mode == "auto":
                        out = clips_dir / f"page_{s.index:02d}.mp4"
                        png_to_zoompan_mp4(png, out, dur)
                        clip_paths.append(out)
                    clip_meta.append({"path": str(png), "image": str(png), "start_sec": round(start, 3),
                                      "duration_sec": round(dur, 3), "notes": s.notes})
                _evt(job_id, f"渲染页 {s.index}/{len(slides)} ({dur:.1f}s)", "info",
                     progress=f"{s.index}/{len(slides)}")

            # 尾卡双卡摆位 (2026-09-05): PPT 之后静音追加, B-Q1 → B-08 垫底
            for c in tail_cards:
                element_pages.append({
                    "start_sec": round(total_end + _gap_cum, 3), "duration_sec": c["dur"],
                    "audio_file": None, "narration": "",
                    "layers": [{"kind": "base", "file": str(c["png"])}],
                    "sfx": "title_in",
                })
                total_end += c["dur"]

            # 存元数据: 供剪映草稿导出 / 后续复用
            meta = {"slides": len(slides), "clips": clip_meta, "audio": combined_path,
                    "video_format": "landscape", "render_mode": mode,
                    "book_id": bound.get("book_id"), "ep_index": bound.get("ep_index"),
                    "skin_applied": bool(skin),
                    "master_ep": skin.master_ep if skin else None}
            (workdir / "ppt_manifest.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            _JOBS[job_id]["manifest"] = meta

            if bound.get("cancel"):
                raise _Cancelled()

            # ── 5. 成片出口 ──
            if mode == "jy2":
                # 元素级剪映草稿: 逐元素动画/动态字幕/音效/逐页音频 (实验验证)
                if not element_pages:
                    raise RuntimeError("jy2: 无元素层产出")
                from app.services.jy_draft_service import export_element_draft
                # 首帧免责字幕 (视觉化): 从书线合规配置读, 不上口播
                disclaimer = ""
                try:
                    from app.services.book_service.distiller import load_book_rules
                    disclaimer = (load_book_rules().get("opening_disclaimer") or "")
                except Exception:
                    disclaimer = ""
                draft_name = f"PPT_{time.strftime('%Y%m%d_%H%M')}_{job_id[:8]}"
                _evt(job_id, f"元素级编排 {len(element_pages)} 页 → 剪映草稿…", "info")
                # 系列角标: 书名 + 集数 (左上角呼吸闪烁); 品牌/台标按书的 persona (0907 双人物)
                book_title = ""
                book_brand = "静读书"
                ep_index = bound.get("ep_index")
                if bound.get("book_id"):
                    try:
                        from app.models import BookProject
                        bk = db.query(BookProject).filter(BookProject.id == bound["book_id"]).first()
                        if bk:
                            book_title = bk.book_title or ""
                            book_brand = _book_brand(db, bk)[0]
                    except Exception:
                        book_title = ""
                try:
                    from app.services.jy_draft_service.watermark import (
                        WATERMARK_ASSET, prepare_watermark,
                    )
                    # 台标资产: 老谭读书用专属 logo (缺失跳过), 其余静读书
                    _lt_logo = _laotan_book_logo()
                    if book_brand == "老谭读书" and _lt_logo.exists():
                        logo_png = str(_lt_logo)
                    elif book_brand != "老谭读书":
                        logo_png = prepare_watermark()
                        if not logo_png:
                            _evt(job_id, "logo 台标跳过 (素材缺失且源 logo 不可达)", "warn")
                    else:
                        logo_png = None
                        _evt(job_id, "logo 台标跳过 (老谭读书 logo 未落位: "
                                     "assets/watermark/laotanbook_logo.png)", "warn")
                    draft = export_element_draft(draft_name, element_pages, canvas=(1920, 1080),
                                                 disclaimer=disclaimer,
                                                 book_title=book_title, ep_index=ep_index,
                                                 watermark=logo_png,
                                                 book_brand=book_brand)
                    _JOBS[job_id]["draft"] = draft
                    _evt(job_id, f"元素级剪映草稿就绪: {draft['draft_name']} "
                                 f"(base{draft['base_segments']}+元素{draft['element_segments']}"
                                 f"+字幕{draft['caption_segments']}+音效{draft['sfx_segments']}"
                                 f"+logo{draft.get('logo_watermark', 0)})", "ok",
                         type="ppt_done")
                except Exception as exc:
                    _evt(job_id, f"元素级草稿导出失败: {exc}", "error", type="ppt_error")
                    raise
            elif mode == "auto":
                _evt(job_id, "拼接成片…", "info")
                final_mp4 = _concat_with_audio(clip_paths, combined_path, workdir)
                _JOBS[job_id]["output"] = str(final_mp4)
                _evt(job_id, f"成片完成: {final_mp4.name} ({final_mp4.stat().st_size/1024/1024:.1f}MB)", "ok",
                     type="ppt_done")
            elif mode == "jy":
                # 剪映草稿 (入场动画/转场/动效字幕由剪映给, 打开即审/导出)
                from app.services.jy_draft_service import export_ppt_draft
                try:
                    draft = export_ppt_draft(job_id, cfg.defaults.ppt_work_root)
                    _JOBS[job_id]["draft"] = draft
                    _evt(job_id, f"剪映草稿就绪: {draft['draft_name']} "
                                 f"({draft['video_segments']} 页, 入场动画+转场)", "ok",
                         type="ppt_done")
                except Exception as exc:
                    _evt(job_id, f"剪映草稿导出失败(可重试 /export-jy-draft): {exc}", "warn",
                         type="ppt_done")
    except _Cancelled:
        _JOBS[job_id]["error"] = "用户取消"
        _evt(job_id, "产线已取消", "warn", type="ppt_error")
    except Exception as exc:
        logger.exception("[ppt %s] 产线失败", job_id)
        _JOBS[job_id]["error"] = str(exc)
        _evt(job_id, f"产线失败: {exc}", "error", type="ppt_error")
    finally:
        j = _JOBS[job_id]
        j["status"] = "cancelled" if j.get("error") == "用户取消" else (
            "done" if not j.get("error") else "failed")


def _logo_data_uri(asset: Path | None = None) -> str:
    """拆书线尾卡 logo (透明 PNG) → data URI.

    原图 1004px/1.8MB → base64 2.5M 字符超 json_schema maxLength;
    显示位仅 120px 高 → 降采样 240px (2x) 足清, ~几十 KB。
    asset (2026-09-07 双人物): 缺省静读书 (jingbook_logo.png);
    老谭读书 = assets/watermark/laotanbook_logo.png (logo 后续提供, 缺失返回空串=卡上无 logo)。
    """
    import base64
    import io
    from app.services.jy_draft_service.watermark import WATERMARK_ASSET
    src = Path(asset) if asset else WATERMARK_ASSET
    try:
        if not src.exists():
            return ""
        from PIL import Image
        im = Image.open(src)
        im.thumbnail((240, 240), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        logger.warning("[ppt] logo 读取失败 (%s): %s", src.name, exc)
        return ""


def _laotan_book_logo() -> Path:
    # 老谭读书 logo 约定路径 (0907 用户: logo 重新生成后落此文件名即自动生效)
    from app.services.jy_draft_service.watermark import WATERMARK_ASSET
    return WATERMARK_ASSET.parent / "laotanbook_logo.png"


def _book_brand(db, book) -> tuple[str, str]:
    """书的账号品牌 (2026-09-07 双人物): book.persona_id → (brand_name, logo_data_uri).

    无 persona / 查不到 → 回退静读书 (旧行为)。老谭读书 logo 缺失时 logo 为空串
    (尾卡无 logo, 文字 brand 照常), logo 文件落位后自动生效。
    """
    default = ("静读书", _logo_data_uri())
    try:
        ij = book.input_json if isinstance(book.input_json, dict) else {}
        pid = ij.get("persona_id")
        if not pid:
            return default
        from app.models import Persona
        persona = db.query(Persona).filter(Persona.id == pid).first()
        if not persona or not persona.host_id:
            return default
        from app.models import Host
        host = db.query(Host).filter(Host.id == persona.host_id).first()
        key = (host.persona_key if host else "") or ""
        if key == "laotan-book":
            return (persona.brand_name or persona.name, _logo_data_uri(_laotan_book_logo()))
        return (persona.brand_name or persona.name, _logo_data_uri())
    except Exception as exc:
        logger.warning("[ppt] 品牌解析失败, 回退静读书: %s", exc)
        return default


def _render_card_png(db, template_id: str, input_data: dict, workdir: Path,
                     out_name: str) -> Path | None:
    """通用卡渲染: VisualRenderJob → mp4 → 抽稳定末帧 PNG (尾卡静态帧)."""
    try:
        from app.models import VisualRenderJob
        from app.services.visual_render_service import execute_visual_render_job
        from app.infrastructure.ffmpeg import run_ffmpeg

        job = VisualRenderJob(template_id=template_id, input_json=input_data, status="queued")
        db.add(job); db.commit(); db.refresh(job)
        result = execute_visual_render_job(db, job.id, template_id, input_data)
        if result.get("status") != "completed":
            logger.warning("[ppt] %s 渲染失败: %s", template_id, result.get("error_message"))
            return None
        mp4 = Path(result.get("output_path") or "")
        if not mp4.exists():
            return None
        dur = float(input_data.get("duration_sec") or 5)
        out = workdir / out_name
        run_ffmpeg(["ffmpeg", "-y", "-loglevel", "error",
                    "-ss", str(max(0.0, dur - 0.5)), "-i", str(mp4),
                    "-frames:v", "1", str(out)], timeout=60)
        return out if out.exists() else None
    except Exception as exc:
        logger.warning("[ppt] %s 渲染异常: %s", template_id, exc)
        return None


def _build_bookinfo_input(db, book, page_dur: float) -> dict | None:
    """B-08 书籍信息卡输入 (2026-09-05 拍板版): 书名/作者/指南/简介 + 账号品牌
    (2026-09-07 双人物: 按书的 persona 解析, 静读书/老谭读书各自品牌+logo)."""
    import re as _re
    ij = book.input_json if isinstance(book.input_json, dict) else {}
    guide = ""
    claim = ij.get("全书核心主张")
    if isinstance(claim, dict):
        guide = str(claim.get("value") or "")[:200]
    # 简介行: 按 。； 打包 ≤60 字/行, ||| 分段 (≤3 段, 与模板契约一致)
    bio_lines: list[str] = []
    bio = (getattr(book, "author_bio", "") or "").strip()
    if bio:
        cur = ""
        for sent in _re.split(r"(?<=[。；])", bio):
            if not sent:
                continue
            if len(cur) + len(sent) <= 60:
                cur += sent
            else:
                if cur:
                    bio_lines.append(cur)
                cur = sent if len(sent) <= 60 else sent[:59] + "…"
        if cur:
            bio_lines.append(cur)
    if not (book.author or guide or bio_lines):
        return None
    _brand, _logo = _book_brand(db, book)
    return {
        "title": f"《{book.book_title}》"[:64],
        "author": (book.author or "")[:128],
        "guide": guide,
        "bio": "|||".join(bio_lines[:3]),
        "brand_name": _brand,
        "logo_b64": _logo,
        "duration_sec": max(4, min(10, round(page_dur))),
    }


def _build_bookquote_input(db, book, ep, page_dur: float) -> dict | None:
    """B-Q1 书摘金句卡输入: 本集 3 句 (quotes_json, 缺则确定性选句回填)."""
    try:
        from app.services.book_service.quotes import ensure_episode_quotes
        quotes = ensure_episode_quotes(db, book, ep)
    except Exception as exc:
        logger.warning("[ppt] 选句失败 (B-Q1 跳过): %s", exc)
        return None
    if len(quotes) < 3:
        return None
    _brand, _logo = _book_brand(db, book)
    data = {"book_title": f"《{book.book_title}》"[:96],
            "brand_name": _brand,
            "logo_b64": _logo,
            "duration_sec": max(4, min(8, round(page_dur)))}
    for i in range(3):
        data[f"q{i+1}"] = str(quotes[i].get("text") or "")[:120]
        data[f"k{i+1}"] = str(quotes[i].get("key") or "")[:60]
    return data


def _render_book_card(db, book_id: str, workdir: Path, page_dur: float) -> Path | None:
    """尾页卡片渲染入口 (2026-09-05 改版): B-08 书籍信息卡 (hf-bookinfo-v1).

    旧 hf-source-v1 (来源行版, 静姐读书) 退役; 版式源 .tmp/style_book_card.html。
    """
    from app.models import BookProject
    book = db.query(BookProject).filter(BookProject.id == book_id).first()
    if not book:
        return None
    input_data = _build_bookinfo_input(db, book, page_dur)
    if not input_data:
        return None
    return _render_card_png(db, "hf-bookinfo-v1", input_data, workdir, "book_card.png")


def _concat_with_audio(clip_paths: list[Path], audio_path: str | None,
                       workdir: Path) -> Path:
    """拼接各页 mp4 + 主音轨 (若有). 用 concat demuxer + 音轨叠加."""
    from app.infrastructure.ffmpeg import run_ffmpeg
    concat_file = workdir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths), encoding="utf-8")

    out_raw = workdir / "concat_raw.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(concat_file),
           "-c", "copy", str(out_raw)]
    try:
        run_ffmpeg(cmd, timeout=120)
    except RuntimeError:
        if not out_raw.exists():
            raise

    # 加主音轨 (音频时长 > 视频则裁剪; 视频更长则保持静音)
    final = workdir / "ppt_final.mp4"
    if audio_path and Path(audio_path).exists():
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-i", str(out_raw), "-i", str(audio_path),
               "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac",
               "-shortest", str(final)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-i", str(out_raw),
               "-c", "copy", str(final)]
    try:
        run_ffmpeg(cmd, timeout=120)
    except RuntimeError:
        if not final.exists():
            raise
    return final
