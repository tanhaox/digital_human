"""Voice TTS service — 参数装配 + 合成调用 + 抽卡任务 + 参考音锚点.
从 `app/routers/voices.py` 下沉的纯逻辑 (不依赖 FastAPI router).
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

from app.models import Voice
from app.services.gpu_service_manager import get_gpu_service_manager

__all__ = [
    "CARNIVAL_STORE", "TTSBackendConfig", "merge_params", "merge_params_into_config",
    "ensure_master_for_indextts", "prepare", "project_root", "anchor_path",
    "synthesize_voice", "start_carnival", "carnival_status", "copy_reference_anchor",
]

# 默认后端服务地址 (与前端约定一致)
DEFAULT_BASE_URLS = {"fish": "http://127.0.0.1:7860", "f5": "http://127.0.0.1:7861", "indextts": "http://127.0.0.1:7862"}

MASTER_STYLES = ("calm", "excited", "relaxed")

# 抽卡任务内存 store: job_id -> {"zip_bytes": bytes | None, "error": str | None}
CARNIVAL_STORE: dict[str, dict[str, Any]] = {}


class TTSBackendConfig:
    """一次 TTS 合成所需的全部后端参数 (从 Voice + 请求 body 装配)."""

    def __init__(self, voice: Voice, params: Any | None) -> None:
        self.backend = voice.backend or "fish"
        self.base_url_fish = voice.base_url_fish or DEFAULT_BASE_URLS["fish"]
        self.base_url_f5 = voice.base_url_f5 or DEFAULT_BASE_URLS["f5"]
        self.base_url_indextts = voice.base_url_indextts or DEFAULT_BASE_URLS["indextts"]
        self.ref_audio = Path(voice.reference_audio_path) if voice.reference_audio_path else None
        self.ref_text = voice.reference_text or ""
        self.master_audio = Path(voice.master_audio_path) if voice.master_audio_path else self.ref_audio
        self.master_text = voice.master_text or self.ref_text
        self.master_style = "calm"
        ms = getattr(params, "master_style", None) if params is not None else None
        if isinstance(ms, str) and ms in MASTER_STYLES:
            self.master_style = ms

    @property
    def reference_audio_kwarg(self) -> Path | None:
        """合成时传给 tts_client 的 reference_audio (仅当文件存在)."""
        return self.master_audio if self.master_audio and self.master_audio.exists() else None


def project_root() -> Path:
    """项目根目录 (digital_human/), 并确保 scripts/tts_client 可导入."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def merge_params(voice: Voice, body_params: Any | None) -> dict[str, Any]:
    """合并 config_json 中已保存的 params 与请求 body.params (请求覆盖)."""
    merged: dict[str, Any] = {}
    if voice.config_json and isinstance(voice.config_json, dict):
        saved = voice.config_json.get("params")
        if saved and isinstance(saved, dict):
            merged.update(saved)
    if body_params is not None:
        merged.update(body_params.model_dump(exclude_unset=True))
    return merged


def merge_params_into_config(
    data: dict[str, Any], existing_config: dict[str, Any] | None
) -> dict[str, Any]:
    """把 data['params'] 合并进 config_json (create/update 共用), 返回 data.

    data 由请求 body.model_dump(exclude_unset=True) 得到.
    """
    params = data.pop("params", None)
    if not params:
        return data
    base = existing_config or data.get("config_json") or {}
    config_json = dict(base) if isinstance(base, dict) else {}
    config_json["params"] = params
    data["config_json"] = config_json
    return data


def prepare(
    voice: Voice, body_params: Any | None
) -> tuple["TTSBackendConfig", dict[str, Any]]:
    """装配 TTS 参数 + 预检 indextts + 合并 params (三个合成端点共用)."""
    cfg = TTSBackendConfig(voice, body_params)
    ensure_master_for_indextts(cfg)
    return cfg, merge_params(voice, body_params)


def ensure_master_for_indextts(cfg: TTSBackendConfig) -> None:
    """indextts 后端需要存在主音色音频; 缺失时抛 ValueError (含中文指引)."""
    if cfg.backend == "indextts" and (not cfg.master_audio or not cfg.master_audio.exists()):
        raise ValueError(
            "Backend=indextts 需要主音色音频 (master_audio_path)。请在编辑面板上传 master audio,或先填写 reference_audio_path 作为兜底。"
        )


def anchor_path(voice_id: str) -> Path:
    """返回 references/<voice_id>/anchor.wav (父目录确保存在)."""
    ref_dir = project_root() / "references" / voice_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    return ref_dir / "anchor.wav"


