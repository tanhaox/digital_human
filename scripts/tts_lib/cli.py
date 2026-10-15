"""tts_lib CLI: 复刻旧 scripts/tts_client.py 的 argparse 接口 (含 indextts flags)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .constants import DEFAULT_F5_URL, DEFAULT_FISH_URL, DEFAULT_INDEXTTS_URL
from .lines import synthesize_lines
from .orchestrator import synthesize

__all__ = ["main", "_build_parser"]


def main() -> int:
    """CLI 入口: 解析参数, 分发 synthesize/synthesize_lines, 打印产物路径."""
    args = _build_parser().parse_args()
    output = Path(args.output) if args.output else None
    try:
        result = _dispatch(args, _read_text_arg(args.text), output)
        print(result)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """构造与旧 tts_client.py 完全一致的参数解析器."""
    parser = argparse.ArgumentParser(description="TTS client for digital human anchor")
    parser.add_argument("--text", required=True,
                        help="Text to synthesize. If prefixed with '@', read from UTF-8 file path.")
    parser.add_argument("--output", "-o", help="Output WAV path")
    parser.add_argument("--backend", choices=["fish", "f5", "indextts", "auto"], default="auto")
    parser.add_argument("--master-audio", type=Path,
                        help="IndexTTS2 master tape path (required when --backend indextts)")
    parser.add_argument("--master-text", default="",
                        help="IndexTTS2 master tape reference text (required when --backend indextts)")
    parser.add_argument("--master-style", choices=["calm", "excited", "relaxed"], default="calm",
                        help="IndexTTS2 emotion preset")
    parser.add_argument("--voice-id", default="default")
    parser.add_argument("--ref-audio", type=Path, help="Reference audio for voice cloning")
    parser.add_argument("--ref-text", default="", help="Reference text for voice cloning")
    parser.add_argument("--base-url-fish", default=DEFAULT_FISH_URL)
    parser.add_argument("--base-url-f5", default=DEFAULT_F5_URL)
    parser.add_argument("--segment", action="store_true",
                        help="Split long text into short segments and concatenate audio")
    parser.add_argument("--segment-max-chars", type=int, default=120,
                        help="Max chars per segment when --segment")
    parser.add_argument("--output-dir", type=Path,
                        help="Output directory: generate one WAV per non-empty line + manifest.json")
    return parser


def _dispatch(args: argparse.Namespace, text: str, output: Path | None) -> Path:
    """--output-dir → synthesize_lines (返回 manifest 路径), 否则 synthesize."""
    if args.output_dir:
        synthesize_lines(
            text=text, output_dir=Path(args.output_dir), backend=args.backend,
            voice_id=args.voice_id, reference_audio=args.ref_audio,
            reference_text=args.ref_text, base_url_fish=args.base_url_fish,
            base_url_f5=args.base_url_f5,
            base_url_indextts=_opt(args, "base_url_indextts", DEFAULT_INDEXTTS_URL),
            master_audio=_opt(args, "master_audio", None),
            master_text=_opt(args, "master_text", ""),
            master_style=_opt(args, "master_style", "calm"),
        )
        return Path(args.output_dir) / "manifest.json"

    path = synthesize(
        text=text, output_path=output, backend=args.backend,
        voice_id=args.voice_id, reference_audio=args.ref_audio,
        reference_text=args.ref_text, base_url_fish=args.base_url_fish,
        base_url_f5=args.base_url_f5,
        base_url_indextts=_opt(args, "base_url_indextts", DEFAULT_INDEXTTS_URL),
        master_audio=_opt(args, "master_audio", None),
        master_text=_opt(args, "master_text", ""),
        master_style=_opt(args, "master_style", "calm"),
        segment=args.segment, segment_max_chars=args.segment_max_chars,
    )
    return path


def _read_text_arg(text: str) -> str:
    """'@' 前缀 → 读 UTF-8 文件 (剥除 Windows/bash 残留引号), 否则原样返回."""
    if not text.startswith("@"):
        return text
    text_file = text[1:].strip('"').strip("'")
    return Path(text_file).read_text(encoding="utf-8")


def _opt(args: argparse.Namespace, name: str, default: Any) -> Any:
    """防御性读取可选参数 (argparse 未定义时返回 default)."""
    return getattr(args, name, default)


if __name__ == "__main__":
    raise SystemExit(main())
