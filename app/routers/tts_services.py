"""TTS GPU 服务托管状态与手动控制."""
from __future__ import annotations

from fastapi import APIRouter

from ..services.gpu_service_manager import get_gpu_service_manager

router = APIRouter(prefix="/api/tts-services", tags=["tts-services"])


@router.get("/status")
def services_status():
    """各 TTS 后端健康/托管/排队状态."""
    return get_gpu_service_manager().status()


@router.post("/stop-all")
def stop_all_services():
    """手动停止所有托管的 TTS 服务, 立即腾出显存 (外部启动的不动)."""
    stopped = get_gpu_service_manager().stop_all()
    return {"stopped": stopped}
