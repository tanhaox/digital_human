"""TTS 引擎后端: fish / f5 / indextts 三个策略实现.

每个函数是一个独立的后端策略: 相同的签名 (text, output_path, ...)
返回合成的 WAV 路径。调度逻辑 (回退顺序) 在 orchestrator.py 中,
本模块不感知自动回退 — 职责单一。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from .audio import _write_wav

logger = logging.getLogger(__name__)
from .constants import DEFAULT_F5_URL, DEFAULT_FISH_URL, DEFAULT_INDEXTTS_URL
from .http import _http_post_bytes, _http_post_json, _pack_msgpack, requests

__all__ = ["fish_speech_tts", "f5_tts", "indextts_tts", "indextts25_tts",
           "_marks_to_bare_pinyin", "_strip_pinyin_marks"]

# 拼音标注协议 <字|PINYIN> (repo 内部通用形态, 见 app/services/pinyin_fix.py)。
# IndexTTS2 前端不认该协议 — 只认**裸内联拼音** (拼音代替字, 官方测例
# "受不liao3你了"): 原样发会被 BPE 切成 [字, |, PINYIN] → 字和拼音各读一遍
# (2026-09-03 蛤蟆先生 PPT 实听抓到 "蛤HA2蟆MA2" 连读)。引擎边界统一转换。
_MARK_RE = re.compile(r"<(\S{1,4})\|([A-Z]+[1-5])>")


def _marks_to_bare_pinyin(text: str) -> str:
    """indextts: <蛤|HA2> → ``HA2 `` (裸拼音替代字; 相邻标注间留空格防 ASCII 粘连)。

    服务端 front.correct_pinyin 自动把 jqx+u 纠成 v (ju2→jv2), 无需本地处理。"""
    return _MARK_RE.sub(lambda m: f"{m.group(2)} ", text)


def _strip_pinyin_marks(text: str) -> str:
    """fish/f5 (无拼音能力): <蛤|HA2> → 蛤 — 退化为裸字, 避免拼音被当英文读。"""
    return _MARK_RE.sub(r"\1", text)


def fish_speech_tts(
    text: str,
    output_path: Path,
    base_url: str = DEFAULT_FISH_URL,
    reference_audio: Path | None = None,
    reference_text: str = "",
    temperature: float | None = None,
    top_p: float | None = None,
    repetition_penalty: float | None = None,
    seed: int | None = None,
) -> Path:
    """Call Fish Speech /v1/tts and save WAV."""
    text = _strip_pinyin_marks(text)
    payload = _build_fish_payload(text, reference_audio, reference_text,
                                  temperature, top_p, repetition_penalty, seed)
    url = base_url.rstrip("/") + "/v1/tts"
    packed = _pack_msgpack(payload)
    headers = {"content-type": "application/msgpack"}
    audio_bytes = _fetch_audio_bytes(
        url, packed, headers,
        requests_kwargs={"params": {"format": "msgpack"}},
    )
    if not audio_bytes or len(audio_bytes) < 44:
        raise RuntimeError("Fish Speech returned empty or invalid audio")
    output_path.write_bytes(audio_bytes)
    return output_path


def _build_fish_payload(
    text: str,
    reference_audio: Path | None,
    reference_text: str,
    temperature: float | None,
    top_p: float | None,
    repetition_penalty: float | None,
    seed: int | None,
) -> dict[str, Any]:
    """Mirror the official api_client ServeTTSRequest shape (msgpack-encoded)."""
    references: list[dict] = []
    if reference_audio and reference_audio.exists():
        audio_bytes = reference_audio.read_bytes()
        references.append({"audio": audio_bytes, "text": reference_text or text})
    return {
        "text": text,
        "references": references,
        "reference_id": None,
        "format": "wav",
        "latency": "normal",
        "max_new_tokens": 1024,
        "chunk_length": 200,
        "top_p": top_p if top_p is not None else 0.7,
        "repetition_penalty": repetition_penalty if repetition_penalty is not None else 1.5,
        "temperature": temperature if temperature is not None else 0.3,
        "streaming": False,
        "use_memory_cache": "off",
        "seed": seed,  # None = random; int = deterministic at low temperature
    }


def _fetch_audio_bytes(
    url: str, data: bytes, headers: dict, requests_kwargs: dict | None = None,
) -> bytes:
    """POST raw bytes; prefer requests (msgpack), fall back to urllib."""
    if requests is not None:
        resp = requests.post(url, data=data, headers=headers, timeout=300,
                             **(requests_kwargs or {}))
        if resp.status_code != 200:
            raise RuntimeError(f"Fish Speech HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.content
    return _http_post_bytes(url, data, headers, timeout=300)


def f5_tts(
    text: str,
    output_path: Path,
    base_url: str = DEFAULT_F5_URL,
    ref_audio: Path | None = None,
    ref_text: str = "",
) -> Path:
    """Call F5-TTS Gradio API and save WAV."""
    text = _strip_pinyin_marks(text)
    url = base_url.rstrip("/") + "/api/predict"
    ref_audio_b64 = ""
    if ref_audio and ref_audio.exists():
        ref_audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode("utf-8")
    payload = {
        "fn_index": 0,
        "data": [
            ref_text or text,  # ref_text
            text,              # gen_text
            ref_audio_b64,     # ref_audio (base64)
            "",                # remove_silence (optional)
        ],
    }
    result = _http_post_json(url, payload, timeout=300)
    if not result.get("data"):
        raise RuntimeError(f"F5-TTS returned no data: {result}")
    _decode_f5_response(result["data"][0], base_url, output_path)
    return output_path


def _decode_f5_response(audio_payload: Any, base_url: str, output_path: Path) -> None:
    """Gradio returns [sr, ndarray] or base64 string / file dict depending on version."""
    if isinstance(audio_payload, dict) and "name" in audio_payload:
        file_url = base_url.rstrip("/") + "/file=" + audio_payload["name"]
        audio_bytes = _http_post_bytes(file_url, {}, timeout=60)
        output_path.write_bytes(audio_bytes)
    elif isinstance(audio_payload, str):
        output_path.write_bytes(base64.b64decode(audio_payload))
    else:
        sr, audio_arr = audio_payload
        _write_wav(output_path, np.array(audio_arr), int(sr))


def indextts_tts(
    text: str,
    output_path: Path,
    base_url: str = DEFAULT_INDEXTTS_URL,
    master_audio: Path | None = None,
    master_text: str = "",
    master_style: str = "calm",
    do_sample: bool = True,
    top_p: float = 0.8,
    top_k: int = 30,
    temperature: float = 0.8,
    max_text_tokens_per_segment: int = 300,
    seed: int | None = None,
    use_emo_text: bool = False,
    emo_text: str | None = None,
    emo_vector: list[float] | None = None,
    emo_alpha: float = 1.0,
    emo_audio_prompt: Path | str | None = None,
    duration_factor: float = 1.0,
) -> Path:
    """调 IndexTTS2 api_server (7862) /v1/tts. 响应是 wav bytes, 持久化到 output_path.

    duration_factor (2026-08-25): IndexTTS2.5 语速控制 (0.5-2.0, 时长系数 <1 加速);
    旧版 IndexTTS2 服务忽略该字段, 向后兼容.
    """
    if master_audio is None or not master_audio.exists():
        raise RuntimeError(
            f"indextts requires master_audio_path, got {master_audio}"
        )
    # 拼音标注协议 → 裸拼音 (协议原样发 = 字+拼音各读一遍, 见 _MARK_RE 注释)
    text = _marks_to_bare_pinyin(text)
    payload = _build_indextts_payload(
        text, master_audio, master_text, master_style, do_sample, top_p,
        top_k, temperature, max_text_tokens_per_segment, seed,
        use_emo_text, emo_text, emo_vector, emo_alpha, emo_audio_prompt,
        duration_factor=duration_factor,
    )
    audio_bytes = _fetch_indextts_audio(base_url, payload)
    if not audio_bytes or len(audio_bytes) < 44:
        raise RuntimeError("IndexTTS2 returned empty or invalid wav")
    output_path.write_bytes(audio_bytes)
    return output_path


def _build_indextts_payload(
    text: str,
    master_audio: Path,
    master_text: str,
    master_style: str,
    do_sample: bool,
    top_p: float,
    top_k: int,
    temperature: float,
    max_text_tokens_per_segment: int,
    seed: int | None,
    use_emo_text: bool = False,
    emo_text: str | None = None,
    emo_vector: list[float] | None = None,
    emo_alpha: float = 1.0,
    emo_audio_prompt: Path | str | None = None,
    duration_factor: float = 1.0,
) -> dict[str, Any]:
    payload = {
        "text": text,
        "spk_audio_prompt": str(master_audio.resolve()),
        "master_text": master_text,
        "master_style": master_style,
        "max_text_tokens_per_segment": max_text_tokens_per_segment,
        "do_sample": do_sample,
        "top_p": top_p,
        "top_k": top_k,
        "temperature": temperature,
        "seed": seed,
        "use_emo_text": use_emo_text,
        "emo_text": emo_text,
        "emo_alpha": emo_alpha,
        # IndexTTS2.5 语速 (2026-08-25): 旧版服务 pydantic 忽略未知字段, 安全透传
        "duration_factor": duration_factor,
    }
    if emo_vector is not None:
        payload["emo_vector"] = emo_vector
    if emo_audio_prompt is not None:
        payload["emo_audio_prompt"] = str(emo_audio_prompt)
    return payload


def _fetch_indextts_audio(base_url: str, payload: dict[str, Any]) -> bytes:
    url = base_url.rstrip("/") + "/v1/tts"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    # 0919 超时自适应 (ep4 实锤: GPU 被 ComfyUI 驻留栈挤压 → 10.4s/步 × 25 步 >
    # 300s 定额 → 整 job "timed out"): 按文本量伸缩 + 超时单次重试 (服务端
    # 无副作用, 重试安全; 已生成包走断点续传, 重试只救当前包)
    n_chars = len(str(payload.get("text") or ""))
    timeout = 300 + int(n_chars * 2.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"IndexTTS2 HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError):
        import time as _t
        logger.warning("[indextts] 超时 (%ds, %d字) — 清场后单次重试", timeout, n_chars)
        _t.sleep(5)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

# ── IndexTTS-2.5 老谭读书通道 (2026-09-10) ──
# 与 indextts_tts 的三点差异 (案卷见 memory indextts-25-upgrade):
#   1. 保留 <字|PIN> 原生注音 (2.5 前端解析; 2 只认裸拼音, 故老引擎做转换)
#   2. 2.5 预洗: ｜【】剥离 / 连续大写缩写加点 (HBO→H.B.O) / 裸 --→到
#   3. 情感参考音频 (emo_audio_prompt) — 音色克隆老谭 + 韵律克隆参考音
_ABBREV_RE = re.compile(r"(?<![A-Za-z.])([A-Z]{3,})(?![A-Za-z])")

_ANNOT_RE = re.compile(r"<[^<>|]*\|[^<>]*>")

def _wash_indextts25(text: str) -> str:
    """2.5 文本预洗 (实测 bug 清单): 标点静默丢弃类直接剥, 缩写强制分读, -- 转口语.

    注音标签 <字|PIN> 内部必须原样保护 — 缩写加点若误伤标签内拼音
    (<行|HANG2>→<行|H.A.N.G2>) 前端拿到碎拼音直接念鬼话 (首样张实锤)。
    """
    parts = _ANNOT_RE.split(text)
    keep = _ANNOT_RE.findall(text)
    washed = []
    for i, seg in enumerate(parts):
        seg = re.sub(r"[｜【】]", " ", seg)
        seg = _ABBREV_RE.sub(lambda m: ".".join(m.group(1)), seg)  # HBO → H.B.O
        seg = seg.replace("--", "到")
        # 书名号 2.5 直接跳过 (0910 实锤: 《后西游记》连读奇怪) — 开书名号换气口,
        # 闭书名号跟随原句读; 去重防标点堆叠 (，，→，; ，。→。)
        seg = seg.replace("《", "，").replace("》", "，")
        # 0912 行尾破折号幻觉: 行尾开放式 —— (未完句感) 会让模型续写下一行开头
        # ("三个维度——"→念出"第一,") — 行尾破折号一律剥掉, 停顿交给静音切分
        seg = re.sub(r"[—…]+$", "", seg.rstrip())
        seg = re.sub(r"，([，。！？；：、])", lambda m: m.group(1), seg)
        seg = seg.replace("，，", "，")
        washed.append(seg)
        if i < len(keep):
            washed.append(keep[i])
    return "".join(washed)


def indextts25_tts(
    text: str,
    output_path: Path,
    base_url: str,
    master_audio: Path | None = None,
    master_text: str = "",
    do_sample: bool = True,
    top_p: float = 0.8,
    top_k: int = 30,
    temperature: float = 0.8,
    max_text_tokens_per_segment: int = 300,
    emo_alpha: float = 0.6,
    emo_audio_prompt: Path | str | None = None,
    duration_factor: float = 1.16,
) -> Path:
    """调 IndexTTS2.5 api_server (7866) /v1/tts — 老谭读书专用通道.

    采样参数 do_sample=True 必须保持 (关=念经, 用户实测); duration 1.16 /
    emo_alpha 0.6 为 webui 实测定稿默认, 调用方可覆盖。P5 情绪向量不传 —
    韵律全部来自 emo_audio_prompt 情感参考 (樊登说书感)。"""
    if master_audio is None or not master_audio.exists():
        raise RuntimeError(f"indextts25 requires master_audio, got {master_audio}")
    payload = _build_indextts_payload(
        _wash_indextts25(text), master_audio, master_text, "calm",
        do_sample, top_p, top_k, temperature, max_text_tokens_per_segment,
        None, False, None, None, emo_alpha, emo_audio_prompt,
        duration_factor=duration_factor,
    )
    audio_bytes = _fetch_indextts_audio(base_url, payload)
    if not audio_bytes or len(audio_bytes) < 44:
        raise RuntimeError("IndexTTS2.5 returned empty or invalid wav")
    output_path.write_bytes(audio_bytes)
    return output_path
