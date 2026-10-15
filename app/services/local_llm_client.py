"""本地视觉 LLM 客户端 — OpenAI 兼容 API (llama-server).

向本地 llama-server 发送多模态请求, 支持 base64 图片 + 文本 prompt.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import requests

from ..config import LocalLLMConfig

logger = logging.getLogger(__name__)


class LocalLLMError(Exception):
    """本地 LLM 调用失败."""


class LocalLLMClient:
    """OpenAI 兼容视觉 LLM 客户端, 对接 llama-server."""

    def __init__(self, cfg: LocalLLMConfig):
        self.cfg = cfg
        self._url = cfg.base_url.rstrip("/") + "/chat/completions"

    def _encode_image(self, path: Path) -> str:
        """将 PNG 编码为 base64 data URI."""
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"

    def health_check(self) -> bool:
        """检查 llama-server 是否可达."""
        health_url = self.cfg.base_url.rstrip("/") + "/health"
        try:
            r = requests.get(health_url, timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def chat_with_images(
        self,
        image_paths: list[Path],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """发送多图视觉请求, 返回模型原始文本.

        Args:
            image_paths: 本地 PNG 文件路径列表（最多 4 张）.
            system_prompt: system role 提示词.
            user_prompt: user role 提示词文本.

        Returns:
            模型原始文本响应.

        Raises:
            LocalLLMError: 所有重试耗尽后仍失败.
        """
        # 构建 vision content 数组
        content: list[dict[str, Any]] = []
        for p in image_paths:
            content.append({
                "type": "image_url",
                "image_url": {"url": self._encode_image(p)},
            })
        content.append({"type": "text", "text": user_prompt})

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        last_error = ""
        for attempt in range(self.cfg.max_retries + 1):
            try:
                r = requests.post(
                    self._url,
                    headers=headers,
                    json=payload,
                    timeout=self.cfg.timeout_sec,
                )
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                return text
            except requests.Timeout:
                last_error = f"请求超时 ({self.cfg.timeout_sec}s)"
                logger.warning("LLM attempt %d/%d: %s", attempt + 1, self.cfg.max_retries + 1, last_error)
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("LLM attempt %d/%d: %s", attempt + 1, self.cfg.max_retries + 1, last_error)
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = f"响应解析失败: {exc}"
                logger.warning("LLM attempt %d/%d: %s", attempt + 1, self.cfg.max_retries + 1, last_error)

        raise LocalLLMError(f"LLM 调用失败 (已重试 {self.cfg.max_retries} 次): {last_error}")
