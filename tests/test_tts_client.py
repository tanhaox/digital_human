"""Tests for tts_client.py interface contract.

These tests are designed to FAIL on a regression — they do not pass
silently. Each test pins one specific invariant about the module's
public surface (functions, signatures, CLI args, patch behavior).

Run from anywhere:
    cd /d F:\\AI-Agent-Local\\digital_human
    pytest tests/test_tts_client.py -v

Or with explicit python:
    F:\\...\\hermes-agent\\venv\\Scripts\\python.exe -m pytest tests/test_tts_client.py -v
"""
from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test via the conftest path injection.
import tts_client


# ---------------------------------------------------------------------------
# 1. Module surface — top-level functions exist with the right names
# ---------------------------------------------------------------------------

REQUIRED_FUNCTIONS = [
    "fish_speech_tts",
    "f5_tts",
    "indextts_tts",
    "synthesize",
    "synthesize_lines",
    "_synthesize_single",
]


@pytest.mark.parametrize("name", REQUIRED_FUNCTIONS)
def test_required_function_exists(name: str) -> None:
    """tts_client must expose every backend entry point + public API."""
    assert hasattr(tts_client, name), f"missing function: {name}"
    assert callable(getattr(tts_client, name)), f"{name} is not callable"


# ---------------------------------------------------------------------------
# 2. indextts_tts() signature contract
# ---------------------------------------------------------------------------


def test_indextts_tts_signature_has_master_params() -> None:
    """indextts_tts() must accept master_audio / master_text / master_style.

    The whole point of the IndexTTS2 wiring is that the master tape (the
    reference audio + its transcript + a style preset) is plumbed through
    to the API server. If these parameters disappear, voice cloning is
    broken — that's a hard regression we want to catch.
    """
    sig = inspect.signature(tts_client.indextts_tts)
    params = set(sig.parameters.keys())
    for required in ("text", "output_path", "master_audio", "master_text", "master_style"):
        assert required in params, (
            f"indextts_tts() missing required param: {required}. "
            f"Current params: {sorted(params)}"
        )


def test_indextts_tts_rejects_missing_master_audio() -> None:
    """indextts_tts() must raise RuntimeError when master_audio is None or missing.

    This is the guard from line 561-563 of tts_client.py — it prevents
    accidental zero-shot synthesis with no voice anchor.
    """
    out = Path(temp_path := (Path.cwd() / "_indextts_no_master.wav"))
    try:
        with pytest.raises(RuntimeError, match="master_audio"):
            tts_client.indextts_tts(
                text="test",
                output_path=out,
                master_audio=None,
            )
    finally:
        if out.exists():
            out.unlink()


def test_indextts_tts_rejects_missing_master_audio_file() -> None:
    """indextts_tts() must raise RuntimeError when the master_audio path doesn't exist."""
    out = Path(temp_path := (Path.cwd() / "_indextts_missing_master.wav"))
    try:
        with pytest.raises(RuntimeError, match="master_audio"):
            tts_client.indextts_tts(
                text="test",
                output_path=out,
                master_audio=Path("Z:/does/not/exist/anywhere.wav"),
            )
    finally:
        if out.exists():
            out.unlink()


# ---------------------------------------------------------------------------
# 3. synthesize() threads master_* parameters through to _synthesize_single
# ---------------------------------------------------------------------------


def test_synthesize_threads_master_params_to_synthesize_single() -> None:
    """synthesize() must accept and forward master_audio/master_text/master_style.

    The patch we applied (2026-07-27) added these kwargs to synthesize() and
    to _synthesize_single(). If a future refactor drops them, the public
    API silently breaks and the offline batch run fails at runtime — this
    test catches the contract break at unit-test time.
    """
    synth_sig = inspect.signature(tts_client.synthesize)
    synth_params = set(synth_sig.parameters.keys())
    for required in ("master_audio", "master_text", "master_style"):
        assert required in synth_params, (
            f"synthesize() missing required param: {required}"
        )

    single_sig = inspect.signature(tts_client._synthesize_single)
    single_params = set(single_sig.parameters.keys())
    for required in ("master_audio", "master_text", "master_style"):
        assert required in single_params, (
            f"_synthesize_single() missing required param: {required}"
        )


