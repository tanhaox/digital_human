# -*- coding: utf-8 -*-
"""图片反推提示词服务 (2026-09-06) — 反推页引擎.

图 → 目标类型(人物/道具/场景) 的结构化锚点字段, 供生成页模板直接带入。
模型: GLM-4V-Flash (免费), 走系统 zhipu 配置 (config.app.yaml zhipu 节 + .env ZHIPU_API_KEY)。
输出: 按字段 key 的 JSON dict, 值为英文提示词片段 (与 builder 模板同语种)。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests

from app.config import get_config

logger = logging.getLogger(__name__)

__all__ = ["ReverseExtractError", "TARGET_FIELDS", "FOCUS_FIELDS", "MODES",
           "extract_fields", "extract_flat", "generate_image", "adapt_prompt"]


class ReverseExtractError(RuntimeError):
    """反推/生成失败 (key 未配 / 网络错 / 模型输出不可解析) — 路由层转 4xx/5xx."""


# 生图: ZHIPU CogView (走同一 key; cogview-3-flash 免费)
_IMAGE_MODEL = os.environ.get("ZHIPU_IMAGE_MODEL", "cogview-3-flash")
_IMAGE_SIZES = {"1024x1024", "768x1344", "864x1152", "1344x768", "1152x864", "1440x720", "720x1440"}

# 提示词移植方言 (0907: CogView 出图一致、导入可灵 K2 完全变样实锤 — 词是方言不是通用指令)
_TEXT_MODEL = os.environ.get("ZHIPU_TEXT_MODEL", "glm-4-flash")
_DIALECTS: dict[str, dict[str, str]] = {
    "kling_video": {
        "cn": "可灵 图生视频",
        "spec": (
            "Target: Kling (可灵) IMAGE-TO-VIDEO. The user attaches a reference image as the first "
            "frame — that image ALREADY locks everything visual. The rewritten prompt must contain "
            "ZERO appearance attributes: no garment names, no colors, no materials, no body/face "
            "description (listing them makes Kling redraw and drift). Write ONLY: what MOVES (subject "
            "action, fabric/hairstyle swaying, environment motion like wind/rain), how the CAMERA "
            "moves (push in / pan / orbit), and atmosphere change. Fluent natural CHINESE, 2-4 "
            "sentences. If the source is purely static appearance with no motion, invent only gentle "
            "natural motion + slow camera push-in. No tag lists, no English, no headers."
        ),
    },
    "kling_image": {
        "cn": "可灵 文生图",
        "spec": (
            "Target: Kling (可灵) TEXT-TO-IMAGE. Rewrite as fluent natural CHINESE, one coherent "
            "paragraph: subject appearance, environment, lighting, style, camera feel. Kling prefers "
            "natural-language Chinese over English tag lists. Keep every concrete detail from the "
            "original; do not invent new ones."
        ),
    },
    "jimeng": {
        "cn": "即梦/豆包",
        "spec": (
            "Target: Jimeng / Doubao (Seedance family, ByteDance). Rewrite as natural CHINESE, "
            "short vivid sentences: 主体长相、服饰、环境、光线、风格、镜头感. Keep all concrete "
            "details, no English, no tag spam."
        ),
    },
}


# ── 反推模式 ──
# anchors: 结构化锚点 (自研, 喂生成页模板); pixel/tags: 整图平铺提示词,
# 预设摘自 ComfyUI-Prompt-Assistant (yawiii, 2298★, MIT 开源提示词资产) —
# 只取其 system_prompts_template.json, 不引入 ComfyUI 本体。
MODES = {
    "anchors": "结构化锚点 (填模板)",
    "pixel": "像素级整图反推",
    "tags": "Tag 风格 (SD/Danbooru)",
}
_PRESET_PATH = Path(__file__).absolute().parents[2] / "config" / "reverse_prompt_presets.json"
_MODE_PRESET_KEYS = {
    "pixel": ("vision_prompts", "vision_zh_像素级描述（by:阿丹）"),
    "tags": ("vision_prompts", "vision_zh_图像描述-Tag风格"),
}
_PRESETS: dict[str, Any] | None = None


def _load_presets() -> dict[str, Any]:
    global _PRESETS
    if _PRESETS is None:
        try:
            _PRESETS = json.loads(_PRESET_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[reverse] presets 加载失败 (%s), pixel/tags 模式不可用", exc)
            _PRESETS = {}
    return _PRESETS


def _preset_system(mode: str) -> str:
    cat, key = _MODE_PRESET_KEYS[mode]
    try:
        content = _load_presets()[cat][key]["content"]
    except (KeyError, TypeError) as exc:
        raise ReverseExtractError(f"预设提示词缺失: {mode} ({key})") from exc
    return str(content)


# 各目标类型的字段 key (与前端反推页/生成页字段一一对应)
TARGET_FIELDS: dict[str, list[str]] = {
    "person": ["subject", "expression", "hairstyle", "anchors", "identifier", "outfit", "shoes"],
    "prop":   ["name", "material", "finish", "scale", "anchors", "identifier"],
    "scene":  ["name", "light", "mood", "anchors", "layout", "shots"],
}

# 重点提取范围: key → 覆盖的字段
FOCUS_FIELDS: dict[str, dict[str, list[str]]] = {
    "person": {
        "face":   ["anchors", "identifier"],
        "hair":   ["hairstyle"],
        "outfit": ["outfit", "shoes"],
        "vibe":   ["subject", "expression"],
    },
    "prop": {
        "form":     ["name", "scale"],
        "material": ["material", "anchors"],
        "wear":     ["identifier"],
        "color":    ["finish"],
    },
    "scene": {
        "layout":    ["layout", "name"],
        "light":     ["light"],
        "mood":      ["mood"],
        "landmarks": ["anchors"],
    },
}

# 引擎可见的字段说明 — 属性清单式 (逼出细节, 2026-09-07: 「卡其色夹克」式三字薄输出实锤后强化)
_FIELD_HINTS: dict[str, dict[str, str]] = {
    "person": {
        "subject": "gender + rough age, e.g. Female, mid-20s",
        "expression": "expression + gaze quality, e.g. calm and confident, direct gaze",
        "hairstyle": "length + color + texture (straight/wavy/curly) + parting + styling",
        "anchors": "eye shape, eyelid type, eyebrow shape, nose bridge, lip shape, jawline, skin tone — one phrase each",
        "identifier": "one position-specific small mark (mole/scar/piercing); empty string if truly none visible",
        "outfit": "garment type + color + fabric + collar + closure (zip/buttons) + pockets + fit + condition, 4-8 attributes, e.g. khaki cotton field jacket, stand collar, brass two-way zip, four flap pockets, relaxed fit, slight fade on shoulders",
        "shoes": "style + color + material, e.g. white leather low-top sneakers, chunky rubber sole",
    },
    "prop": {
        "name": "object name + category, e.g. vintage brass pocket watch",
        "material": "main material + secondary materials + construction, e.g. brushed brass body, brown leather strap, domed glass cover, visible solder seams",
        "finish": "exact color names + surface treatment + wear level, e.g. aged gold with green patina, matte leather, light scratches on the lid",
        "scale": "size reference next to a hand/body, e.g. palm-sized, fits in one closed hand",
        "anchors": "texture, seams, engravings, mechanical details — one phrase each",
        "identifier": "one position-specific wear mark (scratch/dent/sticker); empty string if none visible",
    },
    "scene": {
        "name": "place type + time, e.g. rooftop apartment at dusk",
        "light": "time of day, light direction, hardness, shadow quality",
        "mood": "color temperature + atmosphere keywords",
        "anchors": "each fixed landmark object WITH its position, e.g. red sofa under the west window, bicycle leaning on the north railing",
        "layout": "openness, cardinal directions, depth layers, entry points",
        "shots": "suggested camera setups (wide establishing / eye-level / low angle)",
    },
}

_PROMPT_HEAD = (
    "你是图片反推提示词引擎。Look at the image and reverse-engineer it into structured "
    "prompt fragments for an AI image generator. HARD RULES:\n"
    "1. Output ONLY a JSON object — no markdown fence, no commentary.\n"
    "2. ALL values MUST be written in ENGLISH (英文提示词片段, 严禁输出中文). "
    'Write "brushed brass, visible grain", never "拉丝黄铜".\n'
    "3. Each non-empty value = a comma-separated list of AT LEAST 4 concrete, observable "
    "attributes. Thin answers like a single color or a single noun are FORBIDDEN — "
    'a jacket is not "khaki jacket", it is "khaki cotton field jacket, stand collar, '
    'brass zip, four flap pockets, relaxed fit, faded shoulders".\n'
    "4. Describe exactly what is visible — do not invent details that are not in the image.\n"
    '5. If a field cannot be determined, output empty string "" — 不要写 "无" / "none" / 重复字段名, 直接给 "".'
)

# 出口清洗: 模型偶发把「无/none/字段名回显」当值, 统一归空串
_EMPTY_VALUES = {"", "无", "没有", "未可见", "看不出", "不可见", "none", "n/a", "na", "-", "null", "nil"}
_CJK_RE = re.compile(r"[一-鿿]")


def _sanitize(v: str) -> str:
    v = (v or "").strip().strip('"').strip()
    if v.lower() in _EMPTY_VALUES:
        return ""
    # "磨损与标记: 无" / "identifier: none" 式回显 — 去掉 label 前缀后再判一次
    if ":" in v:
        head, tail = v.split(":", 1)
        if tail.strip().lower() in _EMPTY_VALUES:
            return ""
    return v


# 各目标的前置指令 — 锁定描述对象 (2026-09-07: 道具模式全空实锤, 缺主体锁定)
_TARGET_PREFACE: dict[str, str] = {
    "person": "Describe the PERSON in the image (their clothing counts as their outfit fields).",
    "prop": "First identify the ONE most prominent object or garment in the image "
            "(e.g. the jacket a person is wearing, the watch on the table). "
            "ALL fields describe that single object — fabric/leather counts as material.",
    "scene": "Describe the environment / location shown in the image.",
}


def _build_prompt(target: str, focus_keys: list[str]) -> str:
    fields = TARGET_FIELDS[target]
    if focus_keys:
        wanted: list[str] = []
        for k in focus_keys:
            for f in FOCUS_FIELDS[target].get(k, []):
                if f not in wanted:
                    wanted.append(f)
    else:
        wanted = fields
    lines = [_PROMPT_HEAD, "", _TARGET_PREFACE[target], "", "JSON keys (use exactly these):"]
    for f in fields:
        if f in wanted:
            lines.append(f'  "{f}": {_FIELD_HINTS[target][f]}')
        else:
            lines.append(f'  "{f}": ""  // not requested this time, keep empty')
    skeleton = json.dumps({f: "" for f in TARGET_FIELDS[target]}, ensure_ascii=False)
    lines.append("")
    lines.append(
        "Output EXACTLY this JSON structure — same keys, no extra keys, no nesting, "
        "keep each value under 30 words:"
    )
    lines.append(skeleton)
    return "\n".join(lines)


def _parse_json(text: str) -> dict[str, Any]:
    """模型输出 → dict; 容忍 markdown fence / 前后杂文."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        text = brace.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReverseExtractError(f"模型输出不可解析为 JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise ReverseExtractError(f"模型输出不是 JSON 对象: {str(data)[:200]}")
    return data


