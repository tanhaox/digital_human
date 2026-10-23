# -*- coding: utf-8 -*-
"""whisper worker 隔离层 — 客户端回退 + 转录分流 + worker 协议 (1023)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.alignment_service import _worker as wk
from app.services.alignment_service._transcribe import _transcribe_words
from app.services.alignment_service._worker import _transcribe_via_worker


def test_client_returns_none_when_worker_missing(monkeypatch):
    """worker 脚本路径不存在 → spawn 失败 → None (fail-open 信号)."""
    monkeypatch.setattr(wk, "_WORKER", wk._ROOT / "scripts" / "_no_such_.py")
    wk._SINGLETON = wk._WorkerProc()  # 重置单例
    assert _transcribe_via_worker(Path("x.wav")) is None


def test_transcribe_words_worker_path(monkeypatch):
    """model=None 且 worker 可用 → WordSegment 列表, 不触碰 _ensure_model."""
    segs = [{"text": " 你做出的东西有人用 ", "start": 0.1, "end": 2.2},
            {"text": "", "start": 3.0, "end": 4.0}]
    monkeypatch.setattr(
        "app.services.alignment_service._transcribe._transcribe_via_worker",
        lambda *a, **k: segs)
    words = _transcribe_words(Path("a.wav"))
    assert len(words) == 1
    assert words[0].text == "你做出的东西有人用"
    assert words[0].start == 0.1 and words[0].end == 2.2


def test_transcribe_words_falls_back_inprocess(monkeypatch):
    """worker 返回 None → 回退进程内路径 (此处桩掉 _ensure_model 防真加载)."""
    monkeypatch.setattr(
        "app.services.alignment_service._transcribe._transcribe_via_worker",
        lambda *a, **k: None)

    class _Stub:
        def transcribe(self, *a, **k):
            class _S:
                text = "回退路径"
                start, end = 0.0, 1.0
            return [_S()], None

    monkeypatch.setattr(
        "app.services.alignment_service._transcribe._ensure_model",
        lambda *a, **k: _Stub())
    words = _transcribe_words(Path("a.wav"))
    assert len(words) == 1 and words[0].text == "回退路径"


def test_worker_script_syntax_and_idle_arg():
    """worker 脚本可编译 (语法级冒烟 — 真模型加载不上 CI)."""
    import py_compile
    py_compile.compile(str(wk._WORKER), doraise=True)


def test_worker_protocol_roundtrip_shape():
    """协议形状: 请求可序列化, 响应字段齐 (worker 端形状以文档为准的锚测)."""
    req = {"audio": "a.wav", "language": "zh", "vad_filter": False,
           "initial_prompt": "以下是逐字转写…", "size": None, "device": None}
    line = json.dumps(req, ensure_ascii=False)
    assert json.loads(line)["initial_prompt"].startswith("以下是逐字转写")
