"""分段合成: 长文本切段 → 逐段合成 → FFmpeg 拼接.

与 orchestrator.py 解耦 (通过 synth_fn 注入), 避免 orchestrator ↔ segments
循环导入。行为与旧 tts_client.py 的 segment 路径逐字一致。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .audio import _concat_wavs_with_ffmpeg, _ensure_dir
from .text import _split_text

__all__ = ["_synthesize_segments"]


def _synthesize_segments(
    synth_fn: Callable[..., Path],
    text: str,
    output_path: Path,
    segment_max_chars: int,
    backend: str,
    voice_id: str,
    reference_audio: Path | None,
    reference_text: str,
    base_url_fish: str,
    base_url_f5: str,
    base_url_indextts: str,
    master_audio: Path | None,
    master_text: str,
    master_style: str,
    params: dict[str, Any] | None,
) -> Path:
    """Split long text into segments, synthesize each, then concat with FFmpeg."""
    segments = _split_text(text, max_chars=segment_max_chars)
    if not segments:
        raise ValueError("No text to synthesize after segmentation")

    segment_paths: list[Path] = []
    tmp_dir = _ensure_dir(output_path.parent / ".segments")
    try:
        for idx, seg in enumerate(segments):
            seg_path = tmp_dir / f"{output_path.stem}_{idx:03d}.wav"
            synth_fn(
                text=seg, output_path=seg_path, backend=backend, voice_id=voice_id,
                reference_audio=reference_audio, reference_text=reference_text,
                base_url_fish=base_url_fish, base_url_f5=base_url_f5,
                base_url_indextts=base_url_indextts, master_audio=master_audio,
                master_text=master_text, master_style=master_style, params=params,
            )
            segment_paths.append(seg_path)
        return _concat_wavs_with_ffmpeg(segment_paths, output_path)
    finally:
        for seg_path in segment_paths:
            seg_path.unlink(missing_ok=True)
