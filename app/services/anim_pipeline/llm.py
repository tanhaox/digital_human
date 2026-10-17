# -*- coding: utf-8 -*-
"""独立 LLM 调用 — kimi 主力 + deepseek 兜底 (OpenAI 兼容, httpx 直连).

kimi coding 硬约束 (0909 实测): base 必须带 /v1; temperature 只收 1 (传其他值 400);
response_format json_object 可用. 接入系统时本文件整体换成 get_llm_service().
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from .config import LLMConfig

logger = logging.getLogger(__name__)

_TIMEOUT = 300.0
# 400 不换厂商 (参数问题换厂商也修不了), 其余连接失败/401/403/404/408/429/5xx 换
_SWITCH_STATUS = {401, 403, 404, 408, 429, 500, 502, 503, 504}


def _chat_once(base_url: str, api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "temperature": 1,  # kimi 硬约束
        "max_tokens": max_tokens,  # 不显式给会被默认值截断大 JSON (0911 导演步实锤)
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    if "aliyuncs.com" in base_url:
        payload["enable_thinking"] = False  # qwen 思考版默认在, 白烧 token (主链同款)
    with httpx.Client(timeout=_TIMEOUT) as cli:
        r = cli.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {r.status_code} ({base_url}): {r.text[:300]}")
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError(f"LLM 空 content ({base_url})")
    return content


def _qwen_cfg() -> tuple[str, str, str] | None:
    """qwen 中段兜底 (0916 kimi 限额日): token-plan 套餐 pro 模型 — deepseek
    顶格 8K tokens 装不下导演 24K 字符 JSON (19461 字符截断死循环实锤)."""
    try:
        from app.config import get_config as _gc
        qw = getattr(_gc(), "qwen", None)
        if qw is not None and qw.api_key:
            return qw.base_url, qw.api_key, qw.model_pro
    except Exception:  # noqa: BLE001
        pass
    return None


def chat_json(llm: LLMConfig, system: str, user: str) -> dict:
    """调用 LLM 返回 JSON dict. 三级链 kimi → qwen(套餐) → deepseek; JSON 截断重试一次.

    0916: 中段插 qwen — kimi 限额日直接落 deepseek, 其 8K tokens 顶格装不下
    导演 24K 字符 JSON (19461 字符截断死循环实锤); qwen token-plan 套餐 pro
    模型给 24K tokens, 失败再退 deepseek."""
    import json
    import os

    key = os.environ.get(llm.api_key_env, "")
    fb_key = os.environ.get(llm.fallback_api_key_env, "")
    primary_max = getattr(llm, "max_tokens", 16000)
    fallback_max = getattr(llm, "fallback_max_tokens", 8000)
    # 1017 用户令: qwen 云端到期退役 — 链收为 kimi → deepseek → 本地 qwen36
    last_err: Exception | None = None
    skip_primary = False  # 0916: primary 自己截断 (200 但 JSON 断尾) → 下轮换人
    for attempt in range(2):
        raw = None
        try:
            if not key:
                raise RuntimeError(f"缺少 {llm.api_key_env}")
            if skip_primary:
                raise RuntimeError("上轮 primary 输出截断, 本轮直取兜底链")
            raw = _chat_once(llm.base_url, key, llm.model, system, user, primary_max)
            provider = llm.base_url
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.warning("[llm] kimi 失败切兜底: %s", exc)
            if fb_key:
                try:
                    raw = _chat_once(llm.fallback_base_url, fb_key, llm.fallback_model,
                                     system, user, fallback_max)
                    provider = llm.fallback_base_url
                except (httpx.HTTPError, RuntimeError) as exc2:
                    logger.warning("[llm] deepseek 兜底失败: %s", exc2)
                    raw = None
            if raw is None:
                # 1017 用户令: 本地 Qwen3.6-35B 保底 (12348; 拉起过 gpu_mutex,
                # ComfyUI 占卡拒上卡 → fail-open 由调用方退路)
                try:
                    raw = _local_qwen_chat(system, user, primary_max)
                    provider = "local-qwen36"
                except (httpx.HTTPError, RuntimeError) as exc3:
                    last_err = exc3 or exc2
                    continue
        logger.info("[llm] %s 返回 %d 字符 (attempt %d)", provider, len(raw), attempt + 1)
        try:
            return _parse_json(raw)
        except json.JSONDecodeError as exc:
            last_err = exc
            if provider == llm.base_url:
                skip_primary = True  # primary 截断非偶发 (限额档输出上限), 换人
            logger.warning("[llm] JSON 截断/畸形 (char %s), 重试...", getattr(exc, "pos", "?"))
    raise RuntimeError(f"LLM JSON 解析失败两连: {last_err}")


# ── 本地 Qwen3.6-35B 保底 (1017 用户令: 云端 qwen 到期退役) ──────────────────
_LOCAL_QWEN_URL = "http://127.0.0.1:12348"
_LOCAL_QWEN_MODEL = "qwen36-aggressive"


def _local_qwen_alive() -> bool:
    try:
        with httpx.Client(timeout=3) as cli:
            return cli.get(f"{_LOCAL_QWEN_URL}/health").status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _ensure_local_qwen() -> bool:
    """本地 35B 在线检查; 离线且显卡空闲则拉起 (ComfyUI 占卡拒上卡)。"""
    if _local_qwen_alive():
        return True
    from app.services.gpu_service_manager._gpu_mutex import comfyui_holds_gpu
    if comfyui_holds_gpu():
        logger.warning("[llm] 本地 qwen 保底跳过: ComfyUI 占卡 (独享死命令)")
        return False
    import subprocess
    import time as _t
    cmd = [
        r"E:\Llama-cpp-13\llama-server.exe",
        "-m", r"E:\Llama-cpp\models\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf",
        "--alias", "qwen36-aggressive",
        "--temp", "0.2", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.00",
        "--port", "12348", "--host", "127.0.0.1",
        "-ngl", "999", "-c", "32768", "-b", "4096", "-ub", "4096",
        "-fa", "on", "-ctk", "f16", "-ctv", "f16", "--device", "CUDA0",
        "--parallel", "1", "--fit", "on", "--fit-target", "4096",
        "--cache-ram", "6144", "-lv", "1",
    ]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(cmd, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info("[llm] 本地 qwen36 拉起中 (35B Q5 加载 ~2-5 分钟)…")
    for _ in range(96):  # 最多等 8 分钟
        _t.sleep(5)
        if _local_qwen_alive():
            logger.info("[llm] 本地 qwen36 就绪 ✓")
            return True
    return False


def _local_qwen_chat(system: str, user: str, max_tokens: int) -> str:
    """本地 35B 兜底 (OpenAI 兼容 /v1; 无 response_format — 靠围栏解析)。"""
    if not _ensure_local_qwen():
        raise RuntimeError("本地 qwen 不可用 (离线且显卡被占)")
    with httpx.Client(timeout=600) as cli:
        r = cli.post(
            f"{_LOCAL_QWEN_URL}/v1/chat/completions",
            headers={"Authorization": "Bearer local"},
            json={"model": _LOCAL_QWEN_MODEL, "temperature": 0.2,
                  "max_tokens": max_tokens,
                  "messages": [
                      {"role": "system", "content": system + "\n输出严格 JSON, 不要多余文字。"},
                      {"role": "user", "content": user}]})
    if r.status_code >= 400:
        raise RuntimeError(f"本地 qwen HTTP {r.status_code}: {r.text[:200]}")
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("本地 qwen 空 content")
    return content


def _parse_json(raw: str) -> dict:
    """去 markdown 围栏后解析 (对齐 creation_common._parse_json 做法)."""
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    return json.loads(text)
