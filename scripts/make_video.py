"""Digital human news anchor video assembler.

Pipeline (M0 minimal):
    script.json → TTS audio → static host image video with title / points / subtitles → MP4

Future stage (M1):
    Replace the static host image with the talking-head video exported from the LTX23 ComfyUI workflow.

Usage:
    "E:/AI/tts/fish-speech/.venv/Scripts/python.exe" scripts/make_video.py scripts/example_script.json

Requirements:
    - Fish Speech API server running on http://127.0.0.1:7860
    - FFmpeg in PATH
    - A Chinese font (auto-detects msyh / simhei / simsun)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
AUDIO_DIR = OUTPUTS_DIR / "audio"
VIDEO_DIR = OUTPUTS_DIR / "video"
TTS_CLIENT = PROJECT_ROOT / "scripts" / "tts_client.py"
DEFAULT_FISH_URL = "http://127.0.0.1:7860"

FONT_CANDIDATES = [
    Path(r"C:/Windows/Fonts/msyh.ttc"),   # Microsoft YaHei
    Path(r"C:/Windows/Fonts/msyhbd.ttc"),
    Path(r"C:/Windows/Fonts/simhei.ttf"), # SimHei
    Path(r"C:/Windows/Fonts/simsun.ttc"), # SimSun
]

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _find_font() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No suitable Chinese font found. Install or specify with --font.")


def _check_fish_server(url: str = DEFAULT_FISH_URL) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/v1/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _run(cmd: list[str | Path], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a subprocess and raise on failure."""
    cmd_str = [str(c) for c in cmd]
    # Use errors='replace' because Fish Speech logs may contain non-UTF-8 progress chars.
    result = subprocess.run(cmd_str, capture_output=True, text=True, errors="replace", **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd_str)}\nSTDERR:\n{result.stderr}")
    return result


def _audio_duration(path: Path) -> float:
    """Return audio duration in seconds using ffprobe."""
    result = _run([
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        str(path),
    ])
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 0))
    raise RuntimeError(f"Could not determine duration of {path}")


def _tts(text: str, voice_id: str, python_exe: Path, backend: str = "auto", segment: bool = True) -> Path:
    """Call tts_client.py via subprocess to synthesize audio.

    Defaults to segmented generation to bypass Fish Speech long-text encoding issues.
    """
    _ensure_dir(AUDIO_DIR)
    ts = int(os.path.getmtime(TTS_CLIENT)) if TTS_CLIENT.exists() else 0
    out_path = AUDIO_DIR / f"{voice_id}_{ts}.wav"
    # Write text to a UTF-8 file to avoid Windows shell encoding mangling Chinese.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        text_file = Path(f.name)
    try:
        cmd = [
            str(python_exe),
            str(TTS_CLIENT),
            "--text", "@" + str(text_file),
            "--output", str(out_path),
            "--voice-id", voice_id,
            "--backend", backend,
        ]
        if segment:
            cmd.append("--segment")
        _run(cmd, encoding="utf-8")
    finally:
        text_file.unlink(missing_ok=True)
    if not out_path.exists():
        raise RuntimeError("TTS client did not produce output file")
    return out_path


def _normalize_script(script: dict) -> dict:
    """Ensure required script keys exist."""
    title = script.get("title", "")
    hook = script.get("hook", "")
    points = script.get("points", [])
    cta = script.get("cta", "")
    if not title and not hook:
        raise ValueError("script must contain 'title' or 'hook'")
    full_text = " ".join(filter(None, [hook, *points, cta]))
    return {
        "title": title,
        "hook": hook,
        "points": points,
        "cta": cta,
        "full_text": full_text,
        "host_image": Path(script.get("host_image", "")),
        "voice_id": script.get("voice_id", "default"),
    }


def _split_segments(script: dict, duration: float) -> list[dict]:
    """Split duration into subtitle segments: hook, points, cta."""
    sentences: list[str] = []
    if script["hook"]:
        sentences.append(script["hook"])
    sentences.extend(script["points"])
    if script["cta"]:
        sentences.append(script["cta"])

    n = len(sentences)
    if n == 0:
        return []

    # Simple uniform allocation with a small gap between segments.
    gap = 0.15
    available = max(0.0, duration - gap * (n - 1))
    seg_duration = available / n

    segments = []
    t = 0.0
    for sentence in sentences:
        end = min(t + seg_duration, duration)
        segments.append({"text": sentence, "start": t, "end": end})
        t = end + gap
    return segments


def _escape_drawtext(s: str) -> str:
    """Escape characters for FFmpeg drawtext text expression."""
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace("%", "\\%")
    s = s.replace("\n", "\n")
    return s


