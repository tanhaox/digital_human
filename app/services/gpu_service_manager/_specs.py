"""GPU 服务部署事实 + 模型定义 + 配置构建.

内置默认 (可被 config/app.yaml `tts_services.<key>` 覆盖):
  fish     E:/AI/tts/fish-speech       tools/api_server.py  :7860  /v1/health
  f5       E:/AI/tts/F5-TTS            f5-tts_infer-gradio  :7861  /
  indextts E:/AI/tts/index-tts2.5     api_server.py        :7862  /health
  comfyui  E:/AI/ComfyUI_windows_portable main.py           :8188  /system_stats
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ServiceSpec", "build_specs", "NO_LOCAL_SERVICE"]


# ── 内置部署事实 ──────────────────────────────────────────────
_BUILTIN_SPECS: dict[str, dict[str, Any]] = {
    "fish": {
        "display_name": "Fish Speech",
        "base_url": "http://127.0.0.1:7860",
        "health_path": "/v1/health",
        "cwd": "E:/AI/tts/fish-speech",
        "command": [
            ".venv/Scripts/python.exe", "tools/api_server.py",
            "--listen", "127.0.0.1:7860",
            "--llama-checkpoint-path", "E:/AI/tts/models/s2-pro",
            "--decoder-checkpoint-path", "E:/AI/tts/models/s2-pro/codec.pth",
            "--decoder-config-name", "modded_dac_vq",
            "--device", "cuda",
        ],
        "env": {},
    },
    "f5": {
        "display_name": "F5-TTS",
        "base_url": "http://127.0.0.1:7861",
        "health_path": "/",
        "cwd": "E:/AI/tts/F5-TTS",
        "command": [
            ".venv/Scripts/f5-tts_infer-gradio.exe",
            "--host", "127.0.0.1", "--port", "7861",
        ],
        "env": {},
    },
    "indextts": {
        # 2026-08-25 回退产线至 IndexTTS2: 2.5 语速/韵律/情绪联动仍需联调, 不可投产。
        # 2.5 安装保留在 E:/AI/tts/index-tts2.5 (实验位), 联调完成后改回:
        #   cwd=E:/AI/tts/index-tts2.5 + HF_HOME 同目录 + display_name IndexTTS2.5
        # 端口与接口两版一致。
        "display_name": "IndexTTS2",
        "base_url": "http://127.0.0.1:7862",
        "health_path": "/health",
        "cwd": "E:/AI/tts/index-tts-windows",
        "command": [
            ".venv/Scripts/python.exe", "api_server.py",
            "--port", "7862", "--host", "127.0.0.1",
        ],
        # 与 api_server 内部一致: 清 PYTHONPATH + HF 镜像
        "env": {
            "PYTHONPATH": "",
            "HF_ENDPOINT": "https://hf-mirror.com",
            "HF_HOME": "E:/AI/tts/index-tts-windows/.huggingface",
        },
    },
    "indextts25": {
        # IndexTTS-2.5 老谭读书专用通道 (2026-09-10; 案卷 memory indextts-25-upgrade)
        # 与产线 indextts (7862, IndexTTS2) 完全独立 — 静读书/新闻线不受影响。
        "display_name": "IndexTTS2.5",
        "base_url": "http://127.0.0.1:7866",
        "health_path": "/health",
        "cwd": "E:/AI/tts/index-tts2.5",
        "command": [
            ".venv/Scripts/python.exe", "api_server.py",
            "--port", "7866", "--host", "127.0.0.1",
        ],
        "env": {
            "PYTHONPATH": "",
            "HF_ENDPOINT": "https://hf-mirror.com",
            "HF_HOME": "E:/AI/tts/index-tts2.5/.huggingface",
        },
    },
    "comfyui": {
        "display_name": "ComfyUI",
        "base_url": "http://127.0.0.1:8188",
        "health_path": "/system_stats",
        "cwd": "E:/AI/ComfyUI_windows_portable",
        "command": [
            "python_embeded/python.exe", "-s", "ComfyUI/main.py",
            "--windows-standalone-build", "--listen", "127.0.0.1",
            # 0915 花屏/1450 根治: H3 模型栈 51GB > 物理内存 47.6GB, 默认 pinned-RAM
            # 卸载在超卖时 GetOverlappedResult 1450 (硬错) 或静默脏权重 (噪声废片).
            # --fast-disk = 磁盘页缓存背书 (pageable 可回收), NVMe 直读 0.2s/64MB 实测健康.
            "--fast-disk",
        ],
        # 0920 根治 (ep5 46镜全灭案): 序号锚定翻车 — Whisper 对齐在[后端进程]设
        # CUDA_DEVICE_ORDER=PCI_BUS_ID (alignment_service/_models.py), _launch 的
        # dict(os.environ) 原样继承 → PCI 序下 0=4060 → ComfyUI 落 8GB 卡,
        # K2 17.5G 栈 cuDNN SUBLIBRARY_VERSION_MISMATCH + access violation 崩溃环.
        # 改锚 4090 的 GPU-UUID: 与枚举序完全无关, 双保险见 _lifecycle 剥离继承.
        "env": {"CUDA_VISIBLE_DEVICES": "GPU-722b3d27-419c-2698-89a1-28a44ae2efd0"},
        # 0917 用户令: ComfyUI 冷启实测 82s (51GB 模型栈) — 全局 180s 空闲即杀
        # 在人审/重roll节奏下 = "拉起→干2-7笔→再拉起" churn 主源; 单独保温 30min.
        # TTS 小栈维持全局 idle_timeout_sec (可被 app.yaml tts_services.comfyui 覆盖).
        "idle_timeout_sec": 1800,
    },
}

# elevenlabs 等云端后端不占本地 GPU, session 直接放行
NO_LOCAL_SERVICE = {"elevenlabs"}


@dataclass
class ServiceSpec:
    key: str
    display_name: str
    base_url: str
    health_path: str
    cwd: Path
    command: list[str]
    env: dict[str, str]
    # None = 跟随管理器全局 idle_timeout_sec; 数值 = 本服务独立空闲关停阈值
    # (0917: ComfyUI 51GB 栈冷启 82s, 单独保温; 0 = session 结束立即关)
    idle_timeout_sec: float | None = None

    @property
    def health_url(self) -> str:
        return self.base_url.rstrip("/") + self.health_path


def build_specs(raw_cfg: dict[str, Any]) -> tuple[dict[str, ServiceSpec], dict[str, Any]]:
    """内置默认 + app.yaml `tts_services` 覆盖 → ServiceSpec 表."""
    svc_cfg = raw_cfg.get("tts_services") or {}
    specs: dict[str, ServiceSpec] = {}
    for key, builtin in _BUILTIN_SPECS.items():
        override = svc_cfg.get(key) or {}
        merged = {**builtin, **override}
        _idle = merged.get("idle_timeout_sec")
        specs[key] = ServiceSpec(
            key=key,
            display_name=merged["display_name"],
            base_url=merged["base_url"],
            health_path=merged["health_path"],
            cwd=Path(merged["cwd"]),
            command=list(merged["command"]),
            env=dict(merged.get("env") or {}),
            idle_timeout_sec=float(_idle) if _idle is not None else None,
        )
    return specs, svc_cfg
