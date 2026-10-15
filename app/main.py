"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
import shutil
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
# (BaseHTTPMiddleware removed — 纯 ASGI 中间件不需要它)

from .config import Config, load_config, set_config
from .database import init_db
from .models import AudioJob, Host, Voice, DirectorJob
from .routers import (
    anim, articles, audio, books, comfyui, digital_human_video, hosts, jobs, library, materials, personas, ppt,
    reverse, roles,
    scripts, tagging, tts_services, visual_render, voices,
)
from .routers.director_routes import router as director_router

logger = logging.getLogger(__name__)

# ── 终端日志可见性配置 ──────────────────────────────────────────
# 之前 root logger 无 handler → 只有 WARNING 级能上屏 (lastResort handler),
# [tagging-job] xx/yy 等 INFO 日志全被吞; 而 torch DataLoader 的
# pin_memory UserWarning (warning 级, 独立走 stderr) 却刷屏。
# 这里给 root 挂 INFO handler, 让打标/抽帧进度日志在后台窗口可见。
# (入口脚本已 basicConfig 时 root 已有 handler → no-op, 不重复配置)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
# torch DataLoader 无加速器时的 'pin_memory' 提示纯属噪音, 精确屏蔽
warnings.filterwarnings("ignore", message=r".*pin_memory.*", category=UserWarning)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    set_config(cfg)
    _config = cfg
    init_db(_config.app.database_url)
    # 注册 event loop 给 director_events 跨线程安全调度
    import asyncio
    from .services.director_events import set_event_loop
    set_event_loop(asyncio.get_running_loop())
    _seed_defaults()
    _cleanup_zombie_jobs()
    _cleanup_zombie_audio_jobs()
    _auto_cleanup_stale_jobs()
    # 残留抽帧目录回收: 上次进程被强杀(taskkill //F)时 daemon 线程中断,
    # TemporaryDirectory 未删除 → 临时帧 PNG 孤儿残留累积磁盘。
    # 后端刚启动不可能有打标在跑, 此刻存在的 tagging_frames_* 必为孤儿, 送回收站。
    try:
        from .services.video_tagging_service.cleanup import cleanup_orphan_tagging_frames

        _cleaned = cleanup_orphan_tagging_frames()
        if _cleaned:
            logger.info("[lifespan] 清理 %d 个残留抽帧目录", _cleaned)
    except Exception as exc:
        logger.warning("[lifespan] tagging cleanup failed: %s", exc)
    # 内存集合残留清理: 重启后僵尸 job 已复位, force/cancel 标记与进程注册表
    # 同步清空, 防止旧进程句柄 / 脏标记干扰新会话 (见 force-stop 实现)。
    try:
        from .services.slot_executor import clear_force_stopped_all, clear_cancel_flags_all
        clear_force_stopped_all()
        clear_cancel_flags_all()
        from .services.proc_registry import clear_all
        clear_all()
    except Exception as exc:
        logger.warning("[lifespan] memory cleanup failed: %s", exc)
    # workflow SSOT 同步 (项目 → ComfyUI 运行时)
    try:
        from .services.workflow_sync import sync_workflows_on_startup

        results = sync_workflows_on_startup()
        logger.info("[lifespan] workflow_sync: %d entries", len(results))
    except Exception as exc:
        logger.warning("[lifespan] workflow_sync failed: %s", exc)
    yield
    # 退出时取消活跃打标任务 — uvicorn 优雅关闭只停 HTTP, 不打标 daemon 线程,
    # 否则已 submit 的素材线程池全跑完, 进程退不出 ("Shutting down" 后几分钟
    # 停不下来)。配合 jobs.py 分批提交, 取消后至多再跑完当前批 (<BATCH) 即停。
    try:
        from .services.video_tagging_service.jobs import cancel_all_active_jobs

        _n = cancel_all_active_jobs()
        if _n:
            logger.info("[lifespan] 已请求取消 %d 个进行中打标任务", _n)
    except Exception as exc:
        logger.warning("[lifespan] tagging cancel failed: %s", exc)
    # 退出时杀掉托管的 TTS 进程, 不留孤儿占显存
    try:
        from .services import gpu_service_manager

        if gpu_service_manager._manager is not None:
            gpu_service_manager._manager.shutdown()
    except Exception as exc:
        logger.warning("[lifespan] gpu_service_manager shutdown failed: %s", exc)


