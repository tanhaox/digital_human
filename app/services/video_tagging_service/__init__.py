"""视频素材 AI 打标编排器 — 4 通道智能分析流水线 → LLM 综合打标 → 写入数据库.

流水线:
  通道1: 智能抽帧 (scdet 场景检测, 上限12帧)
  通道2: 运动分析 (scdet 低阈值频率统计, 零AI)
  通道3: OCR 预扫 (EasyOCR + 国旗色块, 可选)
  通道4: LLM 综合打标 (拿到帧 + 通道2/3结果)

后台线程执行, 进度通过 director_events 推送.
打标完成/取消后自动重建关键词词表包 (ID-034), 新标签进入包, 导演下次规划即可命中.

包布局:
  constants.py  — 标签枚举 / SYSTEM_PROMPT / llama-server 常量
  parse.py      — LLM 输出 JSON 解析与规则引擎回退
  llama.py      — llama-server 自动拉起
  legacy_frames.py — 已废弃的等间距抽帧 (向后兼容)
  pipeline.py   — 单素材 4 通道流水线
  store.py      — 标签写库 + 词表包重建
  jobs.py       — 批量任务状态机 / 线程 / 公共 API (start_tagging_job 等)
  single.py     — 同步单素材打标 (tag_single_asset)
"""
from __future__ import annotations

from app.services.video_tagging_service.jobs import (
    cancel_job,
    get_job_status,
    start_tagging_job,
)
from app.services.video_tagging_service.single import tag_single_asset

__all__ = [
    "start_tagging_job",
    "tag_single_asset",
    "get_job_status",
    "cancel_job",
]
