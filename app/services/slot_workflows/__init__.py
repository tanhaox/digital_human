"""Slot workflow 包: 聚合分发表 WORKFLOW_HANDLERS.

slot_executor 通过 ``from app.services.slot_workflows import WORKFLOW_HANDLERS``
分发执行。旧文件 ``app/services/slot_workflows.py`` 已删除 — 与包同名时
Python 导入永远解析到包, shim 是不可达死代码, 故不留 shim。

模块布局 (按 workflow 族 + 职责细分):
  common.py      目录/音频路径/HF 模板辅助
  host.py        C 线组装 (execute_host_slot)
  host_qa.py     C 线分镜图 QA (prepare_comfyui_storyboard)
  host_comfy.py  C 线 ComfyUI 提交/中断/心跳/轮询
  broll.py       L 线本地素材匹配 (execute_broll_local_slot)
  broll_pexels.py P 线 pexels 搜索 (execute_broll_pexels_slot)
  fallback.py    L 线 black 兜底 (execute_black_placeholder_slot)
  hf.py          H 线执行入口 (execute_hf_visual_slot)
  hf_extract.py  H 线口播文本提取
  hf_chart.py    H 线图表归一化
  mixed.py       C 线 host+broll ffmpeg 合成
"""
from __future__ import annotations

from app.services.slot_workflows.broll import execute_broll_local_slot
from app.services.slot_workflows.broll_pexels import execute_broll_pexels_slot
from app.services.slot_workflows.common import HF_TEMPLATE_ID, HF_TEMPLATE_ID_LS
from app.services.slot_workflows.evidence_image import execute_evidence_image_slot
from app.services.slot_workflows.fallback import execute_black_placeholder_slot
from app.services.slot_workflows.hf import execute_hf_visual_slot
from app.services.slot_workflows.host import execute_host_slot
from app.services.slot_workflows.mixed import execute_mixed_host_broll_slot

WORKFLOW_HANDLERS = {
    "host": execute_host_slot,
    "broll_pexels": execute_broll_pexels_slot,
    "broll_local": execute_broll_local_slot,
    "hf_chart": execute_hf_visual_slot,
    "hf_title": execute_hf_visual_slot,
    "hf_opening": execute_hf_visual_slot,
    "hf_quote": execute_hf_visual_slot,
    "hf_identity": execute_hf_visual_slot,
    "hf_follow": execute_hf_visual_slot,
    "mixed_host_broll": execute_mixed_host_broll_slot,
    "black_placeholder": execute_black_placeholder_slot,
    "evidence_image": execute_evidence_image_slot,
}

__all__ = [
    "WORKFLOW_HANDLERS",
    "HF_TEMPLATE_ID",
    "HF_TEMPLATE_ID_LS",
    "execute_host_slot",
    "execute_broll_pexels_slot",
    "execute_broll_local_slot",
    "execute_hf_visual_slot",
    "execute_mixed_host_broll_slot",
    "execute_black_placeholder_slot",
    "execute_evidence_image_slot",
]