def _seed_defaults():
    """Seed default host and voice if they do not exist.

    2026-08-08 人物即账号: 默认 host 不再硬编码"老陈聊财经"。品牌字段
    (brand_name/stamp_name/brand_tag) 及开结尾统一在「人物」页编辑, 此处
    仅 seed 一个中性 host (persona_key=laochen 保留为 config 默认键), 供
    无账号时洗稿/渲染走 config 默认回退。绑定到 persona 后取数走 persona。
    2026-08-07 主路径转正: 默认音色 = 带 master 的 indextts 音色 (大学教授 2267aeae),
    fish laochen_default 已弃用不再重建。host 无 default_voice_id 或指向孤儿时,
    自动绑定到第一个带 master 的 indextts 音色 (保证 generate_audio 不落到 voice=None)。
    """
    from .database import db_session, get_session_maker

    if get_session_maker() is None:
        return
    with db_session() as db:
        host = db.query(Host).filter(Host.persona_key == "laochen").first()
        if not host:
            host = Host(
                name="数字人频道",
                persona_key="laochen",
                fixed_opening=None,
                fixed_ending=None,
                brand_name=None,
                stamp_name=None,
                brand_tag=None,
            )
            db.add(host)
            db.flush()

        # 已弃用: fish laochen_default 不再自动重建。
        # 旧库若已存在同名 fish 音色(带 host_id), 删除以切断"默认走 fish"路径。
        stale_voice = db.query(Voice).filter(Voice.name == "laochen_default").first()
        if stale_voice and stale_voice.backend == "fish":
            db.delete(stale_voice)

        # host 默认音色自愈: 孤儿引用 / 未设置 → 绑定到带 master 的 indextts 音色
        needs_fix = host.default_voice_id is None
        if host.default_voice_id and not db.query(Voice).filter(Voice.id == host.default_voice_id).first():
            needs_fix = True
        if needs_fix:
            default_voice = (
                db.query(Voice)
                .filter(Voice.backend == "indextts", Voice.master_audio_path.isnot(None))
                .order_by(Voice.created_at)
                .first()
            )
            if default_voice:
                host.default_voice_id = default_voice.id

        db.commit()


def _cleanup_zombie_jobs():
    """Reset zombie director jobs left in executing/planning after a restart.

    - planning → failed (alignment/LLM was interrupted)
    - executing with slots → reviewing (slots were planned but execution thread died)
    - executing without slots → failed
    """
    from .database import db_session, get_session_maker

    if get_session_maker() is None:
        return
    with db_session() as db:
        zombies = (
            db.query(DirectorJob)
            .filter(DirectorJob.status.in_(["executing", "planning"]))
            .all()
        )
        if not zombies:
            return
        for job in zombies:
            has_slots = len(job.slots) > 0
            if job.status == "planning":
                job.status = "failed"
                job.error_message = "服务重启时规划未完成，请重新创建任务"
            elif has_slots:
                job.status = "reviewing"
                job.error_message = None
            else:
                job.status = "failed"
                job.error_message = "服务重启时执行未完成，无 slots 可恢复"
        db.commit()
        logger.info("[lifespan] cleaned %d zombie director job(s)", len(zombies))


