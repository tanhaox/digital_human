# -*- coding: utf-8 -*-
"""动画产线编排服务 — app/services/anim_pipeline 的系统外壳 (0914 系统化).

管线本体 (app/services/anim_pipeline) 保持零 app.* import、状态机全在 shots.json;
本模块管 app 侧的一切:
  - 内存 job 注册表 + daemon 线程执行 plan/k2/h3 相位 (bs1 产线模式)
  - GPU 托管: k2/h3 包 gpu_service_manager.session("comfyui") — 不在线自动拉起
  - 进度: jobs._publish → GET /api/jobs/{job_id}/events (SSE) + 管线日志桥接
  - 取消: 镜间协作 (stop_check); k2 粒度 ~15s / h3 粒度 ~100s
  - 恢复: job 是内存的, shots.json 是持久层 — 服务重启后 status 端点重算, 批任务可续跑

锁 (两层, 与 session() 内部全局 GPU 锁不冲突):
  - _GPU_JOB_LOCK: 同一时刻至多一个 GPU 批 (k2/h3), 并发请求 → AnimBusyError(409)
  - _EP_LOCKS: 每书+集写锁 — plan 重写 shots.json / 批任务逐镜落盘 / approve 回填互斥
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.services.anim_pipeline import h3 as h3_mod
from app.services.anim_pipeline import k2 as k2_mod
from app.services.anim_pipeline import planner
from app.services.anim_pipeline import shots as shots_mod
from app.services.anim_pipeline.config import load as anim_cfg_load
from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK as _GPU_JOB_LOCK
from app.services.gpu_service_manager import get_gpu_service_manager
from app.services.job_events import _publish

logger = logging.getLogger(__name__)

# B08 (2026-10-16): 可选加料供给注册 — 管线 (anim_pipeline) 经 feeds.get_feed 取料,
# 此处 (app 外壳, 两侧都可引) 把 book 侧读料函数注入。进程级注册: main 装配
# (routers/anim 顶层引本模块) 即生效, 路由直调管线 (books.py derive_characters)
# 也不缺料; 参数注入方案会连锁 6+ 签名且路由侧没人喂料, 故弃之。
from app.services.anim_pipeline.feeds import register_feed as _register_feed
from app.services.book_service.distiller import load_section as _load_section
from app.services.book_service.fandeng_full import load_fandeng_full as _load_ff

_register_feed("story_section", _load_section)
_register_feed("fandeng_full", _load_ff)

PHASES = ("plan", "replan", "redo", "k2", "h3", "brand", "enhance")
# redo (列队重做) 含 LLM 段但入 GPU_PHASES: 全程持锁, 与 k2/h3/brand 互斥
GPU_PHASES = ("redo", "k2", "h3", "brand")
_LOG_TAIL = 400

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
# _GPU_JOB_LOCK 定义已抽到 anim_pipeline/gpu_lock.py (B07), 顶部 alias 引同一实例
_EP_LOCKS: dict[str, threading.RLock] = {}  # RLock: plan job 内部会再调 align (同线程重入)
_EP_LOCKS_GUARD = threading.Lock()


class AnimBusyError(RuntimeError):
    """产线忙 (GPU 批进行中 / 同集任务进行中)."""


class AnimStyleGateError(AnimBusyError):
    """无书级风格绑定 (0922 硬挡板) — 继承 Busy 走既有 409 通道, 先跑讲书页[风格选型]."""


class AnimNotFound(KeyError):
    """书或 shots.json 不存在."""


# ── 工具 ─────────────────────────────────────────────────────

def resolve_ep(book_ref: str, ep: int) -> tuple[str, str]:
    """book_ref (UUID 前缀/书名子串) → (book_id, book_title). 复用管线解析器."""
    try:
        return planner.resolve_book(anim_cfg_load().db_path, book_ref)
    except SystemExit as exc:
        raise AnimNotFound(str(exc)) from exc


def _ep_lock(book_id: str, ep: int) -> threading.RLock:
    key = f"{book_id}:{ep}"
    with _EP_LOCKS_GUARD:
        if key not in _EP_LOCKS:
            _EP_LOCKS[key] = threading.RLock()
        return _EP_LOCKS[key]


@contextmanager
def ep_guard(book_id: str, ep: int, timeout: float = 10.0):
    """同步短操作 (approve/align/draft) 的每集写锁; 忙则 AnimBusyError."""
    lock = _ep_lock(book_id, ep)
    if not lock.acquire(timeout=timeout):
        raise AnimBusyError(f"该集任务进行中 (book {book_id} ep{ep}), 稍后再试")
    try:
        yield
    finally:
        lock.release()


def ep_base_dir(book_title: str, ep: int):
    return shots_mod.ep_dir(book_title, ep)


def resolve_ep_file(book_title: str, ep: int, rel: str):
    """ep 内相对路径 → 绝对路径; 越出 outputs/动画 根即拒 (防穿越, 含 ../../_资产 合法用例)."""
    base = ep_base_dir(book_title, ep).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(shots_mod.anim_output_root().resolve()):
        raise AnimNotFound(f"非法路径: {rel}")
    return p


class _JobLogBridge(logging.Handler):
    """把管线 logger (app.services.anim_pipeline.*) 的日志桥进 job log + SSE."""

    def __init__(self, job: dict[str, Any]):
        super().__init__(level=logging.INFO)
        self._job = job

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("app.services.anim_pipeline"):
            return
        msg = record.getMessage()
        with _JOBS_LOCK:
            self._job["log"].append(msg)
            if len(self._job["log"]) > _LOG_TAIL:
                del self._job["log"][:-_LOG_TAIL]
        _publish(self._job["id"], {"type": "anim_log", "message": msg[:500]})


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"], "phase": job["phase"], "status": job["status"],
        "book_id": job["book_id"], "ep": job["ep"],
        "arc": (job.get("arc_id") or "").upper() or None,
        "progress": job.get("progress"), "error": job.get("error"),
        "result": job.get("result"), "started_at": job["started_at"],
        "finished_at": job.get("finished_at"), "log_lines": len(job["log"]),
    }


# ── job 查询/取消 ────────────────────────────────────────────

def running_jobs() -> list[dict[str, Any]]:
    """当前运行中的 anim job 列表 (安全重启守卫用, 0915 两次撞杀事故根治)."""
    with _JOBS_LOCK:
        return [_job_view(j) for j in _JOBS.values() if j["status"] == "running"]


def get_job(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise AnimNotFound(f"job 不存在: {job_id}")
        view = _job_view(job)
        view["log"] = list(job["log"][-80:])
        return view


def cancel_job(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise AnimNotFound(f"job 不存在: {job_id}")
        if job["status"] == "running":
            job["cancel"] = True
    _publish(job_id, {"type": "anim_cancelling", "message": "已请求停止 — 当前镜完成后中断"})
    return _job_view(job)


# ── 相位启动 ─────────────────────────────────────────────────

def start_phase(book_ref: str, ep: int, phase: str, *, only: set[str] | None = None,
                sids: set[str] | None = None,
                retry_failed: bool = False, reroll: set[str] | None = None,
                force: bool = False, arc_id: str = "", reset: bool = False,
                scorched: bool = False, chain_h3: bool = False,
                concept_only: bool = False, continue_scenes: bool = False,
                to_image: bool = False) -> dict[str, Any]:
    """启动相位 job (plan=规划 / replan=单场 / k2=批生图 / h3=批生视频). 返回 job view.

    reset (0915 三级清障): k2=保规划清图+视频历史; h3=保规划+图清视频历史.
    scorched: plan 全清重来 (备份+删 shots.json 整集重规划).
    concept_only (0917 立意人闸): 切场+立意即停; continue_scenes: ▶️续跑分镜.
    """
    if phase not in PHASES:
        raise ValueError(f"未知相位: {phase} (可选 {PHASES})")
    book_id, book_title = resolve_ep(book_ref, ep)

    # 0922 风格硬挡板 (用户令): 无书级绑定不动工 — 治默认皮肤悄悄生效;
    # 换肤唯一入口 = 讲书页[风格选型] → 风格选择板点选落绑定。
    from app.services.anim_pipeline.config import style_binding_missing
    if style_binding_missing(book_title):
        raise AnimStyleGateError(
            f"《{book_title}》 无风格绑定 — 默认皮肤不再悄悄生效; "
            "先在讲书页跑 [🎨 风格选型] (真内容试镜 → 选择板点选)")

    if phase in GPU_PHASES and not _GPU_JOB_LOCK.acquire(blocking=False):
        with _JOBS_LOCK:
            cur = next((j for j in _JOBS.values() if j["status"] == "running" and j["phase"] in GPU_PHASES), None)
        desc = f"{cur['phase']} {cur['book_id']} ep{cur['ep']}" if cur else ""
        raise AnimBusyError(f"GPU 批任务进行中 ({desc}), 等它完成或取消后再试")

    job: dict[str, Any] = {
        "id": str(uuid.uuid4()), "phase": phase, "status": "running",
        "book_id": book_id, "book_title": book_title, "ep": ep,
        "only": only, "sids": sids, "retry_failed": retry_failed, "reroll": reroll,
        "force": force, "arc_id": arc_id, "reset": reset, "scorched": scorched,
        "chain_h3": chain_h3, "concept_only": concept_only, "continue_scenes": continue_scenes,
        "to_image": to_image,  # 0927 redo 购物篮: 只到生图 (K2 停 img_done 走人检)
        "cancel": False,
        "progress": None, "result": None, "error": None,
        "log": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "finished_at": None,
    }
    with _JOBS_LOCK:
        _JOBS[job["id"]] = job
        if len(_JOBS) > 60:  # 只留最近 60 个 (终态的旧 job 清掉)
            for old in [j for j in _JOBS.values() if j["status"] != "running"][:-30]:
                _JOBS.pop(old["id"], None)
    threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"anim-{phase}").start()
    return _job_view(job)


def _run_job(job: dict[str, Any]) -> None:
    bridge = _JobLogBridge(job)
    logging.getLogger().addHandler(bridge)
    try:
        _publish(job["id"], {"type": "anim_started", "phase": job["phase"],
                             "book": job["book_title"], "ep": job["ep"]})
        if job["phase"] == "plan":
            _phase_plan(job)
        elif job["phase"] == "replan":
            _phase_replan(job)
        elif job["phase"] == "redo":
            _phase_redo(job)
        elif job["phase"] == "brand":
            _phase_brand(job)
        elif job["phase"] == "enhance":
            _phase_enhance(job)  # 1013 P0: 手动修复相位 (零 GPU, 不走 _phase_gpu)
        else:
            _phase_gpu(job)
        with _JOBS_LOCK:
            cancelled = job["cancel"]
            job["status"] = "cancelled" if cancelled else "completed"
            job["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _publish(job["id"], {"type": "anim_cancelled" if cancelled else "anim_done",
                             "result": job.get("result")})
    except SystemExit as exc:  # 管线业务错 (planner 用 SystemExit 报可读信息)
        _fail_job(job, str(exc))
    except AnimNotFound as exc:
        _fail_job(job, str(exc.args[0]) if exc.args else str(exc))
    except Exception as exc:
        logger.exception("[anim] job %s 崩溃", job["id"])
        _fail_job(job, f"{type(exc).__name__}: {exc}")
    finally:
        logging.getLogger().removeHandler(bridge)
        if job["phase"] in GPU_PHASES:
            _GPU_JOB_LOCK.release()


def _fail_job(job: dict[str, Any], message: str) -> None:
    with _JOBS_LOCK:
        job["status"] = "failed"
        job["error"] = message
        job["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _publish(job["id"], {"type": "anim_error", "error": message[:800]})


def _phase_plan(job: dict[str, Any]) -> None:
    """规划相位 (音频先行执法, 0912 用户架构令: 先语音→实测真实时长→动画按具体秒数).

    动画线动线 = 文字→音频→分镜→动画 (与页单工坊线二选一); 无已验收口播的集
    直接拒绝规划 — 规划必须吃真实音频时长, 否则 H3 按估算生成、漂移在 GPU
    花完后才暴露 (ep1 实证 17 镜 H3重跑)。
    """
    from app.services import anim_draft  # 延迟导入避免模块环
    audio = anim_draft.fetch_episode_audio(job["book_id"], job["ep"])
    logger.info("[plan] 音频先行 ✓ job %s, %d 包 %.0fs — 圣经检查 + 导演规划 (LLM 2-4 分钟, 期间日志静默属正常)",
                audio["job_id"][:8], len(audio["files"]),
                sum(f["dur_s"] for f in audio["files"]))
    with _ep_lock(job["book_id"], job["ep"]):
        if job.get("continue_scenes"):
            # ▶️ 分镜续跑 (0917 立意人闸第二段): 立意确认后从骨架续跑逐场
            path = planner.resume_scenes(job["book_id"], job["ep"])
            # 1013 P0: 自动增强退役 (单写者直接产散文; 手动修复走 enhance 相位/端点)
            job["result"] = {"shots_json": str(path), "scenes_resumed": True}
            return
        path = planner.plan(job["book_id"], job["ep"], force=job["force"],
                            scorched=bool(job.get("scorched")),
                            concept_only=bool(job.get("concept_only")))
        doc = shots_mod.load_doc(job["book_title"], job["ep"])
        if str(doc.get("planner_flow", "")).startswith("director2"):
            # v2 时间轴原生对齐 manifest (含 opening 预留) — 只备整集口播, 不再归真
            from app.services import anim_draft
            voice = shots_mod.ep_dir(job["book_title"], job["ep"]) / "voice_full.wav"
            # 0918 改字陈旧实锤: voice_full 只在"不存在"时拼接 — 用户改字 reroll 后
            # manifest 变旧 voice 永不重建 (三层时长不一致元凶之一)。包 wav 任一比
            # voice 新 → 重建。
            _stale = voice.exists() and any(
                Path(f["file"]).stat().st_mtime > voice.stat().st_mtime
                for f in audio["files"][:8])
            if not voice.exists() or _stale:
                if _stale:
                    logger.info("[plan] voice_full 比包 wav 旧 (改字后未重建) — 重新拼接")
                anim_draft.concat_voice(audio["files"], voice)
            n_arcs = len(doc.get("arcs") or [])
            # 立意人闸停止路径 (concept_only): 骨架无镜 — 取尾镜时长会 IndexError
            # (0918 job 1770330f 实锤), 人闸停止属正常态非崩溃
            _ep_len = float(doc["shots"][-1]["t_end"]) if doc.get("shots") else 0.0
            job["result"] = {
                "shots_json": str(path), "audio_job": audio["job_id"],
                "arcs": n_arcs, "groups": len(doc.get("groups") or []),
                "shots": len(doc.get("shots") or []),
                "bible": bool(doc.get("bible", {}).get("style_prefix")),
                "overrun": [s["shot_id"] for s in doc.get("shots") or [] if s.get("overrun_s")],
                "concept_gate": not doc.get("shots"),
            }
            logger.info("[plan] v2 完成: %d 场 · %d 组 · %d 镜%s", n_arcs,
                        len(doc.get("groups") or []), len(doc.get("shots") or []),
                        f" · 全集成片 ≈ {_ep_len:.0f}s" if _ep_len else " · 🛑 立意人闸待确认")
            # 1013 P0: 自动增强退役 (同上)
        else:
            report = anim_draft.align_episode(job["book_id"], job["ep"], dry_run=False)
            job["result"] = {
                "shots_json": str(path), "audio_job": audio["job_id"],
                "ep_len_s": report["ep_len_s"], "align_stats": report["stats"],
                "overrun": [r["shot_id"] for r in report["shots"] if r.get("class") == "段超8s"],
            }


def _phase_replan(job: dict[str, Any]) -> None:
    """单场重规划相位 (磨合循环: 本场 规划→K2→H3 反复测). LLM 零 GPU, ep 写锁互斥."""
    arc_id = job.get("arc_id") or ""
    logger.info("[replan] 音频先行 ✓ — 场 %s 重规划 (LLM 单窗调用, 1-2 分钟静默属正常)", arc_id.upper())
    with _ep_lock(job["book_id"], job["ep"]):
        path = planner.replan_scene(job["book_id"], job["ep"], arc_id)
        doc = shots_mod.load_doc(job["book_title"], job["ep"])
        key = arc_id.lower()
        n_groups = len([g for g in doc.get("groups") or [] if str(g.get("arc_id", "")).lower() == key])
        scene = [s for s in doc["shots"] if str(s.get("arc_id", "")).lower() == key]
        job["result"] = {"arc_id": arc_id.upper(), "shots_json": str(path),
                         "groups": n_groups, "shots": len(scene), "total_shots": len(doc["shots"])}
        logger.info("[replan] 场 %s 重生完成: %d 组 %d 镜 (全集 %d 镜, 其余场未动)",
                    arc_id.upper(), n_groups, len(scene), len(doc["shots"]))
        # 1013 P0: 自动增强退役 (同上)


def _phase_redo(job: dict[str, Any]) -> None:
    """列队重做 (0921 购物篮): 逐镜 LLM 重设计 (跳败续跑) → 成功集 K2+H3 一条龙.

    复用 replan_shot (每镜自动备份/失败回滚); 单镜败 ×2 只跳过不炸整队, 结束汇总。
    GPU 段直接改道 _phase_gpu (only=成功集, chain_h3) — 时间纪律/禁区闸门照走。
    """
    sids = list(dict.fromkeys(job.get("sids") or []))   # 去重保序
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    with _ep_lock(job["book_id"], job["ep"]):
        doc = shots_mod.load_doc(job["book_title"], job["ep"])
        t0 = {s["shot_id"]: float(s.get("t_start") or 0) for s in doc["shots"]}
        unknown = [sid for sid in sids if sid not in t0]
        failed += [{"sid": sid, "error": "镜不存在 (镜号重排后?)"} for sid in unknown]
        queue = sorted((sid for sid in sids if sid in t0), key=lambda x: t0[x])
        total = len(queue)
        for i, sid in enumerate(queue, 1):
            if job["cancel"]:
                logger.info("[redo] ⏹ 队列中止于 %s (%d/%d 已完成)", sid, i - 1, total)
                break
            with _JOBS_LOCK:
                job["progress"] = {"done": i - 1, "total": total, "current": sid}
            _publish(job["id"], {"type": "anim_progress", "phase": "redo",
                                 "shot_id": sid, "done": i - 1, "total": total})
            try:
                replan_shot(job["book_id"], job["ep"], sid)   # ep 锁 RLock 同线程重入 ✓
                ok.append(sid)
                logger.info("[redo] %d/%d %s ✓ 重设计完成", i, total, sid)
            except Exception as exc:  # noqa: BLE001 — 单镜败只跳过, 队列继续
                msg = str(exc.args[0] if isinstance(exc, SystemExit) and exc.args else exc)[:160]
                failed.append({"sid": sid, "error": msg})
                logger.warning("[redo] %d/%d %s ✗ 跳过: %s", i, total, sid, msg)
        job["result"] = {"redone": ok, "failed": failed}
        logger.info("[redo] LLM 段收队: %d 成功 / %d 失败%s", len(ok), len(failed),
                    " — 转 GPU 一条龙" if ok and not job["cancel"] else " — 无可生成镜")
    if ok and not job["cancel"]:
        job["only"] = set(ok)
        # 0927 to_image: 重设计→生图后停在 img_done 走标准人检, 不自动过审不跑 H3
        job["chain_h3"] = not bool(job.get("to_image"))
        _phase_gpu(job)   # K2 only=成功集 → (chain_h3 时自动过审 → H3 同 comfy session)
        _mark_redo_review(job, ok)
        with _JOBS_LOCK:
            job["result"] = {"redone": ok, "failed": failed, **(job.get("result") or {})}


def _review_flags_path(book_title: str, ep: int) -> Path:
    """1010 R 标存独立小文件 (redo_review.json), 不进 shots.json —
    批量任务持集锁期间用户随时可清标 (409 死锁实锤), 两边互不干扰。"""
    return shots_mod.ep_dir(book_title, ep) / "redo_review.json"


def _load_review_flags(book_title: str, ep: int) -> dict[str, bool]:
    import json as _json
    p = _review_flags_path(book_title, ep)
    if not p.exists():
        return {}
    try:
        d = _json.loads(p.read_text(encoding="utf-8"))
        return {k: True for k, v in d.items() if v} if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — 损坏文件当空处理, 自愈
        return {}


def _save_review_flags(book_title: str, ep: int, flags: dict[str, bool]) -> None:
    import json as _json
    p = _review_flags_path(book_title, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(flags, ensure_ascii=False, indent=1), encoding="utf-8")


def _mark_redo_review(job: dict[str, Any], ok: list[str]) -> None:
    """1010 R 标 (用户令): 列队重做成功镜打待复查标 —
    前端卡头红底白字 R; 点播放看过 / ✅过审 / 打回即消。
    to_image 线只认 img_done (图是新的), 全链线只认 anim_done (视频是新的)。"""
    want = "img_done" if job.get("to_image") else "anim_done"
    try:
        doc = shots_mod.load_doc(job["book_title"], job["ep"])   # 只读, 批已收队
        flags = _load_review_flags(job["book_title"], job["ep"])
        hit = 0
        for sid in ok:
            try:
                s = shots_mod.find_shot(doc, sid)
            except KeyError:
                continue
            if s["status"] == want:
                flags[sid] = True
                hit += 1
        if hit:
            _save_review_flags(job["book_title"], job["ep"], flags)
        logger.info("[redo] R 标: %d/%d 镜打待复查 (认 %s)", hit, len(ok), want)
    except Exception as exc:  # noqa: BLE001 — 打标失败不污染任务结果
        logger.warning("[redo] R 标失败 (忽略): %s", exc)


def mark_viewed(book_ref: str, ep: int, sid: str) -> dict[str, Any]:
    """1010 R 标清除: 用户点播放看过该镜 → 撤"重做待复查"标。
    R 标在独立文件, 不吃集锁 — 批量任务进行中也能清 (看片不被 409 挡)。"""
    book_id, book_title = resolve_ep(book_ref, ep)
    doc = shots_mod.load_doc(book_title, ep)
    shots_mod.find_shot(doc, sid)   # 校验镜存在 (404 语义)
    flags = _load_review_flags(book_title, ep)
    had = flags.pop(sid, None) is not None
    if had:
        _save_review_flags(book_title, ep, flags)
    return {"cleared": had}


def _reset_states(doc: dict[str, Any], phase: str) -> int:
    """三级清障 (0915 用户令): 保留规划, 清生成历史。返回受影响镜数。

    k2: 图+视频历史全清 → 全部回 planned (重新生图, 品牌卡零GPU直通无成本)
    h3: 只清视频历史 → anim_done/anim_fail 回 approved (图保留)
    磁盘旧文件不删 (重生成同名覆盖; 退役id孤儿文件无害)。
    """
    import shutil
    path = shots_mod.shots_path(doc["book_title"], doc["ep"])
    if path.exists():
        bak = path.with_name(f"shots.json.bak_reset_{phase}_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        logger.info("[reset-%s] 旧版备份: %s", phase, bak.name)
    n = 0
    if phase == "k2":
        for s in doc["shots"]:
            if s.get("image_file") or s.get("video_file") or s["status"] != "planned":
                n += 1
            s["image_file"] = None
            s["video_file"] = None
            s["status"] = "planned"
            s["attempts"] = {"k2": 0, "h3": 0}
            s["error"] = None
            s["reject_note"] = ""
            s.pop("overrun_s", None)
    elif phase == "h3":
        for s in doc["shots"]:
            if s["status"] in ("anim_done", "anim_fail") or s.get("video_file"):
                n += 1
                s["status"] = "approved"
                s["video_file"] = None
                s["attempts"]["h3"] = 0
                s["error"] = None
    logger.info("[reset-%s] %d 镜历史已清 (规划%s保留)", phase, n,
                "+图" if phase == "h3" else "")
    return n


def _phase_enhance(job: dict[str, Any]) -> None:
    """手动增强相位 (1013 P0: 自动增强退役后的人工修复入口, 页面 🖌 按钮).

    单写者主路下 enhance 只作修复工具 (改稿/抛光), 走 ep 写锁, 零 GPU。"""
    with _ep_lock(job["book_id"], job["ep"]):
        doc = shots_mod.load_doc(job["book_title"], job["ep"])
        from app.services.anim_pipeline import director2 as _d2e
        _d2e.enhance_keyframes(doc, job["book_title"])
        n1 = sum(1 for s in doc.get("shots") or [] if s.get("_enhanced1"))
        job["result"] = {"shots_json": str(shots_mod.shots_path(job["book_title"], job["ep"])),
                         "enhanced1_total": n1}


def _phase_brand(job: dict[str, Any]) -> None:
    """品牌卡随书生成 (0915 用户令: 风格与书的动画一致). 一次性 ~2min GPU, 全系列复用."""
    from app.services.anim_pipeline import brand as brand_mod
    from app.services.anim_pipeline import director2

    def svc_notify(message: str) -> None:
        _publish(job["id"], {"type": "anim_service", "message": message})

    bible = director2.load_or_derive_bible(job["book_id"], job["book_title"])
    with get_gpu_service_manager().session("comfyui", status_callback=svc_notify):
        from app.services.anim_pipeline import comfy as comfy_mod
        comfy_mod.free_memory()
        paths = brand_mod.generate(job["book_title"], bible=bible)
    job["result"] = {"brand_card": paths, "book": job["book_title"]}
    logger.info("[brand] %s 品牌卡已生成 (骨架冻结+书级风格, 仪式句走后期): %s",
                job["book_title"], paths.get("video"))


def _phase_gpu(job: dict[str, Any]) -> None:
    def on_shot(shot_id: str, done: int, total: int) -> None:
        with _JOBS_LOCK:
            job["progress"] = {"done": done, "total": total, "current": shot_id}
        _publish(job["id"], {"type": "anim_progress", "phase": job["phase"],
                             "shot_id": shot_id, "done": done, "total": total})

    def svc_notify(message: str) -> None:
        _publish(job["id"], {"type": "anim_service", "message": message})

    with _ep_lock(job["book_id"], job["ep"]):
        doc = shots_mod.load_doc(job["book_title"], job["ep"])
        # 0916 全面加强·生成闸门: K2/H3 前直验时间纪律 (镜界必须在句边上) —
        # 乱纪文档烧 GPU = 按错配文案生成 (s53 实锤), 拒跑并指路对齐
        if job["phase"] in ("k2", "h3", "redo"):
            from app.services import anim_draft as _ad
            _viol = _ad.timeline_violations(job["book_id"], job["ep"])
            if _viol:
                raise ValueError(
                    f"时间纪律未过: {_viol[0]} 等 {len(_viol)} 处镜界脱离句边 — "
                    "先按 🎚对齐落盘 再生成 (防按乱纪文案烧 GPU)")
            # 0917 政治敏感画面禁区 (用户令): 中国地图/国旗/国徽/领导人画像 —
            # 卡通/剪影/变形同禁; 0924 升级: 自动修复禁区词 → 修不了才跳过
            from app.services.anim_pipeline import director2 as _d2
            _ban = _d2.visual_ban_scan(doc)
            if _ban:
                # 先尝试自动修复 (已知问题自动修, 不死循环等人)
                _d2.auto_fix_banned(doc)
                _ban = _d2.visual_ban_scan(doc)  # 复扫
                shots_mod.save(doc)  # 修复结果落盘(含清标记)
            if _ban:
                _ban_ids = {b.split("(")[0] for b in _ban}
                for s in doc["shots"]:
                    if s["shot_id"] in _ban_ids:
                        s["political_flag"] = True
                        logger.warning("[gpu] %s 政治禁区命中(修不了), 跳过: %s",
                                       s["shot_id"], str(s.get("keyframe_zh") or "")[:60])
                shots_mod.save(doc)
                logger.warning("[gpu] 政治禁区: %d 镜无法自动修复 (%s), 跳过",
                               len(_ban_ids), ", ".join(sorted(_ban_ids)[:6]))
        # 1013 P0: 降级链可见性 — fallback 场/默认槽进 GPU 前显著记录 (页面/人检)
        _fb = sum(1 for s in doc.get("shots") or [] if str(s.get("writer_source") or "") == "fallback")
        _df = sum(1 for s in doc.get("shots") or [] if s.get("slots_source") == "default")
        if _fb or _df:
            logger.warning("[gpu] ⚠ 降级产物入 GPU: fallback场镜=%d 默认槽镜=%d (人检)", _fb, _df)
            # 1015 修 (ep4 K2 两连崩实锤): job.result 初始为 None, k2 相位到达此行前
            # 无人置 dict — setdefault 拿回 None 再赋值即 TypeError, 批在渲染前 0 秒死
            job["result"] = {**(job.get("result") or {}),
                             "degraded": {"fallback": _fb, "default_slots": _df}}
        if job.get("reset"):
            _reset_states(doc, job["phase"])
            shots_mod.save(doc)
        if job["phase"] == "h3" and job.get("reroll"):
            for sid in job["reroll"]:
                s = shots_mod.find_shot(doc, sid)  # 先全量校验防半改
            for sid in job["reroll"]:
                s = shots_mod.find_shot(doc, sid)
                shots_mod.transition(s, "approved")  # 回可跑态 (旧 video_file 跑完覆盖)
                shots_mod.retry_anim(s)
            shots_mod.save(doc)
        stop = lambda: job["cancel"]
        with get_gpu_service_manager().session("comfyui", status_callback=svc_notify):
            from app.services.anim_pipeline import comfy as comfy_mod
            comfy_mod.free_memory()  # 相位边界卫生: 卸掉上一相位的模型栈 (花屏根治)
            try:
                # 1005 生产台账旗标 (用户令: 只记重做/补做 — 改进点; 首过批量=基线不记):
                # run_shot 层按 (ep,kind,sid) 首次=基线跳过, 后续=重做; trigger 落账
                doc["_ledger_trigger"] = "|".join(filter(None, [
                    job["phase"],
                    "fill" if job.get("only") else "",
                    "retry" if job.get("retry_failed") else "",
                    "reroll" if job.get("reroll") else "",
                    "chain" if job.get("chain_h3") else ""]))
                if job["phase"] in ("k2", "redo"):
                    res = k2_mod.run_batch(doc, only=job["only"],
                                           on_shot_done=on_shot, stop_check=stop)
                    # 1015 修脸 pass: K2 出图后逐脸 inpaint (先修脸后入字 — 文字层最后落)
                    try:
                        from app.services.anim_pipeline.face_fix import batch_face_fix
                        doc = shots_mod.load_doc(job["book_title"], job["ep"])
                        n_face = batch_face_fix(doc, only=job.get("only"))
                        if n_face:
                            shots_mod.save(doc)
                    except Exception as exc:  # noqa: BLE001 — 修脸失败不阻断主链
                        logger.warning("[face-fix] 批量修脸异常 (不阻断): %s", exc)
                    # 1015 抹除 pass: 未申报镜的物件先验涂写 → 2511 编辑抹成空白素面
                    try:
                        from app.services.anim_pipeline.erase_pass import (
                            batch_erase_pass,
                        )
                        doc = shots_mod.load_doc(job["book_title"], job["ep"])
                        n_er = batch_erase_pass(doc, only=job.get("only"))
                        if n_er:
                            shots_mod.save(doc)
                    except Exception as exc:  # noqa: BLE001 — 抹除失败不阻断主链
                        logger.warning("[erase] 批量抹除异常 (不阻断): %s", exc)
                    # 1014 入字 pass: K2 出图后, 对有 in_image_text 的镜运行 2511 入字
                    try:
                        from app.services.anim_pipeline.text_pass import batch_text_pass
                        doc = shots_mod.load_doc(job["book_title"], job["ep"])
                        n_txt = batch_text_pass(doc, only=job["only"])
                        if n_txt:
                            shots_mod.save(doc)
                            logger.info("[text-pass] %d 镜入字完成 ✓", n_txt)
                    except Exception as exc:  # noqa: BLE001 — 入字失败不阻断主链
                        logger.warning("[text-pass] 批量入字异常 (不阻断): %s", exc)
                    if job.get("chain_h3") and not job["cancel"]:
                        # 一条龙 (0915 用户令: 重规划此镜→确认→直达视频):
                        # 图完成后自动过审本批镜, 同一 comfy session 续跑 H3
                        doc = shots_mod.load_doc(job["book_title"], job["ep"])
                        ids = [s["shot_id"] for s in doc["shots"]
                               if s["status"] == "img_done"
                               and (job["only"] is None or s["shot_id"] in job["only"])]
                        if ids:
                            for s in doc["shots"]:
                                if s["shot_id"] in ids:
                                    shots_mod.transition(s, "approved")
                            shots_mod.save(doc)
                            logger.info("[chain] 图完成 → 自动过审 %d 镜 → 续跑 H3", len(ids))
                            res_h = h3_mod.run_batch(doc, only=set(ids),
                                                     on_shot_done=on_shot, stop_check=stop)
                            res = {"k2": res, "h3": res_h}
                        else:
                            logger.warning("[chain] 无图完成镜, 跳过 H3 段")
                else:
                    _adopt_orphan_videos(doc)  # 0918 孤儿回收: 断链/重启遗留的 H3 产物
                    doc["_ledger_trigger"] = "|".join(filter(None, [
                        job["phase"],
                        "fill" if job.get("only") else "",
                        "retry" if job.get("retry_failed") else ""]))
                    res = h3_mod.run_batch(doc, only=job["only"], retry_failed=job["retry_failed"],
                                           on_shot_done=on_shot, stop_check=stop)
            finally:
                # 0923 用户令: 批跑完释放显存 — K2/H3 模型栈不再驻留 (18GB 白占实锤)
                comfy_mod.free_memory()
                logger.info("[gpu] 批跑完显存已释放 (ComfyUI /free)")
        job["result"] = res
        # 1004 用户令: 批终点即实况 — 全镜 anim_done 的集, 逐集确认自动过
        # (讲书页"确认第N集"不再等人点; 重稿过的集仍回人工)
        try:
            from app.services.book_service.episode_gen import auto_confirm_by_anim
            if auto_confirm_by_anim(job["book_id"], job["ep"]):
                _publish(job["id"], {"type": "anim_service",
                                     "message": "动画全生成完 → 逐集确认已自动通过"})
        except Exception as exc:  # noqa: BLE001 — 确认同步失败不污染批结果
            logger.warning("[gpu] 逐集确认自动同步失败 (忽略): %s", exc)


def _adopt_orphan_videos(doc: dict[str, Any]) -> int:
    """孤儿视频回收 (0918 s14 三连实锤): H3 提交后进程重启/断链 → 轮询线程死,
    视频躺在 ComfyUI output 无人搬, 页面只剩图。

    扫 output/video/anim/{书}/ep{n}/, 凡镜无 video 且 output 有其前缀最新 mp4
    (比该镜现有 png/记录新) → 搬回挂账 anim_done。只认"帧对齐合理"(≥1s)产物。"""
    import shutil as _sh
    import subprocess as _sp

    from app.services.anim_pipeline import config as _acfg
    out_root = Path(_acfg.load().comfy.output_dir) / "video" / "anim" / \
        shots_mod._safe(str(doc.get("book_title"))) / f"ep{doc.get('ep')}"
    if not out_root.exists():
        return 0
    ep_dir = shots_mod.ep_dir(str(doc.get("book_title")), int(doc.get("ep")))
    adopted = 0
    for s in doc["shots"]:
        # 0919 扩 anim_fail (H3 假死实锤: 超时判死但 ComfyUI 实际跑完, 产物躺
        # output 无人领); 有 video_file 的 anim_fail (已降级静态) 不碰
        if s.get("video_file") or s.get("status") not in ("approved", "anim_fail"):
            continue
        sid = str(s["shot_id"])
        cands = sorted(out_root.glob(f"{sid}_*.mp4"))
        if not cands:
            continue
        latest = cands[-1]
        dst = ep_dir / "anim" / f"{sid}.mp4"
        try:
            out_ = _sp.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(latest)], capture_output=True, text=True)
            dur = float(out_.stdout.strip() or -1)
        except Exception:  # noqa: BLE001
            continue
        if dur < 1.0:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(latest, dst)
        s["video_file"] = f"anim/{sid}.mp4"
        s["status"] = "anim_done"
        s["error"] = None
        adopted += 1
        logger.info("[adopt] %s 孤儿视频回收: %s (%.1fs)", sid, latest.name, dur)
    if adopted:
        shots_mod.save(doc)
    return adopted


# ── approve (同步, 镜像 CLI 语义) ────────────────────────────

def apply_approve(book_ref: str, ep: int, ok: list[str] | str | None = None,
                  redo: list[Any] | None = None, note: str = "") -> dict[str, Any]:
    """人检回填: ok=[sid..]|"all" 过审 (仅 img_done);
    redo=[{sid,note,image_prompt_zh}|sid] 打回换 seed 回 planned —
    带 image_prompt_zh = 定向修改 (新描述+新seed 重生成), 留空 = 纯换seed抽卡."""
    book_id, book_title = resolve_ep(book_ref, ep)
    lock = _ep_lock(book_id, ep)
    if not lock.acquire(timeout=5):
        raise AnimBusyError("该集有任务进行中 (plan/批生成), 稍后再试")
    try:
        doc = shots_mod.load_doc(book_title, ep)
        if ok == "all":
            # 0924 修: 只过审有图的 img_done — 幽灵 img_done(图文件不存在)回退 planned
            base = shots_mod.ep_dir(book_title, ep)
            ok_ids = set()
            for s in doc["shots"]:
                if s["status"] == "img_done":
                    img_p = base / str(s.get("image_file") or "")
                    if img_p.exists():
                        ok_ids.add(s["shot_id"])
                    else:
                        logger.warning("[approve] %s 幽灵 img_done (图不存在), 回退 planned",
                                       s["shot_id"])
                        shots_mod.transition(s, "planned")
        else:
            ok_ids = {x for x in (ok or []) if x}
        redo_items = []  # (sid, note, new_prompt|None, negatives|[])
        for r in redo or []:
            if isinstance(r, dict):
                if r.get("sid"):
                    _neg = [w.strip() for w in str(r.get("negative") or "").replace("，", ",").split(",") if w.strip()]
                    redo_items.append((str(r["sid"]), str(r.get("note") or note),
                                       (r.get("image_prompt_zh") or "").strip() or None, _neg))
            elif r:
                redo_items.append((str(r), note, None, []))
        if not ok_ids and not redo_items:
            raise ValueError("ok / redo 至少给一个")
        for sid in ok_ids | {r[0] for r in redo_items}:
            try:
                shots_mod.find_shot(doc, sid)  # 先全量校验, 防半改
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
        approved = skipped = 0
        # 1010 R 标: 过审/打回 = 已复查 → 清独立文件里的待复查标 (approve 本就持集锁,
        # 与批量互斥, flags 文件读写无竞争)
        r_flags = _load_review_flags(book_title, ep)
        r_cleared = False
        for sid in sorted(ok_ids):
            s = shots_mod.find_shot(doc, sid)
            if s["status"] != "img_done":
                skipped += 1  # 只有 img_done 可过审 (与 CLI 一致)
                continue
            shots_mod.transition(s, "approved")
            r_cleared |= r_flags.pop(sid, None) is not None
            approved += 1
        for sid, n, new_prompt, negatives in redo_items:
            _s = next((x for x in doc["shots"] if x["shot_id"] == sid), None)
            if _s is not None:
                shots_mod.audit_reroll(doc, _s, f"人检打回:{n or '未填原因'}")
            s = shots_mod.find_shot(doc, sid)
            final_note = n
            if new_prompt:
                s["image_prompt_zh"] = new_prompt  # 定向修改: 下次 k2 按新描述重生成
            elif negatives:
                # 0927 负面词直接应用 (确定性, 不走 LLM): 画面句里含负面词根的
                # 子句整体剔除 (K2 负面通道物理无效, 换词法=词根零残留); 留痕并进
                # 打回理由 (reject_for_reroll 会覆写 reject_note, 先并后调)
                _new_img, _removed = k2_mod.strip_negative(
                    str(s.get("image_prompt_zh") or ""), negatives)
                if _removed:
                    # 双写 keyframe+image_prompt (同 auto_fix_banned 先例):
                    # 只改 image_prompt 会被单镜重规划/增强从 keyframe 带回
                    _new_kf, _r2 = k2_mod.strip_negative(
                        str(s.get("keyframe_zh") or ""), negatives)
                    s["image_prompt_zh"] = _new_img
                    s["keyframe_zh"] = _new_kf if _r2 else _new_img
                    final_note = (f"{n or '打回'} [负面词剔除: "
                                  f"{'/'.join(negatives)} 去{_removed}子句]")
                    logger.info("[approve] %s 负面词剔除 %d 子句: %s",
                                sid, _removed, "/".join(negatives))
                else:
                    logger.warning("[approve] %s 负面词未命中/覆盖全句, 未应用: %s",
                                   sid, "/".join(negatives))
            shots_mod.reject_for_reroll(s, final_note)
            r_cleared |= r_flags.pop(sid, None) is not None   # 1010 R 标: 打回=已复查
        shots_mod.save(doc)
        if r_cleared:
            _save_review_flags(book_title, ep, r_flags)
        return {"approved": approved, "skipped": skipped, "redo": len(redo_items),
                "summary": shots_mod.summary(doc)}
    finally:
        lock.release()


# ── 一键字下沉 (0915 用户令: 字bug出来了点一下就修) ────────────

_CJK_RUN = re.compile(r"[一-鿿]{1,8}[0-9%]{0,3}")
_QUOTED = re.compile(r"[\"“‘']([^\"”’']{1,14})[\"”’']")


def _sink_text_from_beats(beats: list[dict], word: str = "") -> tuple[list[dict], list[str]]:
    """从 beats motion 里抽出文字事件 (纯函数): 返回 (新beats, 下沉的词表).

    显式 word: 删含词子句; 自动: CJK 连续段 + 引号内容 = 入画字事件.
    子句切分按英文标点; motion 被掏空则回填安全环境动效 (镜头不留死文本).
    """
    import re as _re
    sinked: list[str] = []
    for b in beats:
        m = str(b.get("motion") or "")
        if not m:
            continue
        targets: list[str] = []
        if word:
            targets = [word]
        else:
            targets = [w for w in _CJK_RUN.findall(m) if len(_re.sub(r"[0-9%]", "", w)) >= 1]
            targets += [q for q in _QUOTED.findall(m) if any(c.isdigit() or c.isalpha() for c in q)]
        if not targets:
            continue
        sinked.extend(targets)
        # 子句切除: 按句/逗号切, 删含目标词的段
        parts = _re.split(r"(?<=[.,;])\s+", m)
        keep = [seg for seg in parts
                if not any(t.lower() in seg.lower() for t in targets)]
        kept = " ".join(keep).strip(" ,.;")
        if not kept or len(kept) < 12:
            kept = "Gentle ambient motion in the scene, very slight push-in, no text of any kind."
        else:
            kept += " No text of any kind appears."
        b["motion"] = kept
    # 去重保序
    seen, uniq = set(), []
    for w in sinked:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return beats, uniq


AI_FIX_SYS = """你是动画提示词医生。这一镜的图片(第1层)和背景动画(第2层)都没问题,
只有文字动画(第3层, H3 入画字)出了问题。你的任务: 重写每拍的 motion 描述。