def _build_filter_complex(
    script: dict,
    segments: list[dict],
    duration: float,
    font_path: Path,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Build an FFmpeg filter_complex that draws title, points and subtitles."""
    title = _escape_drawtext(script["title"])

    filters: list[str] = []
    # Scale/crop host image to 9:16, force yuv420p at the end.
    filters.append(
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=crop,setsar=1[bg]"
    )

    last = "bg"

    # Title bar (top center, visible entire video)
    if title:
        filters.append(
            f"[{last}]drawtext=fontfile='{font_path.as_posix()}':"
            f"text='{title}':x=(w-text_w)/2:y=80:fontsize=64:fontcolor=white:"
            f"box=1:boxcolor=black@0.55:boxborderw=12:enable='between(t\\,0\\,{duration})'[v_title]"
        )
        last = "v_title"

    # Key points (one per segment, except the last CTA segment)
    point_segments = [s for s in segments if s["text"] in script["points"]]
    for idx, seg in enumerate(point_segments, start=1):
        txt = _escape_drawtext(f"{idx}. {seg['text']}")
        start, end = seg["start"], seg["end"]
        filters.append(
            f"[{last}]drawtext=fontfile='{font_path.as_posix()}':"
            f"text='{txt}':x=70:y=340:fontsize=52:fontcolor=white:"
            f"box=1:boxcolor=#1a1a1a@0.7:boxborderw=16:line_spacing=12:"
            f"enable='between(t\\,{start:.2f}\\,{end:.2f})'[v_pt{idx}]"
        )
        last = f"v_pt{idx}"

    # Subtitles (current sentence at bottom)
    for idx, seg in enumerate(segments):
        txt = _escape_drawtext(seg["text"])
        start, end = seg["start"], seg["end"]
        filters.append(
            f"[{last}]drawtext=fontfile='{font_path.as_posix()}':"
            f"text='{txt}':x=(w-text_w)/2:y=h-text_h-120:fontsize=58:fontcolor=white:"
            f"borderw=4:bordercolor=black@0.6:"
            f"enable='between(t\\,{start:.2f}\\,{end:.2f})'[v_sub{idx}]"
        )
        last = f"v_sub{idx}"

    # Final format
    filters.append(f"[{last}]format=yuv420p[vout]")
    return ";\n".join(filters)


def make_video(
    script_path: Path,
    output_path: Path | None = None,
    python_exe: Path = Path("python"),
    backend: str = "auto",
    font_path: Path | None = None,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Assemble a 9:16 news anchor video from script + TTS + host image."""
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    script = _normalize_script(script)

    if not script["host_image"].exists():
        raise FileNotFoundError(f"Host image not found: {script['host_image']}")

    font = Path(font_path) if font_path else _find_font()

    if not _check_fish_server():
        print(
            "WARNING: Fish Speech server not responding at http://127.0.0.1:7860/v1/health.\n"
            "Start it with:\n"
            '  "E:/AI/tts/fish-speech/.venv/Scripts/python.exe" '
            '"E:/AI/tts/fish-speech/tools/api_server.py" '
            '--listen 127.0.0.1:7860 --llama-checkpoint-path "E:/AI/tts/models/s2-pro" '
            '--decoder-checkpoint-path "E:/AI/tts/models/s2-pro/codec.pth" '
            '--decoder-config-name modded_dac_vq --device cuda',
            file=sys.stderr,
        )

    # 1) TTS
    audio_path = _tts(script["full_text"], script["voice_id"], python_exe, backend)
    duration = _audio_duration(audio_path)
    print(f"Audio: {audio_path} ({duration:.2f}s)")

    # 2) Build filter graph
    segments = _split_segments(script, duration)
    filter_complex = _build_filter_complex(script, segments, duration, font, width, height)

    # 3) Render
    _ensure_dir(VIDEO_DIR)
    if output_path is None:
        safe_title = re.sub(r"[^\w\u4e00-\u9fff]+", "_", script["title"]).strip("_") or "video"
        output_path = VIDEO_DIR / f"{safe_title}_{int(duration)}s.mp4"
    output_path = Path(output_path)
    _ensure_dir(output_path.parent)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(filter_complex)
        filter_script = Path(f.name)

    try:
        _run([
            "ffmpeg",
            "-y",
            "-loop", "1",
            "-i", str(script["host_image"]),
            "-i", str(audio_path),
            "-/filter_complex", str(filter_script),
            "-map", "[vout]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(output_path),
        ])
    finally:
        filter_script.unlink(missing_ok=True)

    print(f"Video: {output_path}")
    return output_path


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble digital human news video")
    parser.add_argument("script", type=Path, help="Path to script.json")
    parser.add_argument("--output", "-o", type=Path, help="Output MP4 path")
    parser.add_argument("--python", type=Path, default=Path("python"), help="Python executable for tts_client.py")
    parser.add_argument("--backend", choices=["fish", "f5", "auto"], default="auto")
    parser.add_argument("--font", type=Path, help="Path to TrueType/Collection font")
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    args = parser.parse_args()

    try:
        make_video(
            script_path=args.script,
            output_path=args.output,
            python_exe=args.python,
            backend=args.backend,
            font_path=args.font,
            width=args.width,
            height=args.height,
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