def _tts_synthesize(
    cfg: TTSBackendConfig,
    voice_name: str,
    text: str,
    output_path: Path,
    params: dict[str, Any] | None,
    use_reference: bool,
    master_style: str | None,
) -> None:
    """底层合成调用 (不管理 GPU session; 由调用方决定锁范围)."""
    from scripts import tts_client

    reference_audio = cfg.reference_audio_kwarg if use_reference else None
    reference_text = cfg.master_text if use_reference else ""
    tts_client.synthesize(
        text=text,
        output_path=output_path,
        backend=cfg.backend,
        voice_id=voice_name,
        reference_audio=reference_audio,
        reference_text=reference_text,
        base_url_fish=cfg.base_url_fish,
        base_url_f5=cfg.base_url_f5,
        base_url_indextts=cfg.base_url_indextts,
        master_audio=cfg.master_audio,
        master_text=cfg.master_text,
        master_style=master_style or cfg.master_style,
        params=params or None,
    )


def synthesize_voice(
    cfg: TTSBackendConfig,
    voice_name: str,
    text: str,
    output_path: Path,
    params: dict[str, Any] | None,
    status_callback: Callable[[str], None] | None = None,
    use_reference: bool = True,
    master_style: str | None = None,
) -> None:
    """GPU 锁内调用 tts_client.synthesize (单条合成入口).

    use_reference=False 时跳过参考音; master_style 显式覆盖 (test-and-anchor 固定 calm).
    """
    project_root()
    with get_gpu_service_manager().session(cfg.backend, status_callback=status_callback):
        _tts_synthesize(
            cfg, voice_name, text, output_path, params, use_reference, master_style
        )


def _pack_carnival_zip(tmp_dir: Path, count: int) -> bytes:
    """把 version_*.wav 打包成 ZIP 字节串."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(count):
            wav_path = tmp_dir / f"version_{i + 1:02d}.wav"
            if wav_path.exists():
                zf.write(wav_path, f"version_{i + 1:02d}.wav")
    return buf.getvalue()


def _run_carnival_worker(
    cfg: TTSBackendConfig,
    voice_name: str,
    text: str,
    count: int,
    params: dict[str, Any],
    job_id: str,
    publish: Callable[[str, dict[str, Any]], None],
) -> None:
    """后台线程入口: 一次 GPU session 内生成全部版本 → 打包 ZIP → 广播进度."""

    def _svc_notify(message: str) -> None:
        publish(job_id, {"type": "carnival_service", "message": message})

    tmp_dir = Path(tempfile.mkdtemp(suffix="_carnival"))
    try:
        with get_gpu_service_manager().session(cfg.backend, status_callback=_svc_notify):
            for i in range(count):
                version_params = dict(params) if params else {}
                version_params["seed"] = i + 1
                out_path = tmp_dir / f"version_{i + 1:02d}.wav"
                _tts_synthesize(
                    cfg, voice_name, text, out_path, version_params, True, None
                )
                publish(
                    job_id,
                    {"type": "carnival_progress", "current": i + 1, "total": count, "seed": i + 1},
                )

        CARNIVAL_STORE[job_id]["zip_bytes"] = _pack_carnival_zip(tmp_dir, count)
        publish(job_id, {"type": "carnival_done", "job_id": job_id, "total": count})
    except Exception as exc:  # noqa: BLE001
        CARNIVAL_STORE[job_id]["error"] = str(exc)
        publish(job_id, {"type": "carnival_error", "error": str(exc)})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def start_carnival(
    cfg: TTSBackendConfig,
    voice_name: str,
    text: str,
    count: int,
    params: dict[str, Any],
    publish: Callable[[str, dict[str, Any]], None],
) -> str:
    """生成 job_id → 初始化 store 条目 → 起 daemon 线程, 返回 job_id."""
    job_id = str(uuid.uuid4())[:8]
    CARNIVAL_STORE[job_id] = {"zip_bytes": None, "error": None}
    threading.Thread(
        target=_run_carnival_worker,
        args=(cfg, voice_name, text, count, params, job_id, publish),
        daemon=True,
    ).start()
    return job_id


def carnival_status(job_id: str) -> dict[str, Any] | None:
    """抽卡任务状态 (未找到返回 None)."""
    return CARNIVAL_STORE.get(job_id)


def copy_reference_anchor(voice_id: str, src: Path) -> Path:
    """把 src 复制为 references/<voice_id>/anchor.wav, 返回目标路径."""
    dest = anchor_path(voice_id)
    shutil.copy2(str(src), str(dest))
    return dest
