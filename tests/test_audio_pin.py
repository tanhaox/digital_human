# -*- coding: utf-8 -*-
"""1101 U8 批1 音频钉选 (验收采用): 钉选优先/失效回退/错误分型/无manifest报错 (零 GPU)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

import sqlalchemy
import sqlalchemy.orm

from app.database import Base
from app.models import AudioFile, AudioJob, Episode, Script, Segment
from app.services import anim_draft
from app.services.anim_draft import AnimNotFound


def _db(tmp: Path):
    eng = sqlalchemy.create_engine(f"sqlite:///{tmp / 't.db'}")
    Base.metadata.create_all(eng)
    sm = sqlalchemy.orm.sessionmaker(bind=eng)
    return sm


def _seed(sm):
    from datetime import datetime
    with sm() as db:
        sc = Script(id="sc1", article_id="a1", script_text="稿")
        ep = Episode(id="ep1", book_id="b1", ep_index=3, script_id="sc1")
        j1 = AudioJob(id="j1", script_id="sc1", status="completed",
                      output_dir=str(_OUT), created_at=datetime(2026, 11, 1, 10, 0, 0))
        j2 = AudioJob(id="j2", script_id="sc1", status="completed",
                      output_dir=str(_OUT), created_at=datetime(2026, 11, 1, 11, 0, 0))
        db.add_all([sc, ep, j1, j2])
        for name, text in (("001.wav", "第一包"), ("002.wav", "第二包")):
            p = _OUT / name
            p.write_bytes(b"f")
            db.add(AudioFile(id=f"af{name}", audio_job_id="j1", filename=name,
                             file_path=str(p), segment_id="s1", duration=2.0))
            db.add(AudioFile(id=f"bf{name}", audio_job_id="j2", filename=name,
                             file_path=str(p), segment_id="s1", duration=2.0))
        db.add(Segment(id="s1", script_id="sc1", line_index=0, text="第一包"))
        db.commit()
        return ep.id


_OUT = Path(tempfile.mkdtemp())
(_OUT / "manifest.json").write_text(
    '{"segments": [{"file": "001.wav", "text": "第一包", "duration": 2.0},'
    ' {"file": "002.wav", "text": "第二包", "duration": 2.0}]}',
    encoding="utf-8")


def test_unpinned_uses_latest(monkeypatch, tmp_path):
    sm = _db(tmp_path)
    _seed(sm)
    monkeypatch.setattr(anim_draft, "get_session_maker", lambda: sm)
    r = anim_draft.fetch_episode_audio("b1", 3)
    assert r["job_id"] == "j2" and r["pinned"] is False and r["pinned_lost"] is False


def test_pinned_wins_over_latest(monkeypatch, tmp_path):
    sm = _db(tmp_path)
    ep_id = _seed(sm)
    with sm() as db:
        db.get(Episode, ep_id).audio_job_id = "j1"   # 钉旧版
        db.commit()
    monkeypatch.setattr(anim_draft, "get_session_maker", lambda: sm)
    r = anim_draft.fetch_episode_audio("b1", 3)
    assert r["job_id"] == "j1" and r["pinned"] is True   # 钉选压过更新的 j2


def test_pinned_lost_falls_back(monkeypatch, tmp_path):
    sm = _db(tmp_path)
    ep_id = _seed(sm)
    with sm() as db:
        db.get(Episode, ep_id).audio_job_id = "jX"       # 钉的 job 不存在 (被级联删)
        db.commit()
    monkeypatch.setattr(anim_draft, "get_session_maker", lambda: sm)
    r = anim_draft.fetch_episode_audio("b1", 3)
    assert r["job_id"] == "j2" and r["pinned_lost"] is True and r["pinned"] is False


def test_error_typing_no_script(monkeypatch, tmp_path):
    sm = _db(tmp_path)
    ep_id = _seed(sm)
    with sm() as db:
        db.get(Episode, ep_id).script_id = None          # 🔊force 重建后
        db.commit()
    monkeypatch.setattr(anim_draft, "get_session_maker", lambda: sm)
    try:
        anim_draft.fetch_episode_audio("b1", 3)
        raise AssertionError("应抛 AnimNotFound")
    except AnimNotFound as e:
        assert "重建音频工位" in str(e)                   # A4 分型文案


def test_no_manifest_raises_clear(monkeypatch, tmp_path):
    sm = _db(tmp_path)
    _seed(sm)
    (_OUT / "manifest.json").unlink()
    monkeypatch.setattr(anim_draft, "get_session_maker", lambda: sm)
    try:
        anim_draft.fetch_episode_audio("b1", 3)
        raise AssertionError("应抛 AnimNotFound")
    except AnimNotFound as e:
        assert "史前" in str(e) or "manifest" in str(e)   # D9: 行级回退已删
    finally:
        _OUT.joinpath("manifest.json").write_text("{}", encoding="utf-8")
