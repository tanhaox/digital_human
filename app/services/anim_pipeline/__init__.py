# -*- coding: utf-8 -*-
"""动画管线 v1 — 0914 系统化平移入 app/services (原 sandbox/anim_pipeline).

链路: LLM 规划(三件套) → shots.json → K2 批生图(2ST) → 人检关口 → H3 批生视频(节拍式)
     → 对齐归真 + 剪映草稿 (app/services/anim_draft.py) .
本包保持零 app.* import (编排/DB/SSE 由 app/services/anim_service.py 外壳负责):
配置自带(本目录 config.json 可覆盖), LLM key 走 .env / 环境变量, ComfyUI 走本机 8188.

用法: python -m app.services.anim_pipeline.run <plan|k2|qc|approve|h3|tts|status> --book <id/书名> --ep <N>
"""
