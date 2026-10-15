"""OCR 预扫服务 — 文字语言检测 + 国旗识别, 辅助 LLM 定位国内外.

依赖 easyocr (可选): pip install easyocr
未安装时自动跳过, 返回空结果, 不阻塞流水线.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 中文字符检测 ──────────────────────────────────────────────

_CJK_RE = re.compile(
    r"[一-鿿㐀-䶿豈-﫿⾀0-⾡f"
    r"　-〿＀-￯]"
)
_ENGLISH_RE = re.compile(r"[a-zA-Z]{2,}")


def _is_chinese_text(text: str) -> bool:
    """判断文本是否包含中文."""
    return bool(_CJK_RE.search(text))


def _is_english_text(text: str) -> bool:
    """判断文本是否主要为英文 (至少 2 个连续英文字母)."""
    return bool(_ENGLISH_RE.search(text))


# ── 国旗色块检测 ──────────────────────────────────────────────

def _detect_flag_colors(frame_path: Path) -> bool:
    """简单色块分析: 检测画面中上区域是否有中国国旗特征色块.

    中国国旗特征: 红色背景 + 黄色五角星.
    检测逻辑:
    1. 取画面中间偏上 1/4 区域
    2. 统计红色像素占比
    3. 若红色占比 > 15% 且画面有黄色区域, 判定可能有国旗

    Returns:
        True 若疑似检测到中国国旗.
    """
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        img = Image.open(frame_path).convert("RGB")
        w, h = img.size

        # 取上 1/3 区域 (国旗通常在画面中上部或顶部)
        upper = img.crop((0, 0, w, h // 3))
        pixels = list(upper.getdata())

        total = len(pixels)
        if total == 0:
            return False

        red_count = 0
        yellow_count = 0
        for r, g, b in pixels:
            # 红色: R 高, G 和 B 低
            if r > 160 and g < 100 and b < 100:
                red_count += 1
            # 黄色/金色: R 和 G 高, B 低
            if r > 180 and g > 150 and b < 80:
                yellow_count += 1

        red_ratio = red_count / total
        yellow_ratio = yellow_count / total

        # 红色占比 > 12% 且黄色点 > 3% → 疑似中国国旗
        return red_ratio > 0.12 and yellow_ratio > 0.03
    except Exception:
        return False


# ── EasyOCR 封装 ──────────────────────────────────────────────

_EASYOCR_AVAILABLE = None
_READER = None               # Reader 进程级单例 (init 开销大, 复用)


def _ensure_easyocr():
    """惰性检查 + 导入 easyocr."""
    global _EASYOCR_AVAILABLE
    if _EASYOCR_AVAILABLE is not None:
        return _EASYOCR_AVAILABLE
    try:
        import easyocr  # noqa: F401
        _EASYOCR_AVAILABLE = True
    except ImportError:
        logger.info("easyocr not installed; OCR pre-scan will be skipped. "
                     "Install with: pip install easyocr")
        _EASYOCR_AVAILABLE = False
    return _EASYOCR_AVAILABLE


def _get_reader():
    """惰性创建进程级 Reader 单例, 复用避免每次调用重复 init.

    本机 torch 为 CPU 版 (cuda_available=False), gpu=True 反而每次触发
    DataLoader pin_memory UserWarning 刷屏, 且白做 GPU 探测。显式 gpu=False。
    easyocr.Reader 初始化 (torch 探测 + 模型加载) 单次可达数秒, 预抽帧
    全库 1400+ 素材每素材一调, 必须复用。
    """
    global _READER
    if _READER is None:
        import easyocr  # 模块级不可见, 必须函数内再导入
        _READER = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    return _READER


def _empty_ocr_result() -> dict:
    return {
        "text_language": "unknown",
        "has_flag": False,
        "text_coverage": 0.0,
        "location_hint": "uncertain",
        "available": False,
    }


# ── 公共 API ─────────────────────────────────────────────────

def ocr_scan_frames(frame_paths: list[Path]) -> dict:
    """对多帧图片进行 OCR 文字检测 + 国旗检测.

    Args:
        frame_paths: 帧 PNG 文件路径列表.

    Returns:
        {
            "text_language": "chinese"|"english"|"both"|"none"|"unknown",
            "has_flag": bool,
            "text_coverage": float,       # 0.0-1.0 文字区域占比
            "location_hint": "domestic"|"foreign"|"uncertain",
            "available": bool,            # easyocr 是否可用
        }
    """
    if not _ensure_easyocr():
        # 无 easyocr 时仍尝试国旗检测
        has_flag = False
        for fp in frame_paths[:6]:  # 只扫前 6 帧
            if _detect_flag_colors(fp):
                has_flag = True
                break
        result = _empty_ocr_result()
        result["has_flag"] = has_flag
        if has_flag:
            result["location_hint"] = "domestic"
        return result

    import easyocr

    try:
        # 进程级 Reader 单例 (复用, 避免每次调用重复 init)
        reader = _get_reader()
    except Exception as exc:
        logger.warning("EasyOCR init failed: %s", exc)
        return _empty_ocr_result()

    if not frame_paths:
        return _empty_ocr_result()

    text_languages: set[str] = set()
    has_flag = False
    total_text_area = 0.0
    total_frame_area = 0.0
    scanned = 0

    for fp in frame_paths:
        if not fp.exists():
            continue
        try:
            results = reader.readtext(str(fp))
        except Exception as exc:
            logger.debug("OCR failed for %s: %s", fp.name, exc)
            continue

        for bbox, text, _conf in results:
            if _is_chinese_text(text):
                text_languages.add("chinese")
            if _is_english_text(text):
                text_languages.add("english")

            # 计算文字区域面积
            if len(bbox) >= 4:
                try:
                    x0, y0 = bbox[0]
                    x1, y1 = bbox[2]
                    total_text_area += abs(x1 - x0) * abs(y1 - y0)
                except (TypeError, IndexError):
                    pass

        scanned += 1
        # 国旗检测: 每帧都试, 命中一次即可
        if not has_flag and _detect_flag_colors(fp):
            has_flag = True

    # 统计语言类型
    if "chinese" in text_languages and "english" not in text_languages:
        text_lang = "chinese"
    elif "english" in text_languages and "chinese" not in text_languages:
        text_lang = "english"
    elif "chinese" in text_languages and "english" in text_languages:
        text_lang = "both"
    else:
        text_lang = "none"

    # 推断国内外
    if text_lang == "chinese":
        location_hint = "domestic"
    elif text_lang == "english" and has_flag:
        location_hint = "domestic"  # 国旗优先
    elif text_lang == "english":
        location_hint = "foreign"
    elif has_flag:
        location_hint = "domestic"  # 疑似中国国旗
    else:
        location_hint = "uncertain"

    # 计算文字覆盖率
    if scanned > 0:
        # 粗略估算单帧面积: 假设 1920x1080
        total_frame_area = scanned * 1920 * 1080
    text_coverage = round(min(1.0, total_text_area / max(total_frame_area, 1)), 4)

    result = {
        "text_language": text_lang,
        "has_flag": has_flag,
        "text_coverage": text_coverage,
        "location_hint": location_hint,
        "available": True,
    }
    logger.debug("OCR scan result: %s", result)
    return result