铁律 (违反=废稿):
1. 全镜最多 1 个文字事件 — 数字/年份/拉丁字母 或 2字中文超短词, 二选一
2. 数字与中文绝不共现同镜; 原提示词里多个文字事件 = 拆掉多余的 (写明该词走后期字幕, 不入画)
3. 3 字及以上中文词绝不入画 (一律 "走后期字幕")
4. 无文字事件的拍: 环境动画描述原样保留 (车/人/光/水/微推拉), 一字不改除非它在描述文字
5. 禁发明新文字; 招牌/门牌/卡片等表面保持空白 (blank as in the first frame)
6. 每拍以英文写, 描述具体可见的运动; 保留原拍的时间结构信息

输出: 严格 JSON {"beats": [{"motion": "..."}], "notes": "一句话说明改了什么"} 拍数必须与输入一致"""


def _sanitize_shot_text(doc: dict[str, Any], sid: str) -> int:
    """1010 单镜重规划门禁洗涤 (确定性, 用户令"绝对是系统bug"根治):
    重掷出的花字若与相邻镜重复/裸数字/单字 — 直接剥除再过门。
    拆镜共享口播回声让 LLM 几乎必掷中重复 (s81/s81b/s82 连败实锤),
    格式病不赌 LLM 运气, 与 validate_doc 同一把归一尺 (director2.norm_text_layer)。
    返回剥除条数。"""
    import re as _re

    from app.services.anim_pipeline import director2 as _d2
    shots = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start") or 0))
    idx = next((i for i, s in enumerate(shots) if s["shot_id"] == sid), None)
    if idx is None:
        return 0
    nb: set[str] = set()
    for j in (idx - 1, idx + 1):
        if 0 <= j < len(shots):
            nb |= {_d2.norm_text_layer(t.get("text"))
                   for t in (shots[j].get("text_layer") or []) if t.get("text")}
    tgt = shots[idx]
    keep, dropped = [], 0
    for t in (tgt.get("text_layer") or []):
        txt = str(t.get("text") or "").strip()
        if ((txt.isdigit() and len(txt) <= 1)
                or (len(txt) == 1 and not _re.match(r"[？！?!.。…]", txt))
                or (txt and _d2.norm_text_layer(txt) in nb)):
            dropped += 1
            continue
        keep.append(t)
    if dropped:
        tgt["text_layer"] = keep
        logger.info("[replan-shot] %s 门禁洗涤: 剥 %d 条花字 (重复/裸数字/单字)", sid, dropped)
    return dropped


def replan_shot(book_ref: str, ep: int, sid: str) -> dict[str, Any]:
    """单镜重规划 (0915 用户令): 只重设计这一镜, 时间槽/口播/兄弟镜/图全不动.

    0916 品牌卡镜放开 (用户令"有错的时候要能重规划"): 品牌镜先剥 brand_card 标
    (回普通镜+生成队列), 再走 LLM 重设计 — 标错镜/口播漂移误标 一键纠偏.
    """
    from app.services import anim_draft
    from app.services.anim_pipeline import director2

    book_id, book_title = resolve_ep(book_ref, ep)
    audio = anim_draft.fetch_episode_audio(book_id, ep)
    manifest = {"segments": [{"text": f["text"], "duration": f["dur_s"]}
                             for f in audio["files"]]}
    bible = director2.load_or_derive_bible(book_id, book_title)
    logger.info("[replan-shot] %s 单镜重规划 (LLM 30-60s, 槽位口播钉死)", sid)
    with ep_guard(book_id, ep):
        import shutil
        path = shots_mod.shots_path(book_title, ep)
        bak = path.with_name(f"shots.json.bak_shot_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        cur = shots_mod.load_doc(book_title, ep)
        target = next((x for x in cur.get("shots") or [] if x["shot_id"] == sid), None)
        if target is not None and target.get("brand_card"):
            target.pop("brand_card", None)
            target["status"] = "planned"
            target.pop("video_file", None)
            target.pop("image_file", None)
            shots_mod.save(cur)
            logger.info("[replan-shot] %s 品牌卡标已剥 → 回普通镜重设计", sid)
        problems = []
        doc = None
        # 1009 P4 (拍板 D2, 设计 §6.2): 单镜重生器层化 — 重掷创意槽 + 器层组装,
        # 寄存器/角色围栏全生效; plan_shot LLM 直写 keyframe 退役为无法层兜底
        # (旧 doc 无 shooting_script 时仍走老路)。填空 ×2 失败 → 器层默认槽
        # (画面保守但合法, 重 roll 语义=有变化)。
        from app.services.anim_pipeline.shooting_script import load as _load_ss
        _ss = _load_ss(book_title, ep)
        # 1013 P0 设计纠错#6 落地 (列队重做空转实锤): writer 镜的单镜重做走
        # plan_shot 全字段路线 (单镜版单写者) — 槽路线对 writer 镜是空转
        # (器层免覆写保护会把 assemble 跳过, "重设计完成"但画面句一字未变,
        # flash 填槽 2.6s/69token 假重做); fallback 镜保持槽路线。
        # 1016 修 (s01 实锤): fallback 镜同入 plan_shot 全字段路线 — 槽路线对
        # 槽词表稳定的镜是假重做 (flash 填槽+assemble 后画面句逐字不变, 用户
        # 连点两次重规划零变化)。writer 身份由路线自然获得。
        if _ss and target is not None:
            for attempt in (1, 2):
                try:
                    doc = director2.plan_shot(book_id, book_title, ep, sid, manifest, bible)
                    from app.services.anim_pipeline.scene_writer import writer_failures
                    _wf = writer_failures(doc, str(target.get("arc_id") or ""))
                    _mine = [f for f in _wf if f.startswith(sid) or sid in f]
                    if not _mine:
                        s2 = next(x for x in doc.get("shots") or [] if x["shot_id"] == sid)
                        s2["writer_source"] = "writer"  # 保持单写者身份
                        # 1014 修 (redo K2 待跑0镜实锤): 重设计成功必须回 planned 清媒体
                        s2["status"] = "planned"
                        s2["image_file"] = None
                        s2["video_file"] = None
                        s2.pop("text_passed", None)  # 新设计须重新入字
                        shots_mod.save(doc)
                        logger.info("[replan-shot] %s ✓ (单写者全字段重设计, plan_shot)", sid)
                        return {"shot_id": sid, "path": "plan_shot", "backup": bak.name}
                    problems = _mine
                except (SystemExit, ValueError, Exception) as exc:  # noqa: BLE001
                    problems = [str(exc)[:200]]
                logger.warning("[replan-shot] %s writer 路线第%d把不过: %s",
                               sid, attempt, "; ".join(problems[:2]))
            # 两败 → 退槽路线 (兜底的兜底, 打标降级)
            logger.warning("[replan-shot] %s writer 路线两败 → 退槽路线 (降级)", sid)
            # 1014 修: writer 失败退槽路线时, 必须摘 writer 标 — 否则 assemble_episode
            # 会因 writer_source=="writer" 跳过此镜 (画面句不变 → strip_media 不重置 → K2 0镜)
            cur = shots_mod.load_doc(book_title, ep)
            tgt = next((x for x in cur.get("shots") or [] if x["shot_id"] == sid), None)
            if tgt is not None and str(tgt.get("writer_source") or "") == "writer":
                tgt["writer_source"] = "fallback"
                shots_mod.save(cur)
                logger.info("[replan-shot] %s writer→fallback (槽路线可组装)", sid)
        # 1010 门禁双修: ①子串匹配升级词边界 (s81 是 s81b 的子串, 旧过滤把
        # 邻居/成对问题全算到本镜头上) ②重掷花字先确定性洗涤再过门 (拆镜
        # 共享口播回声 = LLM 掷几乎必中重复, 不赌运气)
        import re as _re_gate
        _gate = _re_gate.compile(rf"\b{_re_gate.escape(sid)}\b")
        if _ss:
            from app.services.anim_pipeline.assembler import assemble_episode
            from app.services.anim_pipeline.creative_slots import fill_creative_slots
            for attempt in (1, 2):
                try:
                    cur = shots_mod.load_doc(book_title, ep)
                    target = next((x for x in cur.get("shots") or []
                                   if x["shot_id"] == sid), None)
                    if target is None:
                        raise ValueError(f"镜 {sid} 不存在")
                    target.pop("creative_slots", None)  # 重掷: 清旧槽
                    target.pop("slots_source", None)
                    target.pop("political_flag", None)  # 1010: 新keyframe获新扫描(旧标记残留=K2永久跳过)
                    doc = cur
                    fill_creative_slots(doc, _ss, only={sid}, bible=bible)
                    assemble_episode(doc, _ss, bible, only={sid})
                    _sanitize_shot_text(doc, sid)
                    problems = director2.validate_doc(doc)
                    # 0924 修沿袭: 单镜重规划只关注本镜问题 — 其他镜的存量问题不阻断
                    problems = [p for p in problems if _gate.search(str(p))] if problems else []
                except (SystemExit, ValueError) as exc:
                    problems = [str(exc)[:200]]
                if not problems:
                    break
        else:
            for attempt in (1, 2):
                try:
                    doc = director2.plan_shot(book_id, book_title, ep, sid, manifest, bible)
                    _sanitize_shot_text(doc, sid)
                    problems = director2.validate_doc(doc)
                    # 0924 修: 单镜重规划只关注本镜问题 — 其他镜的存量问题不阻断
                    # (s46 被s02/s03花字重复拦 = 单镜修不了别镜的病)
                    problems = [p for p in problems if _gate.search(str(p))] if problems else []
                except (SystemExit, ValueError) as exc:
                    problems = [str(exc)[:200]]
                if not problems:
                    break
        if problems:
            shutil.copy2(bak, path)  # 失败回滚 (品牌剥标一并还原, 盘上未动)
            raise ValueError(f"单镜重规划失败 ×2: {problems[0][:120]} — 盘上未动")
        _st = shots_mod.strip_media_on_narr_change(shots_mod.load_doc(book_title, ep), doc)
        if _st:
            logger.warning("[narr-change] 文案变→媒体失效: %s", _st)
        # 1016 修 (s01 实锤): 重设计成功必须回 planned 清媒体 — 1014 修只落在
        # writer 分支; 槽/兜底路线 "✓" 后状态原样 anim_done → K2 待跑 0 镜。
        _tgt = next((x for x in doc.get("shots") or [] if x["shot_id"] == sid), None)
        if _tgt is not None:
            _tgt["status"] = "planned"
            _tgt["image_file"] = None
            _tgt["video_file"] = None
            _tgt.pop("text_passed", None)
            _tgt.pop("face_fixed", None)
            _tgt.pop("text_erased", None)
        shots_mod.save(doc)
        logger.info("[replan-shot] %s ✓ → planned (旧版备份 %s)", sid, bak.name)
        return {"shot_id": sid, "summary": f"{sid} 已重设计 (图待生成)",
                "backup": bak.name}


def renumber_shots(book_ref: str, ep: int) -> dict[str, Any]:
    """整集镜号重排 (0916 用户令): 按时间轴 s1..sN, 页面/草稿/文件一致.

    两段式文件改名 (anim/{sid}.mp4, img/{sid}.png → tmp → 新名; 目标位上的
    旧规划残留直接覆盖); 品牌资产路径 (非镜 id 命名) 不动; 首末镜 page_type
    重算 C; 自动备份; 返回新旧对照."""
    import os
    import shutil
    import time as _t

    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        shots = sorted(doc.get("shots") or [], key=lambda s: float(s.get("t_start", 0)))
        if not shots:
            raise AnimNotFound("shots 为空")
        base = shots_mod.ep_dir(book_title, ep)
        path = shots_mod.shots_path(book_title, ep)
        bak = path.with_name(f"shots.json.bak_renumber_{_t.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)

        mapping: list[tuple[str, str]] = []
        # Phase A: 全部先挪临时名 (避免新旧名交叉互撞)
        for i, s in enumerate(shots):
            old = str(s["shot_id"])
            new = f"s{i + 1}"
            mapping.append((old, new))
            for d, ext in (("anim", ".mp4"), ("img", ".png")):
                src = base / d / f"{old}{ext}"
                if src.exists():
                    tmp = base / d / f"_rn{i + 1}{ext}"
                    os.replace(src, tmp)
        # Phase B: 临时名 → 终名 (目标位残留 = 旧规划孤儿件, 覆盖安全)
        for i, s in enumerate(shots):
            new = f"s{i + 1}"
            for d, ext in (("anim", ".mp4"), ("img", ".png")):
                tmp = base / d / f"_rn{i + 1}{ext}"
                if tmp.exists():
                    os.replace(tmp, base / d / f"{new}{ext}")
            s["shot_id"] = new
            if s.get("video_file") and f"/{mapping[i][0]}." in f"/{s['video_file']}":
                s["video_file"] = f"anim/{new}.mp4"
            if s.get("image_file") and f"/{mapping[i][0]}." in f"/{s['image_file']}":
                s["image_file"] = f"img/{new}.png"
        for i, s in enumerate(shots):
            s["page_type"] = "C" if i in (0, len(shots) - 1) else "B"
        doc["shots"] = shots
        shots_mod.save(doc)
        logger.info("[renumber] %s ep%d → s1..s%d (备份 %s, 改名 %d 文件)",
                    book_title, ep, len(shots), bak.name,
                    sum(1 for _, n in mapping if (base / "anim" / f"{n}.mp4").exists()))
        return {"shots": len(shots), "backup": bak.name,
                "mapping": [f"{o}→{n}" for o, n in mapping if o != n]}


def produce_opening_asset(book_ref: str, ep: int) -> dict[str, Any]:
    """开场白书级素材生成 (0920 用户令: wav 也是素材, 同图书图片路数).
    装配只读 _资产; 本端点负责生产 (迁移缓存优先, 否则 TTS 合成).
    """
    _book_id, book_title = resolve_ep(book_ref, ep)
    from app.services import anim_draft
    return anim_draft.produce_opening(book_title)


def brand_tag_shot(book_ref: str, ep: int, sid: str, on: bool) -> dict[str, Any]:
    """品牌卡镜手动标/剥 (0916 用户令): 误标剥掉回生成队列, 漏标补上零 GPU 直通."""
    import shutil
    import time as _t

    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        target = next((s for s in doc.get("shots") or [] if s["shot_id"] == sid), None)
        if target is None:
            raise AnimNotFound(f"镜不存在: {sid}")
        path = shots_mod.shots_path(book_title, ep)
        bak = path.with_name(f"shots.json.bak_brandtag_{_t.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        if on:
            target["brand_card"] = True
        else:
            target.pop("brand_card", None)
            target["status"] = "planned"
            target.pop("video_file", None)
            target.pop("image_file", None)
        shots_mod.save(doc)
        logger.info("[brand-tag] %s brand_card=%s (备份 %s)", sid, on, bak.name)
        return {"shot_id": sid, "brand_card": on, "backup": bak.name}


def brand_pass_shot(book_ref: str, ep: int, sid: str) -> dict[str, Any]:
    """品牌卡单镜一键直通 (0921 用户令): 标品牌后卡上无生成出路, 只能回顶栏走
    K2→过审→H3 三步批。直接插定稿 keyframe+视频 → anim_done, 零 GPU 秒级
    (逻辑同 k2/h3 批内直通块, 单镜化)。顺手补 brand_card 标 (漏标直通)。

    0921b 换片态 (ep6 s56 实锤): 已完成镜事后手标品牌 = 哑标 — 装配端生成片
    优先, 旧视频照用, 卡上又无按钮。放开 anim_done 换片: 生成片 → 定稿卡
    (原 mp4 留盘, shots.json 有备份); 仅当前已是定稿卡时 noop。"""
    import shutil
    import time as _t

    from .anim_pipeline import brand as brand_mod

    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        target = next((s for s in doc.get("shots") or [] if s["shot_id"] == sid), None)
        if target is None:
            raise AnimNotFound(f"镜不存在: {sid}")
        brand_video = brand_mod.resolve(book_title, ep, "video")
        if (target.get("status") == "anim_done" and target.get("brand_card")
                and target.get("video_file") == brand_video):
            return {"shot_id": sid, "status": "anim_done", "noop": True}
        swapped = bool(target.get("video_file") and target.get("status") == "anim_done")
        path = shots_mod.shots_path(book_title, ep)
        bak = path.with_name(f"shots.json.bak_brandpass_{_t.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        if swapped:
            logger.info("[brand-pass] %s 生成片 %s 换定稿卡 (原片留盘, 备份 %s)",
                        sid, target.get("video_file"), bak.name)
        target["brand_card"] = True
        target["image_file"] = brand_mod.resolve(book_title, ep, "keyframe")
        target["video_file"] = brand_video
        target["error"] = None
        shots_mod.transition(target, "anim_done")
        shots_mod.save(doc)
        logger.info("[brand-pass] %s 品牌卡直通 (零GPU, 首帧+定稿卡, 备份 %s)", sid, bak.name)
        return {"shot_id": sid, "status": "anim_done", "swapped": swapped,
                "backup": bak.name}


def ai_fix_shot_motion(book_ref: str, ep: int, sid: str, note: str = "") -> dict[str, Any]:
    """AI 动画医生 (0915 用户令): 图/背景动画不动, LLM 按文字纪律重写 motion → 重roll.

    note = 用户随手描述哪里不对 (可空)。改完自动换 seed 回可重roll态。
    """
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.book_service.creation_common import _llm, _parse_json

    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        try:
            s = shots_mod.find_shot(doc, sid)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        beats = (s.get("anim") or {}).get("beats") or []
        if not beats:
            raise ValueError(f"{sid} 无动画节拍")

        cur = chr(10).join(f"拍{i + 1} [{b.get('t_start')}-{b.get('t_end')}s]: {b.get('motion')}"
                           for i, b in enumerate(beats))
        tl = json.dumps(s.get("text_layer") or [], ensure_ascii=False)
        user = ("【首帧 (不动)】" + str(s.get('keyframe_zh') or '')[:150] + chr(10)
                + "【口播】" + str(s.get('narration') or '')[:80] + chr(10)
                + "【已有后期花字 (这些词已走后期, 视频里绝不要再画)】" + tl[:200] + chr(10)
                + "【当前每拍 motion】" + chr(10) + cur + chr(10)
                + "【用户反馈】" + (note or '(自动: 文字动画有问题, 按铁律修正)') + chr(10)
                + f"输出 JSON, {len(beats)} 拍。")
        raw = _llm().chat(AI_FIX_SYS, user, model="pro", temperature=0.3)
        data = _parse_json(raw) or {}
        new_motions = [str(b.get("motion") or "").strip() for b in (data.get("beats") or [])]
        if len(new_motions) != len(beats) or not all(new_motions):
            raise ValueError(f"AI 返回拍数不符或空 (要 {len(beats)} 拍得 {len(new_motions)}) — 重试即可")
        changed = 0
        for b, m in zip(beats, new_motions):
            if m != b.get("motion"):
                b["motion"] = m
                changed += 1
        if s["status"] == "anim_done":
            shots_mod.transition(s, "approved")
        shots_mod.retry_anim(s)  # 换 h3_seed
        shots_mod.save(doc)
        summary = str(data.get("notes") or f"{changed}/{len(beats)} 拍已重写")
        logger.info("[ai-fix] %s 动画医生: %s (图未动, seed已换)", sid, summary)
        return {"shot_id": sid, "changed": changed, "beats": len(beats),
                "notes": summary, "motions": new_motions}


def fix_shot_text(book_ref: str, ep: int, sid: str, word: str = "") -> dict[str, Any]:
    """一键修字: H3 入画字下沉后期 + 换 seed 回可重roll态 (图不动).

    下沉词自动加 text_layer (金色大字档, 拍窗口时机) — 草稿/音效清单自动接手。
    """
    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        try:
            s = shots_mod.find_shot(doc, sid)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        beats = (s.get("anim") or {}).get("beats") or []
        if not beats:
            raise ValueError(f"{sid} 无动画节拍")
        beats, sinked = _sink_text_from_beats(beats, word)
        if not sinked:
            raise ValueError("没检测到入画文字事件 (可显式传 word)")
        tl = s.setdefault("text_layer", [])
        for w in sinked:
            # 时机: 首拍窗口起, 2.5s 一闪
            b0 = float(beats[0].get("t_start") or 0)
            tl.append({"kind": "hero_number", "text": w,
                       "t_start": round(b0 + 0.3, 2),
                       "t_end": round(b0 + 2.8, 2)})
        if s["status"] == "anim_done":
            shots_mod.transition(s, "approved")
        shots_mod.retry_anim(s)  # 换 h3_seed
        shots_mod.save(doc)
        logger.info("[fix-text] %s 入画字下沉后期: %s (换seed, 图未动)", sid, "/".join(sinked))
        return {"shot_id": sid, "sinked": sinked,
                "summary": f"{'/'.join(sinked)} 已转后期金字 · seed已换 · 🎲重roll生效"}


def update_shot_anim(book_ref: str, ep: int, sid: str, motions: list[str]) -> dict[str, Any]:
    """逐镜动画描述编辑 (0915 文字乱码根治工作流: 图满意只改 motion 结构后重roll).

    motions = 每拍一条 (顺序对应 beats); 空串保留原拍。图/状态全不动。
    """
    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        try:
            s = shots_mod.find_shot(doc, sid)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        beats = (s.get("anim") or {}).get("beats") or []
        if len(motions) != len(beats):
            raise ValueError(f"拍数不符: 传 {len(motions)} 条, 实有 {len(beats)} 拍")
        changed = 0
        for b, m in zip(beats, motions):
            m = str(m or "").strip()
            if m and m != b.get("motion"):
                b["motion"] = m
                changed += 1
        if changed:
            shots_mod.save(doc)
        return {"shot_id": sid, "beats": len(beats), "updated": changed,
                "summary": f"{changed}/{len(beats)} 拍已改 (图未动, 重roll生效)"}


def update_text_layer(book_ref: str, ep: int, sid: str, entries: list[dict] | None) -> dict[str, Any]:
    """逐镜花字编辑 (规划没给的字后期随时补): text_layer = 剪映字幕轨内容+镜内时机.

    草稿装配自动按暖金色上字幕轨; 花字特效模板 (描边/弹跳/综艺字) 在剪映里手动套.
    """
    book_id, book_title = resolve_ep(book_ref, ep)
    with ep_guard(book_id, ep):
        doc = shots_mod.load_doc(book_title, ep)
        try:
            s = shots_mod.find_shot(doc, sid)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        cleaned = []
        _kinds = {"title", "caption", "hero_number", "year", "impact", "logo_note", "typewriter"}
        for e in entries or []:
            text = str(e.get("text") or "").strip()
            if not text:
                continue
            try:
                t0, t1 = float(e.get("t_start") or 0), float(e.get("t_end") or 0)
            except (TypeError, ValueError):
                t0, t1 = 0.0, 0.0
            # 0917 P1 修复: 花字 kind 不再塌缩 — hero_number 等合法 kind 原样保留
            # (装配层 hero_number/impact/logo_note 降维进字幕高亮, kind 决定动效与角色)
            kind = str(e.get("kind") or "caption")
            cleaned.append({"kind": kind if kind in _kinds else "caption",
                            "text": text, "t_start": round(t0, 2), "t_end": round(t1, 2)})
        s["text_layer"] = cleaned
        shots_mod.save(doc)
        return {"shot_id": sid, "text_layer": cleaned}


# ── 状态看板 (shots.json 是持久层, 重启即恢复) ─────────────────

def status_view(book_ref: str, ep: int) -> dict[str, Any]:
    book_id, book_title = resolve_ep(book_ref, ep)
    view: dict[str, Any] = {"book_id": book_id, "book_title": book_title, "ep": ep,
                            "planned": False, "running_job": None, "audio_ready": False}
    # 音频先行门控信号: 无已验收口播 → 规划禁用 (动画线动线: 文字→音频→分镜→动画)
    try:
        from app.services import anim_draft
        view["audio_ready"] = bool(anim_draft.fetch_episode_audio(book_id, ep).get("files"))
    except Exception:  # noqa: BLE001 — 音频未就绪是合法状态, 不是错误
        view["audio_ready"] = False
    with _JOBS_LOCK:
        running = next((j for j in _JOBS.values()
                        if j["book_id"] == book_id and j["ep"] == ep and j["status"] == "running"), None)
        if running:
            view["running_job"] = _job_view(running)
    try:
        doc = shots_mod.load_doc(book_title, ep)
    except FileNotFoundError:
        return view
    base = ep_base_dir(book_title, ep)
    r_flags = _load_review_flags(book_title, ep)   # 1010 R 标: 重做待复查
    counts: dict[str, int] = {}
    shots_out = []
    for s in doc["shots"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
        img_rel = s.get("image_file") if s.get("image_file") and (base / s["image_file"]).exists() else None
        vid_rel = s.get("video_file") if s.get("video_file") and (base / s["video_file"]).exists() else None
        shots_out.append({
            "shot_id": s["shot_id"], "label": s.get("label", ""), "page_type": s.get("page_type"),
            "status": s["status"], "brand_card": bool(s.get("brand_card")),
            "arc_id": s.get("arc_id"), "group_id": s.get("group_id"),
            "camera": s.get("camera", ""),
            "redo_review": s["shot_id"] in r_flags,  # 1010 R 标: 重做待复查
            # 1015 修 (UI 实锤: FB 红标从未显示 — 投影白名单漏字段, 前端
            # s.writer_source==='fallback' 永假): 写侧契约字段必须同步读侧投影
            "writer_source": s.get("writer_source") or "",
            "in_image_text": s.get("in_image_text") or [],   # 二段式申报 (页面 QC 可见)
            "h3_text_event": s.get("h3_text_event") or {},    # H3 事件字 (文字三路由)
            "slots_source": s.get("slots_source"),           # 默认槽降级可见 (同族)
            # 1006 修 (ep1 审阅实锤): [:200] 视图截断误导逐镜细审 ("19/45 断在200"=
            # 全文被砍的假象, H3 吃盘上全文零影响) — 视图不再截, 交前端按需折行
            "motion_zh": (s.get("motion_zh") or ""),
            "t_start": s.get("t_start"), "t_end": s.get("t_end"),
            "narration": (s.get("narration") or ""),
            "zone": s.get("zone"), "scene_tag": s.get("scene_tag"),
            "image_prompt_zh": s.get("image_prompt_zh") or "",
            "text_layer": s.get("text_layer") or [],
            "overrun_s": s.get("overrun_s"),
            "image_file": img_rel, "video_file": vid_rel,
            "attempts": s.get("attempts", {}), "reject_note": s.get("reject_note", ""),
            "error": s.get("error"),
            "video_suspect": s.get("video_suspect"),  # 0927 P1-B 视频机检标记
            # 1008 P8: 法层参数 (每镜卡片灰标签 ELS·鸟瞰·窗光) + 术层槽 (action 展示)
            "fa": s.get("fa"),
            "creative_slots": s.get("creative_slots"),
            # 0917 P1 修复: 🎞动画逐拍编辑需要 beats (此前死 UI — status 不回 anim)
            "anim": ({"duration_s": (s.get("anim") or {}).get("duration_s"),
                      "beats": [{"t_start": b.get("t_start"), "t_end": b.get("t_end"),
                                 "motion": (b.get("motion") or "")[:120]}
                                for b in (s.get("anim") or {}).get("beats") or []]}
                     if isinstance(s.get("anim"), dict) else None),
        })
    view.update({
        "planned": True, "ep_title": doc.get("ep_title", ""),
        "counts": counts, "total": len(shots_out), "shots": shots_out,
        "aligned": any("t_start_orig" in s for s in doc["shots"]),
        "arcs": doc.get("arcs") or [],
        "groups": doc.get("groups") or [],
        "bible": doc.get("bible") or {},
        "style_recipe": doc.get("style_recipe") or "",
        "opening_sec": anim_cfg_load().brand_card.opening_sec,
        "ep_dir": str(base),
    })
    return view