def test_synthesize_lines_threads_master_params() -> None:
    """synthesize_lines() must also accept master_* for the batched path."""
    sig = inspect.signature(tts_client.synthesize_lines)
    params = set(sig.parameters.keys())
    for required in ("master_audio", "master_text", "master_style"):
        assert required in params, (
            f"synthesize_lines() missing required param: {required}"
        )


# ---------------------------------------------------------------------------
# 4. CLI exposes --backend indextts and the master_* args
# ---------------------------------------------------------------------------


_CLI_HELP_CACHE: dict[str, str] = {}


def _cli_help() -> str:
    """Run `tts_client.py --help` and cache the output."""
    if "help" not in _CLI_HELP_CACHE:
        tts_client_path = Path(tts_client.__file__).resolve()
        # Prefer the venv python that's already running this test.
        py = Path(sys.executable)
        result = subprocess.run(
            [str(py), str(tts_client_path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        _CLI_HELP_CACHE["help"] = result.stdout + result.stderr
    return _CLI_HELP_CACHE["help"]


def test_cli_backend_includes_indextts() -> None:
    """`--backend` choices must include 'indextts'.

    The patch (2026-07-27) added 'indextts' to the choices list. If a
    future change narrows the choices back to (fish, f5, auto), this test
    fails — pinning the public CLI surface.
    """
    help_text = _cli_help()
    assert "--backend" in help_text, "--backend flag missing from CLI help"
    # argparse renders choices as: {fish,f5,indextts,auto}
    assert "indextts" in help_text.split("--backend")[1].split("}")[0], (
        "--backend choices do not include 'indextts'. "
        f"Help snippet: {help_text.split('--backend')[1][:200]}"
    )


@pytest.mark.parametrize(
    "flag",
    ["--master-audio MASTER_AUDIO", "--master-text MASTER_TEXT", "--master-style {calm,excited,relaxed}"],
)
def test_cli_exposes_master_flags(flag: str) -> None:
    """The three master_* CLI flags must be present with the expected metavar."""
    help_text = _cli_help()
    assert flag in help_text, f"missing CLI flag: {flag}"


# ---------------------------------------------------------------------------
# 5. Backend literal includes 'indextts' (the type-annotated choice)
# ---------------------------------------------------------------------------


def test_backend_literal_includes_indextts() -> None:
    """The backend Literal type must include 'indextts' to keep type-checkers honest."""
    source = inspect.getsource(tts_client._synthesize_single)
    # The annotation `Literal["fish", "f5", "indextts", "auto"]` lives in
    # the signature. Look for the literal set.
    assert '"indextts"' in source, (
        "Literal['indextts'] missing from _synthesize_single's backend annotation. "
        "The patch is gone or someone narrowed the type."
    )


# ---------------------------------------------------------------------------
# 6. synthesize() DefaultOuputDir / default URL sanity (catches drift)
# ---------------------------------------------------------------------------


def test_default_indextts_url_is_7862() -> None:
    """DEFAULT_INDEXTTS_URL must remain http://127.0.0.1:7862 — pinned by memory."""
    assert tts_client.DEFAULT_INDEXTTS_URL == "http://127.0.0.1:7862", (
        f"DEFAULT_INDEXTTS_URL drifted to {tts_client.DEFAULT_INDEXTTS_URL}"
    )


def test_default_output_dir_under_digital_human() -> None:
    """DEFAULT_OUTPUT_DIR must point under the digital_human outputs tree.

    This is the contract — if it silently points elsewhere (e.g. /tmp),
    callers writing to outputs/audio/ break.
    """
    out = Path(tts_client.DEFAULT_OUTPUT_DIR)
    # Allow either absolute Windows path under digital_human OR a Path-like
    # anchored at the scripts parent. Tolerate both forms.
    assert "digital_human" in str(out).replace("/", "\\"), (
        f"DEFAULT_OUTPUT_DIR drifted away from digital_human: {out}"
    )