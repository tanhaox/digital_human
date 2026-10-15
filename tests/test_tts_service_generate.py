"""Tests for tts_service.TTSService.generate() row-count contract.

Regression guard for the P1 duplicate-row bug found during the 2026-08-01
full-project audit:

- `tts_client.synthesize_lines` already commits one AudioFile row per segment
  via `_manifest_callback` → `progress_callback`.
- Previously `generate()` ALSO re-traversed the manifest at the end and built
  a second set of AudioFile rows + a second progress_callback per segment,
  doubling the rows (85 segments → 171 AudioFile rows in the DB).

These tests pin: exactly N rows / N progress callbacks for N segments, and
each row's filename matches its manifest segment file.

Run:
    cd /d F:\\AI-Agent-Local\\digital_human
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_tts_service_generate.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Put digital_human/ on sys.path so `from app.services.tts_service import ...`
# resolves (conftest.py only adds scripts/).
_DH = Path(__file__).resolve().parents[1]
if str(_DH) not in sys.path:
    sys.path.insert(0, str(_DH))

from app.services import tts_service  # noqa: E402
from app.services.tts_service import TTSService  # noqa: E402


def _fake_voice() -> SimpleNamespace:
    return SimpleNamespace(
        backend=None,
        name="test-voice",
        reference_audio_path=None,
        reference_text="",
        config_json=None,
        master_audio_path=None,
        master_text="",
        base_url_fish=None,
        base_url_f5=None,
        base_url_indextts=None,
    )


def _fake_defaults() -> SimpleNamespace:
    return SimpleNamespace(
        backend="fish",
        voice_id="default",
        base_url_fish="http://127.0.0.1:10032",
        base_url_f5="http://127.0.0.1:7000",
        base_url_indextts="http://127.0.0.1:7862",
    )


def _fake_segments(n: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=f"seg-{i}", text=f"第{i}句测试文本") for i in range(n)]


def _fake_job(tmp_path: Path, n: int) -> SimpleNamespace:
    return SimpleNamespace(
        id="job-rowcount",
        output_dir=str(tmp_path / "audio"),
        total_segments=n,
    )


def _fake_manifest(n: int, tmp_path: Path) -> dict:
    segs = []
    for i in range(n):
        segs.append(
            {
                "index": i,
                "file": f"{i:03d}.wav",
                "duration": 1.5,
                "sample_rate": 24000,
                "text": f"第{i}句测试文本",
                "inference_text": f"第{i}句测试文本",
            }
        )
    return {"segments": segs, "segment_count": n}


@pytest.fixture()
def _monkey_synth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Stub tts_client.synthesize_lines to return an N-segment manifest."""

    def _make(n: int):
        calls = {"count": 0}

        def fake_synth_lines(text, output_dir, **kwargs):
            calls["count"] += 1
            # Mirror the real synthesize_lines contract: it calls
            # progress_callback(completed, total, text, seg_dict) once per
            # completed segment, and returns the manifest.
            pc = kwargs.get("progress_callback")
            manifest = _fake_manifest(n, tmp_path)
            for i, seg in enumerate(manifest["segments"], start=1):
                if pc:
                    pc(i, n, seg["text"], seg)
            return manifest

        monkeypatch.setattr(tts_service.tts_client, "synthesize_lines", fake_synth_lines)
        return calls

    return _make


def test_generate_creates_exactly_one_row_per_segment(_monkey_synth, tmp_path):
    """N segments → exactly N AudioFile rows, N progress callbacks, no dups."""
    _monkey_synth(3)
    job = _fake_job(tmp_path, 3)
    segments = _fake_segments(3)

    progress_rows: list = []
    result = TTSService(_fake_defaults()).generate(
        job, segments, _fake_voice(),
        progress_callback=lambda c, t, text, af: progress_rows.append(af),
    )

    afs = result["audio_files"]
    assert len(afs) == 3, f"expected 3 AudioFile rows, got {len(afs)}"
    assert len(progress_rows) == 3, f"expected 3 progress callbacks, got {len(progress_rows)}"

    # No duplicate rows / duplicate callbacks: filenames all distinct.
    filenames = [af.filename for af in afs]
    assert len(set(filenames)) == 3, f"duplicate AudioFile rows: {filenames}"
    progress_filenames = [af.filename for af in progress_rows]
    assert len(set(progress_filenames)) == 3, f"duplicate progress callbacks: {progress_filenames}"

    # Row filenames match manifest segment files and segment order.
    for i, af in enumerate(afs):
        assert af.filename == f"{i:03d}.wav", f"row {i} filename mismatch: {af.filename}"
        assert af.segment_id == f"seg-{i}", f"row {i} segment_id mismatch: {af.segment_id}"
        assert af.duration == 1.5


def test_generate_progress_order_matches_segments(_monkey_synth, tmp_path):
    """progress_callback fires in segment order with correct completed count."""
    _monkey_synth(4)
    job = _fake_job(tmp_path, 4)
    segments = _fake_segments(4)

    seen: list[tuple[int, str]] = []
    TTSService(_fake_defaults()).generate(
        job, segments, _fake_voice(),
        progress_callback=lambda c, t, text, af: seen.append((c, af.filename)),
    )

    assert [c for c, _ in seen] == [1, 2, 3, 4], f"completed counts out of order: {seen}"
    assert [f for _, f in seen] == ["000.wav", "001.wav", "002.wav", "003.wav"], f"order mismatch: {seen}"


def test_generate_zero_segments_is_safe(_monkey_synth, tmp_path):
    """Empty manifest → zero rows, zero callbacks, no crash (defensive)."""
    _monkey_synth(0)
    job = _fake_job(tmp_path, 0)
    segments: list = []

    progress_rows: list = []
    result = TTSService(_fake_defaults()).generate(
        job, segments, _fake_voice(),
        progress_callback=lambda c, t, text, af: progress_rows.append(af),
    )

    assert result["audio_files"] == []
    assert progress_rows == []
