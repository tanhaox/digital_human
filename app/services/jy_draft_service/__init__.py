# -*- coding: utf-8 -*-
"""J 线草稿导出服务 — 导演 job / PPT / 元素级 → 剪映明文草稿 (J1, 2026-08-15).

设计: docs/design/剪映草稿产线-设计方案.md §4
- slot 时间轴直接映射草稿 video 轨 (微秒制, source/target 双坐标系)
- TTS 分段 wav 逐段进 audio 轨 (不聚合, 段落级可在剪映再调)
- manifest 逐段文本进 text 轨 (字幕层)
- 音画不预合成; 渲染出口交给剪映 (人工审 + 调 BGM + 导出)

写入路径结论 (实验 A 验证): 新版剪映"打开时接受明文、保存时才加密",
pyJianYingDraft 生成的明文 draft_content.json 可直接被剪映打开。

模块布局 (2026-09-01 自单文件 1594 行拆包, 函数体原样搬运零行为变更;
旧 app/services/jy_draft_service.py 已删 — 与包同名时 Python 永远解析到包,
shim 不可达故不留, 本 __init__ 即转发层):
  common.py          _US/时间轴/manifest/_drafts_dir + jy config 加载
  subtitle_text.py   字幕洗涤纯文本 (wash/split/highlight/中文数字)
  subtitle_style.py  字幕样式常量 + _StyledTextSegment (内联划重点)
  sfx.py             音效库路径/挂载 + 时长探测
  auto_choreo.py     R9 自动编排 (语义分类/划重点/动效/同帧音效)
  job_draft.py       export_job_draft (导演 job → 草稿, J1 主入口)
  ppt_draft.py       export_ppt_draft (PPT 整页 → 草稿)
  ppt_layout.py      元素级布局分析 (分带/分列/宫格检测)
  ppt_timing.py      元素级时序编排 (element/cell/block/page)
  ppt_element.py     export_element_draft (元素级草稿 + 免责/角标/字幕轨)
  watermark.py       静姐读书 logo 台标 (右下角间歇, 白底转透明预处理)
"""
from __future__ import annotations

from app.services.jy_draft_service.auto_choreo import _auto_choreograph
from app.services.jy_draft_service.common import (
    _ANIM_PAIRS,
    _HF_TEXT_FAMILIES,
    _TITLE_IN_SOUNDS,
    _US,
    _load_manifest,
    _trange_sec,
)
from app.services.jy_draft_service.job_draft import export_job_draft
from app.services.jy_draft_service.ppt_draft import export_ppt_draft
from app.services.jy_draft_service.ppt_element import export_element_draft
from app.services.jy_draft_service.ppt_timing import (
    compute_block_timing,
    compute_cell_timing,
    compute_element_timing,
    compute_page_timing,
    split_narration,
)
from app.services.jy_draft_service.sfx import _probe_duration, attach_sound, sound_path
from app.services.jy_draft_service.subtitle_style import _CREDIT_BG, _StyledTextSegment
from app.services.jy_draft_service.subtitle_text import (
    find_highlight_ranges,
    split_subtitle,
    wash_subtitle_text,
)

__all__ = [
    "export_job_draft",
    "export_ppt_draft",
    "export_element_draft",
    "compute_page_timing",
    "compute_element_timing",
    "compute_cell_timing",
    "compute_block_timing",
    "wash_subtitle_text",
    "split_subtitle",
    "split_narration",
    "find_highlight_ranges",
    "sound_path",
    "attach_sound",
]
