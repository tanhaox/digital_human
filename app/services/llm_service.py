"""LLM service for article rewriting."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "你是一位资深财经短视频编剧。"
    "请将用户提供的财经新闻改写成适合数字人口播的短视频脚本，"
    "要求语言口语化、有节奏感、适合中文 TTS 朗读。"
)


def _render_common_blocks(base: str) -> str:
    """公共提示词块渲染 (2026-09-07): {{common:xxx}} → config/common/xxx.txt。

    tech/geo 模板的人设/互动时序/TTS约束/真实性红线/输出格式五块已抽公共库,
    消除双轨人工同步漂移 (通用技巧只改一处, 两线同时生效)。只渲染一层 —
    公共块内禁止再嵌 {{common:}}; 引用不存在的块原样保留 (typo 可见于产物)。
    """
    import re

    from ..config import PROJECT_ROOT as _root

    def _sub(m: "re.Match[str]") -> str:
        p = _root / "config" / "common" / f"{m.group(1)}.txt"
        try:
            return p.read_text(encoding="utf-8").strip()
        except OSError:
            return m.group(0)

    return re.sub(r"\{\{common:([a-z_]+)\}\}", _sub, base)


def _load_prompt_template(prompt_template: str | None) -> str:
    """Load prompt template from config/ if a name is provided, otherwise return as-is."""
    if not prompt_template:
        return DEFAULT_SYSTEM_PROMPT
    # If it contains instructions already, use directly.
    if len(prompt_template) > 80 and ("你" in prompt_template or "请" in prompt_template):
        return prompt_template
    from ..config import PROJECT_ROOT

    candidate = PROJECT_ROOT / "config" / f"{prompt_template}.txt"
    if candidate.exists():
        base = candidate.read_text(encoding="utf-8").strip()
        # 2026-09-07: {{common:xxx}} 公共块渲染 (模板瘦身, 见 _render_common_blocks)
        base = _render_common_blocks(base)
        # 2026-08-22: 全系统统一限流词注入 (config/compliance_common.json — 拆书/新闻线/未来系统共用)
        try:
            from .compliance import build_redline_prompt
            base += "\n\n" + build_redline_prompt()
        except Exception:
            pass
        return base
    return prompt_template


class LLMService:
    """LLM 客户端: 包装 OpenAI 兼容 chat/completions.

    2026-09-09 用户令: kimi 主力 / deepseek 系统兜底 (fallback= 参数)。
    新闻线洗稿/拆书编排/导演工序单等原 deepseek 入口统一走本类,
    单次请求内按 主力→兜底 自动切换。

    connection-level 重试只针对 "请求还没发出去/连接建立失败" 的错误
    (requests.ConnectionError / 超时), 不重试 HTTP 4xx/5xx — 后者
    在 stream=True 时可能已吐出部分内容, 重发会重复。
    """
    # 远端连接抖动/长连接被重置时重试 (10054/10060/11001 等)。
    # 0915 修: 只重试"请求没发出去"级 (ConnectionError/ConnectTimeout);
    # ReadTimeout (服务端收了请求不回话, deepseek 过载实锤) 不重试 — 直接换下一厂商,
    # 否则 300s×3 次 = 单调用卡 15 分钟 (A2 重规划卡死实锤)。
    _RETRYABLE = (requests.ConnectionError, requests.ConnectTimeout)
    _MAX_RETRIES = 2
    _BACKOFF_SEC = 2.0
    # 触发切换兜底的 HTTP 码: 鉴权/限流/服务端错。
    # 400 = 自家 payload 问题, 换端同样炸, 不浪费一轮兜底。
    _FALLBACK_STATUS = {401, 403, 404, 408, 429, 500, 502, 503, 504}
    # 0915 熔断: 厂商失败后 5 分钟内跳过 (deepseek 队列过载时单调用可拖 900s)
    _BREAKER_SEC = 300.0

    def __init__(self, cfg, fallback=None):
        """fallback 可传单个 provider 或列表 (0915: kimi → deepseek → qwen 三级链)."""
        self.cfg = cfg
        if fallback is None:
            self.fallbacks: list = []
        elif isinstance(fallback, (list, tuple)):
            self.fallbacks = list(fallback)
        else:
            self.fallbacks = [fallback]
        self._provider_down: dict[int, float] = {}

    @staticmethod
    def _sanitize_payload(cfg, payload: dict) -> None:
        """厂商 payload 适配: kimi coding 系仅收 temperature=1 (2026-09-09 实测,
        传 0.2~0.7 全 400), 发送前统一摘除走远端默认; enable_thinking 为
        DeepSeek 私有参数但 kimi 容忍 (实测 200), 保留不动。
        0915: deepseek V4 也关思考 — flash 默认思考开 (官方 thinking_mode 文档),
        结构化单发调用 (导演规划等) 思考纯属耗时烧钱; 语法同 kimi
        {"thinking":{"type":"disabled"}} (OpenAI 格式 extra_body 字段, 直传 JSON 同效)。
        qwen (百炼 compatible-mode): enable_thinking=false — 思考版"在的"烧 28 token
        vs 关后 2 token (实测), 备份配额有限必须省。"""
        if "kimi.com" in (cfg.base_url or "") or "deepseek.com" in (cfg.base_url or ""):
            payload.pop("temperature", None)
            # 0910: kimi 思考开关原生翻译 (boost 通道同款修复) — 大 prompt 思考烧光
            # 输出预算 → content 空 (bs1 真稿规划 0 字符实锤); chat 路径一律关思考
            payload["thinking"] = {"type": "disabled"}
        elif "aliyuncs.com" in (cfg.base_url or ""):
            payload["enable_thinking"] = False

    def _resolve_model_for(self, cfg, model: str | None) -> str:
        """Resolve 'flash'/'pro' aliases per provider (各家模型名不同), 具体名透传."""
        if model is None:
            model = cfg.default_model
        aliases = {"flash": cfg.model_flash, "pro": cfg.model_pro}
        return aliases.get(model, model)

    def _post_with_retry(self, url, *, headers, json, stream, timeout=120):
        """POST with connection-level retry. 见类 docstring 的重试范围约定."""
        last_exc = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return requests.post(url, headers=headers, json=json, stream=stream, timeout=timeout)
            except self._RETRYABLE as exc:
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    delay = self._BACKOFF_SEC * (2 ** attempt)
                    logger.warning(
                        "[llm] connection error (attempt %d/%d): %s — retry in %.1fs",
                        attempt + 1, self._MAX_RETRIES + 1, exc, delay,
                    )
                    time.sleep(delay)
        raise last_exc

    def _post_providers(self, payload: dict, *, stream: bool, timeout: int = 120):
        """按 主力→兜底 链发请求, 返回 (response, 实际服务的 provider cfg).

        每厂商: 解析模型别名 → payload 适配 → connection-level 重试;
        连接失败/超时/HTTP 兜底码/200+错误体 → 记日志换下一厂商。
        response 成功返回后才读 body, 故 stream 场景兜底不会重复内容。

        0915 熔断器: 厂商失败 (含 900s 队列错误体) 后 _BREAKER_SEC 内直接跳过 —
        deepseek 过载时客户端超时拦不住服务端保活连接 (实测拖满 900s),
        不熔断则每次调用都要白等一轮才轮到下一家。
        非流式额外校验 body: 200 + {"error":...} 错误体 (deepseek 队列超时形态)
        也算厂商级失败, 标记熔断并换下一家。
        1009 P0: 返回值附带 served cfg (usage 日志要知道谁实际服务了).
        """
        providers = [self.cfg] + self.fallbacks
        providers = [c for c in providers if c is not None and c.api_key]
        if not providers:
            raise RuntimeError(
                "LLM API key is not configured. "
                "Set KIMI_API_KEY / DEEPSEEK_API_KEY environment variables before starting the server."
            )
        now = time.time()
        last_exc: Exception | None = None
        for idx, cfg in enumerate(providers):
            if now - self._provider_down.get(idx, 0) < self._BREAKER_SEC:
                logger.warning("[llm] provider#%d %s 熔断中 (最近失败 %.0fs 内), 跳过",
                               idx, cfg.base_url, self._BREAKER_SEC)
                continue
            body = dict(payload)
            body["model"] = self._resolve_model_for(cfg, payload.get("model"))
            self._sanitize_payload(cfg, body)
            headers = {
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{cfg.base_url.rstrip('/')}/chat/completions"
            try:
                resp = self._post_with_retry(url, headers=headers, json=body,
                                             stream=stream, timeout=timeout)
            except (*self._RETRYABLE, requests.ReadTimeout) as exc:
                last_exc = exc
                self._provider_down[idx] = time.time()
                logger.warning("[llm] provider#%d %s 连接失败/读超时: %s → 换下一厂商",
                               idx, cfg.base_url, exc)
                continue
            if resp.status_code in self._FALLBACK_STATUS:
                last_exc = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                self._provider_down[idx] = time.time()
                logger.warning("[llm] provider#%d %s HTTP %s → 换下一厂商",
                               idx, cfg.base_url, resp.status_code)
                continue
            if not stream:  # 200 + 错误体也当厂商失败 (deepseek 900s 队列超时形态)
                try:
                    data = resp.json()
                except ValueError:
                    self._provider_down[idx] = time.time()
                    last_exc = RuntimeError("非 JSON 响应体")
                    logger.warning("[llm] provider#%d %s 非 JSON 体 → 换下一厂商",
                                   idx, cfg.base_url)
                    continue
                if "choices" not in data:
                    err = (data.get("error") or {}).get("message") or str(data)[:200]
                    self._provider_down[idx] = time.time()
                    last_exc = RuntimeError(f"错误体: {err}")
                    logger.warning("[llm] provider#%d %s 200+错误体 → 换下一厂商: %s",
                                   idx, cfg.base_url, err[:120])
                    continue
            self._provider_down.pop(idx, None)
            if idx > 0:
                logger.warning("[llm] 请求兜底到 provider#%d %s", idx, cfg.base_url)
            return resp, cfg
        raise last_exc or RuntimeError("no LLM provider available")

    @staticmethod
    def _log_usage(data: dict, tag: str | None, served_cfg) -> None:
        """1009 P0 (设计 §二): usage 落日志 — 实弹 token 分布, 优化排序的地面真值.

        cached 字段三家形状不同, 防御式全试 (OpenAI prompt_tokens_details.cached_tokens
        / deepseek prompt_cache_hit_tokens / 兼容网关裸 cached_tokens), 取不到记 0。
        日志失败不影响业务 (usage 缺失/形状怪 = 记不了就不记)。
        """
        try:
            u = data.get("usage") or {}
            ptok = int(u.get("prompt_tokens") or 0)
            ctok = int(u.get("completion_tokens") or 0)
            cached = 0
            det = u.get("prompt_tokens_details") or {}
            if isinstance(det, dict) and det.get("cached_tokens") is not None:
                cached = int(det.get("cached_tokens") or 0)
            elif u.get("prompt_cache_hit_tokens") is not None:
                cached = int(u.get("prompt_cache_hit_tokens") or 0)
            elif u.get("cached_tokens") is not None:
                cached = int(u.get("cached_tokens") or 0)
            host = str(getattr(served_cfg, "base_url", "") or "")
            provider = ("kimi" if "kimi.com" in host else
                        "qwen" if "aliyuncs.com" in host else
                        "deepseek" if "deepseek.com" in host else
                        host.split("//")[-1].split("/")[0][:20] or "?")
            logger.info("[llm:usage] tag=%s provider=%s model=%s ptok=%d ctok=%d cached=%d",
                        tag or "无标", provider, str(data.get("model") or "-")[:40],
                        ptok, ctok, cached)
        except Exception:  # noqa: BLE001
            pass

    def chat(self, system: str, user: str, model: str | None = None,
             temperature: float = 0.7, timeout: int = 300,
             max_tokens: int | None = None,
             response_format: dict[str, Any] | None = None,
             tag: str | None = None) -> str:
        """通用单轮对话 (非流式) — 拆书编排等结构化调用入口.

        timeout 默认 300s: pro 长稿生成常超 120s。
        max_tokens/response_format (2026-08-25): 可选注入 — 结构化调用方(director 工序单)
        此前不设上限, 长稿输出截断 = JSON 解析失败 = job 报废。
        tag (1009 P0): 调用点标签, usage 日志归因用 (无标=记"无标"不炸).
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        response, served = self._post_providers(payload, stream=False, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        # 0915 实锤: deepseek 队列过载时回 HTTP 200 + {"error":{...}} 错误体
        # (900s 排队超时原文) — 没有 choices, 旧代码 KeyError 崩得不明不白
        if "choices" not in data:
            err = (data.get("error") or {}).get("message") or str(data)[:200]
            raise RuntimeError(f"LLM 服务商错误体: {err[:300]}")
        self._log_usage(data, tag, served)
        return data["choices"][0]["message"]["content"]

    def edit_patch(self, text: str, system: str, user_extra: str = "",
                   model: str | None = None, max_tokens: int | None = None,
                   tag: str | None = None) -> str | None:
        """通用行级 patch 协议 (1009 P5, 设计 §七; correct_article 协议泛化+删行).

        text 编号后连同 system/user_extra 发给 LLM, 只要求输出改动行:
          {"changes": [[行号, "新行文本"], ...]}
        删行语义: 新行为空串 = 删该行。未涉及的行由代码逐字保留 — 段落标签/
        coverage JSON/⟦⟧原话零漂移是构造保证, 不是 prompt 自律。
        返回拼装后的全文; 解析失败/越界/零合法改动 → None (调用方走全稿兜底)。
        """
        lines = text.splitlines()
        if not lines:
            return None
        numbered = chr(10).join(f"[{i}] {ln}" for i, ln in enumerate(lines, start=1))
        sys_p = (system.rstrip()
                 + chr(10) + chr(10) + "【patch 协议】你收到带行号的稿子, 只输出需要修改的行: "
                 + '严格 JSON {"changes": [[行号, "新行文本"], ...]}。'
                 + "行号为空串 = 删除该行; 未涉及的行禁止出现在 changes 里 (代码原样保留); "
                 "新行保持原语言风格与节奏, 禁输出 JSON 以外任何文字。")
        try:
            raw = self.chat(sys_p, numbered + user_extra, model=model,
                            temperature=0.3, max_tokens=max_tokens,
                            response_format={"type": "json_object"}, tag=tag)
        except Exception:
            raw = self.chat(sys_p, numbered + user_extra, model=model,
                            temperature=0.3, max_tokens=max_tokens, tag=tag)
        try:
            s = str(raw or "").strip()
            if s.startswith("```"):
                s = s.split(chr(10), 1)[-1] if chr(10) in s else s
                for fence in ("```json", "```"):
                    if s.endswith(fence):
                        s = s[: -len(fence)]
            data = json.loads(s.strip())
            changes = data.get("changes") if isinstance(data, dict) else None
            if not isinstance(changes, list):
                return None
            patch: dict[int, str] = {}
            for ch in changes:
                try:
                    n, new_ln = int(ch[0]), str(ch[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if 1 <= n <= len(lines):
                    patch[n] = new_ln
            if not patch:
                return None
            out: list[str] = []
            for i, ln in enumerate(lines, start=1):
                if i not in patch:
                    out.append(ln)
                elif patch[i].strip():
                    out.append(patch[i])
            return chr(10).join(out)
        except Exception:  # noqa: BLE001 — 协议失败走全稿兜底
            return None


    def chat_turns(self, messages: list[dict[str, Any]], model: str | None = None,
                   temperature: float = 0.7, timeout: int = 300,
                   max_tokens: int | None = None,
                   response_format: dict[str, Any] | None = None,
                   tag: str | None = None) -> str:
        """多轮对话 (0917 用户令: 调优优先 — 哪步适合多轮哪步用, 不一刀切).

        messages 携带 assistant 历史 (模型看着自己的旧产出修正, 优于把旧稿贴进新 user)。
        用于"先答后改"式渐进 (动画线: 立意确认轮/全片巡检轮); 单轮结构化调用继续走
        chat() 不动。与 chat() 同一厂商兜底链与错误体防御。tag 同 chat (1009 P0).
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        response, served = self._post_providers(payload, stream=False, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if "choices" not in data:
            err = (data.get("error") or {}).get("message") or str(data)[:200]
            raise RuntimeError(f"LLM 服务商错误体: {err[:300]}")
        self._log_usage(data, tag, served)
        return data["choices"][0]["message"]["content"]

    def rewrite_article(
        self,
        raw_text: str,
        prompt_template: str | None = None,
        model: str | None = None,
        stream: bool = True,
        chunk_callback: Callable[[str], None] | None = None,
        perspective: str | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Rewrite raw article into broadcast script.

        Args:
            raw_text: The original article text.
            prompt_template: Optional override prompt template name or raw prompt.
            model: 'flash', 'pro', or a concrete model name.
            stream: Whether to stream response.
            chunk_callback: Called with each content chunk when streaming.
            perspective: Optional user perspective injected before rewrite.
            max_tokens/response_format (2026-08-25): 可选注入, 供结构化调用方(director 工序单)防截断。
        """
        system = _load_prompt_template(prompt_template)

        # 构建 user message: 有观点时前置补充观点
        if perspective and perspective.strip():
            user_content = f"【原作者补充观点】\n{perspective.strip()}\n\n【原文】\n{raw_text}"
        else:
            user_content = raw_text

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": 0.7,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        response, _ = self._post_providers(payload, stream=stream)
        response.raise_for_status()

        if not stream:
            data = response.json()
            return data["choices"][0]["message"]["content"]

        full_text = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: "):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                full_text += delta
                if chunk_callback:
                    chunk_callback(delta)
        return full_text

    def correct_article(
        self,
        rewritten_text: str,
        perspective: str,
        prompt_template: str | None = None,
        model: str | None = None,
        stream: bool = True,
        chunk_callback: Callable[[str], None] | None = None,
    ) -> str:
        """根据用户修正观点调整已洗稿脚本 — patch 协议 (2026-08-25 v2).

        与 rewrite 不同: 输入是洗稿结果 + 修正观点，目标是微调而非重写。
        v2: 行编号 + LLM 只输出改动行 {"changes":[[行号,"新行"]]} (输出从全稿 ~2400 字
        降到几百字, 零复写零漂移), 代码替换拼装; 未涉及的行由代码保证原样 —
        旧版"必须输出完整稿一行不少"的复写协议废除非必要的大改全部重写场景。
        失败回退旧全稿协议一次 (保功能可用)。
        """
        try:
            result = self._correct_patch(rewritten_text, perspective, prompt_template, model,
                                         chunk_callback)
            if result is not None:
                return result
        except Exception:
            # patch 失败 → 旧协议兜底
            pass
        return self._correct_fulltext(rewritten_text, perspective, prompt_template, model,
                                      stream, chunk_callback)

    def _correct_patch(
        self, rewritten_text: str, perspective: str, prompt_template: str | None,
        model: str | None, chunk_callback: Callable[[str], None] | None,
    ) -> str | None:
        lines = [ln for ln in rewritten_text.splitlines()]
        if not lines:
            return None
        numbered = "\n".join(f"[{i}] {ln}" for i, ln in enumerate(lines, start=1))
        system = _load_prompt_template(prompt_template)
        system += (
            "\n\n【修正任务 · patch 协议】用户对洗稿稿提出修正意见。你收到带行号的稿子, "
            "只输出需要修改的行: 严格 JSON {\"changes\": [[行号, \"新行文本\"], ...]}。\n"
            "1. 只改与修正观点直接相关的行, 其余行禁止出现在 changes 里 (代码原样保留)。\n"
            "2. 新行保持原有语言风格与节奏, 行内可含多个句子但不合并/拆分相邻行。\n"
            "3. 若修正意见与稿子无关或无需改动, 输出 {\"changes\": []}。\n"
            "4. 禁止输出 JSON 以外的任何文字。"
        )
        user_content = f"【修正观点】\n{perspective.strip()}\n\n【带行号稿】\n{numbered}"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 3000,
            "response_format": {"type": "json_object"},
        }
        response, _ = self._post_providers(payload, stream=False)
        response.raise_for_status()
        data = json.loads(response.json()["choices"][0]["message"]["content"])
        changes = data.get("changes") if isinstance(data, dict) else None
        if not isinstance(changes, list):
            return None
        patch: dict[int, str] = {}
        for ch in changes:
            try:
                n, new_ln = int(ch[0]), str(ch[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 1 <= n <= len(lines) and new_ln.strip():
                patch[n] = new_ln
        if not patch:
            return rewritten_text  # 无需改动 — 原稿直返
        out_lines = [patch.get(i, ln) for i, ln in enumerate(lines, start=1)]
        result = "\n".join(out_lines)
        # 模拟打字机: 按行回调 (改动行加标记节奏), 前端 correct_chunk 事件格式不变
        if chunk_callback:
            for i, ln in enumerate(out_lines, start=1):
                chunk_callback(("✎ " if i in patch else "") + ln + "\n")
        return result

    def _correct_fulltext(
        self, rewritten_text: str, perspective: str, prompt_template: str | None,
        model: str | None, stream: bool, chunk_callback: Callable[[str], None] | None,
    ) -> str:
        """旧全稿协议 (patch 失败兜底, 行为同 2026-08-11 版)."""
        system = _load_prompt_template(prompt_template)
        system += (
            "\n\n【重要】用户对洗稿结果提出了修正意见。"
            "你的任务是**在保留完整稿子的前提下，只修改与修正观点相关的句子**。"
            "\n\n【硬性约束】"
            "\n1. 必须输出【修正后的完整稿】，一行都不能少，禁止删段、禁止压缩、禁止精简。"
            "\n2. 只改与修正观点直接相关的句子（措辞/语气/事实），其余句子**原样保留**，一字不改。"
            "\n3. 全文句数、段落数必须与输入稿**一致**，不得合并或拆分句子。"
            "\n4. 保持原有的语言风格、节奏和结构。"
            "\n5. 若修正观点与稿子无关，直接原样输出输入稿，不做任何改动。"
        )

        user_content = f"【修正观点】\n{perspective.strip()}\n\n【洗稿结果】\n{rewritten_text}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": 0.6,  # 修正用稍低温度，保持连贯
        }

        response, _ = self._post_providers(payload, stream=stream)
        response.raise_for_status()

        if not stream:
            data = response.json()
            return data["choices"][0]["message"]["content"]

        full_text = ""
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: "):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                full_text += delta
                if chunk_callback:
                    chunk_callback(delta)
        return full_text


def get_llm_service() -> LLMService:
    """全系统统一 LLM 入口。

    0915 三级链 (用户令): kimi 主力 → qwen (token-plan 套餐, 不用白不用,
    用完为止 → 套餐耗尽自然落到 deepseek) → deepseek 末位兜底。
    kimi key 未配置时 qwen 顶主力, 再退 deepseek 单 provider。
    """
    from ..config import get_config

    cfg = get_config()
    fallbacks = []
    qw = getattr(cfg, "qwen", None)
    if qw is not None and qw.api_key:
        fallbacks.append(qw)
    if cfg.deepseek.api_key:
        fallbacks.append(cfg.deepseek)
    if cfg.kimi.api_key:
        return LLMService(cfg.kimi, fallback=fallbacks)
    if fallbacks:
        return LLMService(fallbacks[0], fallback=fallbacks[1:])
    return LLMService(cfg.deepseek)


def _parse_json(text: str) -> Any:
    """LLM 输出取 JSON: 去 markdown 围栏后解析.

    B08 (2026-10-16) 自 book_service/creation_common 迁入 — anim_pipeline 与
    book_service 双方共用的中立底层 (creation_common 侧 re-export 保持旧入口)。
    """
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t)
    t = re.sub(r"```$", "", t).strip()
    m = re.search(r"[\[{]", t)
    if m and not t.startswith(("{", "[")):
        t = t[m.start():]
    return json.loads(t)