def _chat(image_data_url: str, prompt: str, system: str | None = None,
          max_tokens: int = 1024) -> str:
    """GLM-4V-Flash 单轮调用, 返回 message content 文本.

    max_tokens 硬上限 1024 (glm-4v-flash 参数范围 [1,1024], 传 2048 直接 400).
    """
    cfg = get_config()
    key = (cfg.zhipu.api_key or "").strip()
    if not key:
        raise ReverseExtractError(
            "ZHIPU_API_KEY 未配置 — 请在 digital_human/.env 填写后重启服务"
            " (open.bigmodel.cn/usercenter/apikeys, glm-4v-flash 免费)"
        )

    user_content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": image_data_url}},
        {"type": "text", "text": prompt},
    ]
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})

    body = {
        "model": "glm-4v-flash",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    url = f"{cfg.zhipu.base_url.rstrip('/')}/chat/completions"
    try:
        resp = requests.post(
            url, json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=(10, 120),
        )
    except requests.exceptions.RequestException as exc:
        raise ReverseExtractError(f"网络错误: {exc}") from exc
    if resp.status_code == 401:
        raise ReverseExtractError("ZHIPU_API_KEY 无效 (401)")
    if resp.status_code != 200:
        raise ReverseExtractError(f"ZHIPU API status {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ReverseExtractError(f"ZHIPU 返回结构异常: {resp.text[:200]}") from exc


def adapt_prompt(prompt: str, target: str) -> str:
    """提示词按目标模型方言改写 (glm-4-flash 文本模型, 免费).

    同一段词在不同模型是「方言」关系 — CogView 词直进可灵必变样;
    图生视频更要把外貌从词里剥掉 (交给首帧图), 否则模型重画漂移。
    """
    cfg = get_config()
    key = (cfg.zhipu.api_key or "").strip()
    if not key:
        raise ReverseExtractError("ZHIPU_API_KEY 未配置 — 请在 digital_human/.env 填写后重启服务")
    dialect = _DIALECTS.get(target)
    if not dialect:
        raise ReverseExtractError(f"未知目标模型: {target} (可选: {sorted(_DIALECTS)})")
    if not prompt.strip():
        raise ReverseExtractError("提示词为空, 无法改写")

    system = (
        "你是 AI 绘画/视频提示词移植专家。用户给一段为别的模型写的提示词, "
        "你把它改写成目标模型的方言。只输出改写后的提示词本身 — 无解释、无引号、无前缀。"
    )
    user = dialect["spec"] + "\n\n原提示词:\n" + prompt.strip()

    url = f"{cfg.zhipu.base_url.rstrip('/')}/chat/completions"
    try:
        resp = requests.post(
            url,
            json={
                "model": _TEXT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 800,
            },
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=(10, 90),
        )
    except requests.exceptions.RequestException as exc:
        raise ReverseExtractError(f"改写网络错误: {exc}") from exc
    if resp.status_code == 401:
        raise ReverseExtractError("ZHIPU_API_KEY 无效 (401)")
    if resp.status_code != 200:
        raise ReverseExtractError(f"改写 API status {resp.status_code}: {resp.text[:200]}")
    try:
        text = resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise ReverseExtractError(f"改写返回结构异常: {resp.text[:200]}") from exc
    if not text:
        raise ReverseExtractError("改写返回为空")
    return text


def generate_image(prompt: str, size: str = "1024x1024") -> bytes:
    """CogView 文生图 — 返回图片字节 (路由层落盘入库, 2026-09-07 工坊闭环).

    cogview-3-flash 免费, 走系统 ZHIPU key; 失败统一抛 ReverseExtractError.
    """
    cfg = get_config()
    key = (cfg.zhipu.api_key or "").strip()
    if not key:
        raise ReverseExtractError("ZHIPU_API_KEY 未配置 — 请在 digital_human/.env 填写后重启服务")
    if size not in _IMAGE_SIZES:
        size = "1024x1024"
    if not prompt.strip():
        raise ReverseExtractError("提示词为空, 无法生成")

    url = f"{cfg.zhipu.base_url.rstrip('/')}/images/generations"
    try:
        resp = requests.post(
            url,
            json={"model": _IMAGE_MODEL, "prompt": prompt[:2000], "size": size},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=(10, 180),
        )
    except requests.exceptions.RequestException as exc:
        raise ReverseExtractError(f"生图网络错误: {exc}") from exc
    if resp.status_code == 401:
        raise ReverseExtractError("ZHIPU_API_KEY 无效 (401)")
    if resp.status_code != 200:
        raise ReverseExtractError(f"CogView status {resp.status_code}: {resp.text[:200]}")
    try:
        item = resp.json()["data"][0]
    except (KeyError, IndexError, ValueError) as exc:
        raise ReverseExtractError(f"CogView 返回结构异常: {resp.text[:200]}") from exc

    img_url = item.get("url")
    if not img_url:
        raise ReverseExtractError("CogView 未返回图片 URL")
    try:
        img = requests.get(img_url, timeout=(10, 120))
    except requests.exceptions.RequestException as exc:
        raise ReverseExtractError(f"生成图下载失败: {exc}") from exc
    if img.status_code != 200 or not img.content:
        raise ReverseExtractError(f"生成图下载失败 HTTP {img.status_code}")
    return img.content


def extract_flat(image_data_url: str, mode: str) -> str:
    """整图平铺反推 (pixel/tags 预设) — 返回可直接使用的提示词文本."""
    content = _chat(
        image_data_url,
        prompt="请反推这张图片。" if mode == "pixel" else "请反推这张图片，输出标签流。",
        system=_preset_system(mode),
        max_tokens=1024,
    )
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    if not text:
        raise ReverseExtractError("模型返回空内容")
    return text


def _finalize(data: dict, target: str) -> dict[str, str]:
    """只保留合法 key, 清洗后规整为 str."""
    out: dict[str, str] = {}
    for f in TARGET_FIELDS[target]:
        v = data.get(f, "")
        out[f] = _sanitize(str(v)) if v is not None else ""
    return out


def _has_cjk(values: dict[str, str]) -> bool:
    return any(_CJK_RE.search(v) for v in values.values())


def extract_fields(image_data_url: str, target: str, focus_keys: list[str]) -> dict[str, str]:
    """结构化锚点反推 (anchors 模式), 返回 {字段 key: 英文片段}.

    image_data_url: data:image/...;base64,... 统一走 base64 (本地/Pexels 已下载字节).
    target: person | prop | scene;  focus_keys: 空 = 全部提取.
    小模型 (glm-4v-flash) 偶发无视英文约束回退中文 — 检出 CJK 后带训斥语重试一轮。
    """
    prompt = _build_prompt(target, focus_keys)
    try:
        data = _parse_json(_chat(image_data_url, prompt=prompt))
    except ReverseExtractError:
        # 小模型偶发换键名/嵌套/截断 — 带骨架训斥语重试一轮 (2026-09-07 jacket 实锤)
        logger.info("[reverse] JSON 解析失败, 追加骨架重试 (target=%s)", target)
        retry = (prompt + "\n\nCRITICAL: your previous output was NOT valid JSON with the "
                  "required keys. Output ONLY the exact skeleton above, filled with rich "
                  "English values. No other text.")
        data = _parse_json(_chat(image_data_url, prompt=retry))
    out = _finalize(data, target)

    if _has_cjk(out):
        logger.info("[reverse] 检出中文输出, 追加英文重试 (target=%s)", target)
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: your previous answer contained Chinese. That is FORBIDDEN. "
            "Rewrite the SAME JSON with every value translated to rich, detailed ENGLISH "
            "prompt phrases (4+ attributes each). Output ONLY the JSON."
        )
        try:
            data2 = _parse_json(_chat(image_data_url, prompt=retry_prompt))
            out2 = _finalize(data2, target)
            if not _has_cjk(out2):
                return out2
            # 重试仍中文: 取非空值更多的那份, 尽力而为 (中文提示词国内工具也能用)
            filled1 = sum(1 for v in out.values() if v)
            filled2 = sum(1 for v in out2.values() if v)
            return out2 if filled2 > filled1 else out
        except ReverseExtractError:
            return out
    return out
