"""Voice router: CRUD + TTS test/trial/carnival/anchoring endpoints."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Voice
from app.schemas import (
    VoiceCarnivalRequest,
    VoiceCreate,
    VoiceOut,
    VoiceSetReferenceRequest,
    VoiceTestRequest,
    VoiceUpdate,
)
from app.services import voice_service
from app.services.job_events import _publish

router = APIRouter(prefix="/api/voices", tags=["voices"])


@router.get("", response_model=list[VoiceOut])
def list_voices(db: Session = Depends(get_db)):
    return db.query(Voice).order_by(Voice.name).all()


@router.get("/{voice_id}", response_model=VoiceOut)
def get_voice(voice_id: str, db: Session = Depends(get_db)):
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    return voice


@router.post("", response_model=VoiceOut, status_code=201)
def create_voice(body: VoiceCreate, db: Session = Depends(get_db)):
    data = voice_service.merge_params_into_config(body.model_dump(exclude_unset=True), None)
    voice = Voice(**data)
    db.add(voice)
    db.commit()
    db.refresh(voice)
    return voice


@router.put("/{voice_id}", response_model=VoiceOut)
def update_voice(voice_id: str, body: VoiceUpdate, db: Session = Depends(get_db)):
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    data = voice_service.merge_params_into_config(
        body.model_dump(exclude_unset=True), voice.config_json
    )
    for k, v in data.items():
        setattr(voice, k, v)
    db.commit()
    db.refresh(voice)
    return voice


@router.delete("/{voice_id}", status_code=204)
def delete_voice(voice_id: str, db: Session = Depends(get_db)):
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    db.delete(voice)
    db.commit()


@router.post("/{voice_id}/test")
def test_voice(voice_id: str, body: VoiceTestRequest, db: Session = Depends(get_db)):
    """合成一句短测试语料, 返回 WAV 供浏览器试听."""
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    cfg, merged_params = voice_service.prepare(voice, body.params)

    # 输出到临时文件以便流式返回 (mktemp 有竞态, NamedTemporaryFile 原子创建)
    _tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    _tf.close()
    tmp = Path(_tf.name)
    try:
        voice_service.synthesize_voice(
            cfg,
            voice_name=voice.name,
            text=body.text,
            output_path=tmp,
            params=merged_params,
        )
        return FileResponse(
            path=tmp,
            media_type="audio/wav",
            filename=f"test_{voice.name}.wav",
            headers={"Content-Disposition": "inline"},
        )
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"TTS test failed: {exc}") from exc


@router.post("/{voice_id}/carnival")
async def carnival_voice_start(
    voice_id: str, body: VoiceCarnivalRequest, db: Session = Depends(get_db)
):
    """疯狂抽卡: 异步启动抽卡任务, 返回 job_id 用于 SSE 进度监听.

    POST 后立即返回 job_id; 客户端连 GET /api/jobs/{job_id}/events 拿进度;
    完成后 GET /api/voices/carnival/{job_id}/download 下载 ZIP.
    """
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    cfg, merged_params = voice_service.prepare(voice, body.params)

    job_id = voice_service.start_carnival(
        cfg,
        voice_name=voice.name,
        text=body.text,
        count=body.count,
        params=merged_params,
        publish=_publish,
    )
    return {"job_id": job_id, "total": body.count}


@router.get("/carnival/{job_id}/download")
def carnival_download(job_id: str):
    """下载抽卡完成的 ZIP 包."""
    job = voice_service.carnival_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["error"]:
        raise HTTPException(status_code=502, detail=f"Carnival failed: {job['error']}")
    if job["zip_bytes"] is None:
        raise HTTPException(status_code=425, detail="Carnival still in progress")
    return Response(
        content=job["zip_bytes"],
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=carnival_{job_id}.zip",
        },
    )


@router.post("/{voice_id}/test-and-anchor")
def test_and_anchor(voice_id: str, body: VoiceTestRequest, db: Session = Depends(get_db)):
    """合成试听音频并直接设为固定参考音锚点.

    生成音频保存到 references/{voice_id}/anchor.wav,
    同时更新 voice.reference_audio_path 和 reference_text.
    返回 WAV 文件供浏览器播放/试听.
    """
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    cfg, merged_params = voice_service.prepare(voice, body.params)
    output_path = voice_service.anchor_path(voice_id)
    try:
        voice_service.synthesize_voice(
            cfg,
            voice_name=voice.name,
            text=body.text,
            output_path=output_path,
            params=merged_params,
            use_reference=False,  # 纯净合成后再锚定
            master_style="calm",
        )
        voice.reference_audio_path = str(output_path)
        voice.reference_text = body.text
        db.commit()
        db.refresh(voice)

        return FileResponse(
            path=output_path,
            media_type="audio/wav",
            filename=f"anchor_{voice.name}.wav",
            headers={"Content-Disposition": "inline"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Anchor TTS failed: {exc}") from exc


@router.post("/{voice_id}/set-reference")
def set_voice_reference(
    voice_id: str, body: VoiceSetReferenceRequest, db: Session = Depends(get_db)
):
    """将用户选中的优质音频片段设为音色的固定参考音锚点.

    参考音 + 准确文本 = Fish Speech "声音锚点" (最可靠的音色稳定方式).
    """
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    audio_path = Path(body.audio_path)
    if not audio_path.exists():
        raise HTTPException(status_code=400, detail=f"Audio file not found: {audio_path}")

    ref_path = voice_service.copy_reference_anchor(voice_id, audio_path)
    voice.reference_audio_path = str(ref_path)
    voice.reference_text = body.audio_text
    db.commit()
    db.refresh(voice)
    return {"status": "ok", "detail": f"参考音锚点已设为: {ref_path}"}


@router.post("/{voice_id}/upload-master")
async def upload_master_audio(
    voice_id: str,
    audio: UploadFile = File(...),
    master_text: str = Form(""),
    db: Session = Depends(get_db),
):
    """上传/更新 IndexTTS2 主音色参考音频 (master tape).

    接收 multipart form-data (audio file + master_text),
    持久化到 references/{voice_id}/master_<ts><suffix>,
    同时更新 voice.master_audio_path (与可选的 master_text).
    """
    voice = db.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")

    ref_dir = voice_service.project_root() / "references" / voice_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(audio.filename or "master.wav").suffix or ".wav"
    dest = ref_dir / f"master_{int(time.time() * 1000)}{suffix}"
    blob = await audio.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty file")
    dest.write_bytes(blob)

    voice.master_audio_path = str(dest)
    if master_text:
        voice.master_text = master_text
    db.commit()
    db.refresh(voice)
    return {
        "status": "ok",
        "master_audio_path": voice.master_audio_path,
        "master_text": voice.master_text,
        "size_bytes": len(blob),
    }