def _cleanup_zombie_audio_jobs():
    """Reset zombie audio jobs stuck in pending/running after a restart.

    背景: 后端重启会杀掉 _do_tts 后台线程, 但 DB 里 AudioJob 仍标记
    pending/running → 永不完成, 前端 restoreAudioJobs 会一直"恢复订阅"
    这个死任务而不是新建, 且 director 台看不到音频 → 永远卡死.

    策略: 重启时把残留的 pending/running 复位为 failed (附错误原因),
    让用户重新触发生成. 不删记录, 保留可追溯的失败历史.
    """
    from .database import db_session, get_session_maker

    if get_session_maker() is None:
        return
    with db_session() as db:
        zombies = (
            db.query(AudioJob)
            .filter(AudioJob.status.in_(["pending", "running", "cancelling"]))
            .all()
        )
        if not zombies:
            return
        for job in zombies:
            if job.status == "cancelling":
                # 2026-09-02: 停止请求已收到但线程随重启被杀 → 按停止落定, 不算失败
                job.status = "cancelled"
                job.error_message = "服务重启中断 — 已按停止处理，已生成音频保留"
            else:
                job.status = "failed"
                job.error_message = "服务重启中断 TTS 任务，已标记失效，请重新生成音频"
        db.commit()
        logger.info("[lifespan] cleaned %d zombie audio job(s)", len(zombies))


def _auto_cleanup_stale_jobs():
    """Auto-delete director jobs that never produced any video and are older than N days.

    Criteria:
    - No slot with status 'completed'
    - created_at older than config.defaults.job_auto_cleanup_days
    - Status in (failed, planning, reviewing) — not executing (safety)
    """
    from datetime import datetime, timezone, timedelta
    from .database import db_session, get_session_maker
    from .config import get_config

    cfg = get_config()
    days = cfg.defaults.job_auto_cleanup_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    if get_session_maker() is None:
        return
    with db_session() as db:
        candidates = (
            db.query(DirectorJob)
            .filter(
                DirectorJob.created_at < cutoff,
                DirectorJob.status.in_(["failed", "planning", "reviewing"]),
            )
            .all()
        )
        deleted = 0
        for job in candidates:
            # 检查是否有任何 completed slot
            has_completed = any(s.status == "completed" for s in job.slots)
            if not has_completed:
                db.delete(job)
                deleted += 1
                # 同步清理磁盘产物
                try:
                    comp_dir = Path(cfg.defaults.composition_output_root) / job.id
                    if comp_dir.exists():
                        shutil.rmtree(comp_dir, ignore_errors=True)
                    legacy_dir = Path(cfg.defaults.director_output_root) / job.id
                    if legacy_dir.exists():
                        shutil.rmtree(legacy_dir, ignore_errors=True)
                except Exception:
                    pass
        if deleted:
            db.commit()
            logger.info("[lifespan] auto-cleaned %d stale director job(s) (no output, >%d days)", deleted, days)

        # ── 素材保留策略 (2026-08-07): completed job 的 slot 素材保留 N 天后回收 ──
        # 保留策略下 slots/ 不再由合成成功路径删除 (composition_service Step 7 已移除),
        # 素材默认保留 slot_retention_days 天; 超过后由本扫描回收 slots/ + hf_visual +
        # 孤儿中间件, 但保留成片 director_<job_id>.mp4 + manifest, 便于用户回溯。
        retention_days = cfg.defaults.slot_retention_days
        if retention_days > 0:
            retention_cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            retained = (
                db.query(DirectorJob)
                .filter(
                    DirectorJob.status == "completed",
                    DirectorJob.completed_at < retention_cutoff,
                )
                .all()
            )
            swept = 0
            for job in retained:
                # 防误删: 只扫 completed job; 任何重试先把 status 置回 reviewing → 移出范围。
                # 正在 composing 的 job 状态为 composing/reviewing, 不在此范围, 安全。
                comp_root = Path(cfg.defaults.composition_output_root) / job.id
                slots_dir = comp_root / "slots"
                if slots_dir.exists() and slots_dir.is_dir():
                    try:
                        shutil.rmtree(slots_dir, ignore_errors=True)
                        swept += 1
                        logger.info("[lifespan] retention sweep: removed slots/ for job %s (>%dd)", job.id, retention_days)
                    except Exception:
                        pass
                # hf_visual 独立 workspace: <hf_visual_root>/<job_id>/, 与 job 同步回收
                hf_dir = Path(cfg.defaults.hf_visual_root) / job.id
                if hf_dir.exists() and hf_dir.is_dir():
                    try:
                        shutil.rmtree(hf_dir, ignore_errors=True)
                    except Exception:
                        pass
                # 孤儿中间件 (director_*.mp4 / manifest / 洗稿.txt 之外), 保留成片与洗稿文本
                if comp_root.exists() and comp_root.is_dir():
                    kept = {f"director_{job.id}.mp4", "composition_manifest.json", "洗稿.txt"}
                    for f in comp_root.iterdir():
                        if f.is_file() and f.name not in kept:
                            try:
                                f.unlink()
                            except Exception:
                                pass
            if swept:
                logger.info("[lifespan] retention sweep removed slots/ for %d completed job(s) (>%d days)", swept, retention_days)


