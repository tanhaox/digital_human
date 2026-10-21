# -*- coding: utf-8 -*-
"""GLM 看图 CLI (1021 配, GLM Coding Plan 专用 key).

用法:
    python scripts/glm_vision.py <图片路径> [图片路径...] [-p "问题"] [--model glm-4.6v]

- key: .env 的 GLM_VISION_API_KEY (GLM Coding Plan, 与 ZHIPU_API_KEY 搜索/flash 池分开)
- 模型: 默认 glm-4.6v (1021 实测眼力正常), 429/模型错自动降级 glm-4.5v
- glm-4v-flash 不在套餐内 (1021 实测 1314 企业套餐失效), 别选它
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
FALLBACK_MODEL = "glm-4.5v"
TIMEOUT = (10, 180)


def load_key() -> str:
    env_key = os.environ.get("GLM_VISION_API_KEY", "").strip()
    if env_key:
        return env_key
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"GLM_VISION_API_KEY=(.*)", line.strip())
        if m and m.group(1).strip():
            return m.group(1).strip()
    raise SystemExit("[abort] GLM_VISION_API_KEY 未配置 — 请在 digital_human/.env 填写 (GLM Coding Plan 看图专用 key)")


def to_data_url(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"[abort] 图片不存在: {path}")
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def chat(key: str, model: str, content: list[dict], max_tokens: int) -> tuple[str, str]:
    """返回 (回答文本, 实际模型)."""
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "temperature": 0.2, "max_tokens": max_tokens}
    resp = requests.post(BASE_URL, json=body,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         timeout=TIMEOUT)
    if resp.status_code == 401:
        raise SystemExit("[abort] GLM_VISION_API_KEY 无效 (401)")
    if resp.status_code != 200:
        return "", f"__HTTP_{resp.status_code}__" + resp.text[:200]  # 交给调用方降级/报错
    try:
        return resp.json()["choices"][0]["message"]["content"].strip(), model
    except (KeyError, IndexError, ValueError) as exc:
        raise SystemExit(f"[abort] 返回结构异常: {resp.text[:200]}") from exc


def main() -> None:
    ap = argparse.ArgumentParser(description="GLM 看图 (glm-4.6v, Coding Plan 专用 key)")
    ap.add_argument("images", nargs="+", help="图片路径 (可多张对比)")
    ap.add_argument("-p", "--prompt", default="详细描述这张图的内容。", help="问题/指令")
    ap.add_argument("--model", default="glm-4.6v", help="默认 glm-4.6v, 降级 glm-4.5v")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--all", action="store_true", help="每张图分别问 (默认多图合并一次问)")
    args = ap.parse_args()

    key = load_key()
    data_urls = [to_data_url(Path(p)) for p in args.images]

    def ask(urls: list[str], label: str) -> None:
        content = [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        content.append({"type": "text", "text": args.prompt})
        answer, used = chat(key, args.model, content, args.max_tokens)
        if not answer:  # 降级重试
            answer, used = chat(key, FALLBACK_MODEL, content, args.max_tokens)
            if not answer:
                raise SystemExit(f"[abort] {label}: {args.model} 与 {FALLBACK_MODEL} 均失败: " + used[:200])
        prefix = f"[{used}] {label}\n" if len(args.images) > 1 or args.all else f"[{used}] "
        print(prefix + answer)

    if args.all:
        for p, u in zip(args.images, data_urls):
            ask([u], p)
    else:
        ask(data_urls, " + ".join(args.images))


if __name__ == "__main__":
    main()
