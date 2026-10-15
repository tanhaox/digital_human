"""素材保留策略整体升级 — 隔离验证脚本 (2026-08-07).

验证清单:
  1. 核心链路: 合成 job → slots/ 保留存在 → retry 单条 → 再 compose 复用其余不重跑
  2. 幂等: 无变化再 compose → 幂等返回, 不动磁盘
  3. 出口: 保留扫描 (completed 超 slot_retention_days) → slots/ 清, 成片留
  4. silence_fallback.wav 纳入合成后清理

全程使用临时 DB + 临时 composition 目录, 不触碰真实数据。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config, set_config
from app.database import init_db, get_session_maker
from app.models import DirectorJob, DirectorSlot
from app.services.composition_service import compose_director_job

FFMPEG = "ffmpeg"
results: list[tuple[str, bool, str]] = []


def gen_video(out: Path, color: str, dur: float = 2.0) -> None:
    """生成统一格式的测试视频 (960x540 h264 24fps + 48kHz 音轨)."""
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=960x540:d={dur}:r=24",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gen_video failed: {r.stderr[:300]}")


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{'通过' if cond else '失败'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    cfg = load_config("config/app.yaml")
    tmp_root = Path(tempfile.mkdtemp(prefix="compose_verify_"))
    new_defaults = replace(
        cfg.defaults,
        composition_output_root=str(tmp_root / "composition"),
        hf_visual_root=str(tmp_root / "hf_visual"),
        director_output_root=str(tmp_root / "director"),
        slot_retention_days=7,
        job_auto_cleanup_days=7,
    )
    set_config(replace(cfg, defaults=new_defaults))

    db_url = f"sqlite:///{(tmp_root / 'test.db').as_posix()}"
    init_db(db_url)

    db = get_session_maker()()
    job_id = "test-retention-0001"
    job = DirectorJob(
        id=job_id, script_id="none", title="verify", status="reviewing",
        video_format="portrait", pipelines=None,
    )
    db.add(job)
    db.flush()
    slots: list[DirectorSlot] = []
    for i in range(3):
        sl = DirectorSlot(
            id=f"slot-{i}", director_job_id=job_id, slot_index=i,
            start_sec=i * 2.0, end_sec=(i + 1) * 2.0, duration_sec=2.0,
            visual_type="broll_local", workflow="broll_local",
            status="completed", output_path=None, text_context=f"seg {i}",
        )
        db.add(sl)
        slots.append(sl)
    db.commit()

    comp_root = tmp_root / "composition" / job_id
    slots_dir = comp_root / "slots"
    for i, sl in enumerate(slots):
        slot_dir = slots_dir / sl.id
        slot_dir.mkdir(parents=True, exist_ok=True)
        out = slot_dir / "segment.mp4"
        gen_video(out, ["0xCC0000", "0x00CC00", "0x0000CC"][i])
        sl.output_path = str(out)
    db.commit()

    print("=== 1. 核心链路: 合成 → slots/ 保留 → retry 单条 → 复用其余 ===")
    res = compose_director_job(db, job_id)
    check("合成成功", res.get("ok") is True, str(res.get("error")))
    final_candidate = comp_root / f"director_{job_id}.mp4"
    check("成片存在", final_candidate.exists())
    check("slots/ 保留存在 (Step7 已删)", slots_dir.exists() and slots_dir.is_dir())
    for i, sl in enumerate(slots):
        check(f"slot {i} 素材保留", Path(sl.output_path).exists())
    check("silence_fallback.wav 已清理", not (comp_root / "silence_fallback.wav").exists())
    check("中间件 concat_list 已清理", not (comp_root / "concat_list.txt").exists())
    check("中间件 with_audio 已清理", not (comp_root / "with_audio.mp4").exists())

    # 记录 mtime (rest time, 需在 compose 完成后)
    rest_mtimes = {i: Path(sl.output_path).stat().st_mtime for i, sl in enumerate(slots) if i != 1}

    print("=== 2. retry 单条 (slot 1) → 再 compose → 其余 slot 不重跑 ===")
    time.sleep(1.2)  # 确保 mtime 可分辨
    slot1_out = Path(slots[1].output_path)
    gen_video(slot1_out, "0x00FFFF")  # 覆盖 slot1 素材 (换素材)
    # 模拟 retry_slot: 刷新 updated_at (onupdate 生效), 状态保持 completed
    slots[1].updated_at = datetime.now(timezone.utc)
    db.commit()
    res2 = compose_director_job(db, job_id)
    check("重合成成功", res2.get("ok") is True, str(res2.get("error")))
    check("成片已更新", final_candidate.exists())
    for i, t0 in rest_mtimes.items():
        t1 = Path(slots[i].output_path).stat().st_mtime
        check(f"slot {i} 未被重跑 (mtime 不变)", t1 == t0, f"mtime {t0}→{t1}")
    check("slots/ 仍保留", slots_dir.exists())

    print("=== 3. 幂等: 无变化再 compose → 幂等返回 ===")
    res3 = compose_director_job(db, job_id)
    check("幂等返回 ok", res3.get("ok") is True)
    # 幂等返回无 error 且 output 指向既有成片
    check("幂等 output=成片", res3.get("output_path") == str(final_candidate))

    print("=== 4. 出口: completed 超 slot_retention_days → 保留扫描清 slots/ 留成片 ===")
    db.query(DirectorJob).filter(DirectorJob.id == job_id).update(
        {"completed_at": datetime.now(timezone.utc) - timedelta(days=10)}
    )
    db.commit()
    from app.main import _auto_cleanup_stale_jobs
    _auto_cleanup_stale_jobs()
    check("slots/ 已被保留扫描清理", not slots_dir.exists())
    check("成片保留", final_candidate.exists())
    check("manifest 保留", (comp_root / "composition_manifest.json").exists())

    # 清理临时目录
    shutil.rmtree(tmp_root, ignore_errors=True)
    print("\n=== 结果汇总 ===")
    failed = [n for n, ok, _ in results if not ok]
    for n, ok, d in results:
        print(f"  [{'通过' if ok else '失败'}] {n}")
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