def create_app() -> FastAPI:
    app = FastAPI(title="Digital Human Pipeline", lifespan=lifespan)

    # Force no-cache on /web/ static files so browser always gets latest HTML
    # NOTE: 不能用 BaseHTTPMiddleware — 它会缓冲请求体导致大文件上传失败。
    # 改用纯 ASGI 中间件，仅修改响应头，不触碰请求体。
    class NoCacheWebMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or not scope.get("path", "").startswith("/web/"):
                await self.app(scope, receive, send)
                return

            async def send_with_no_cache(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"))
                    headers.append((b"pragma", b"no-cache"))
                    headers.append((b"expires", b"0"))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_no_cache)

    app.add_middleware(NoCacheWebMiddleware)

    # 0919 anim 严格 CSP (已替换上线): anim.html/anim.css/anim/ 三前缀精确圈定。
    # 其余 /web 页 (books 等) 有内联脚本, 不可扩大范围。
    # 回滚开关 (env ANIM_RF_CSP): 1=强制(默认) / report=仅上报不拦截 / 0=完全关闭。
    # 页面被 CSP 打坏时: ANIM_RF_CSP=0 重启即恢复, 无需改代码。
    _csp_mode = os.environ.get("ANIM_RF_CSP", "1")
    _anim_csp_paths = ("/web/anim.html", "/web/anim.css", "/web/anim/")
    _anim_rf_csp = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )

    class AnimRfCspMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if (_csp_mode == "0" or scope["type"] != "http"
                    or not scope.get("path", "").startswith(_anim_csp_paths)):
                await self.app(scope, receive, send)
                return

            _header = (b"content-security-policy-report-only" if _csp_mode == "report"
                       else b"content-security-policy")

            async def send_with_csp(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((_header, _anim_rf_csp.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_csp)

    app.add_middleware(AnimRfCspMiddleware)
    app.include_router(articles.router)
    app.include_router(scripts.router)
    app.include_router(audio.router)
    app.include_router(jobs.router)
    app.include_router(hosts.router)
    app.include_router(voices.router)
    app.include_router(comfyui.router)
    app.include_router(roles.router)
    app.include_router(digital_human_video.router)
    app.include_router(visual_render.router)
    app.include_router(director_router)
    app.include_router(library.router)
    app.include_router(reverse.router)
    app.include_router(tagging.router)
    app.include_router(personas.router)
    app.include_router(tts_services.router)
    app.include_router(materials.router)
    app.include_router(books.router)
    app.include_router(ppt.router)
    app.include_router(anim.router)

    # Convenience redirect: /api/templates → /api/visual-render/templates
    @app.get("/api/templates")
    def _redirect_templates():
        return RedirectResponse(url="/api/visual-render/templates", status_code=307)

    # favicon — 阻止浏览器每次请求都打 404
    @app.get("/favicon.ico")
    def _favicon():
        from fastapi.responses import Response
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<circle cx="16" cy="16" r="14" fill="#3b82f6"/>'
            '<text x="16" y="22" text-anchor="middle" font-size="18" fill="#fff">DH</text>'
            '</svg>'
        )
        return Response(content=svg, media_type="image/svg+xml")

    # parents[0] == app/, parents[1] == project root (digital_human/)
    web_dir = Path(__file__).absolute().parents[1] / "web"
    app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()


# 默认端口由 54321 改为 54323 — 看板 P0-1 修复
# (2026-07-26 自检发现 54321 是 orphan socket,真实生产服务在 54323)
DEFAULT_PORT = 54323


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=DEFAULT_PORT,
        log_level="info",
    )
