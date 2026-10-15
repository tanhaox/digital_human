"""TTS 文本处理工具: 分段 / 净化 / 批量分组.

不依赖任何引擎后端, 是纯字符串逻辑 — 与 requests / sqlalchemy 等
域外依赖零耦合, 方便单测与复用。
"""
from __future__ import annotations

import re

__all__ = [
    "_sanitize_for_fish",
    "_tts_text",
    "_split_text",
    "_split_by_pause_marks",
    "_merge_lines_for_batch",
    "_ends_sentence",
    "_group_lines_semantic",
]

# 句末标点 + 可跟在句末后的闭合符 (引号/括号不算断句, 剥掉再看末字)。
_SENTENCE_END_CHARS = "。！？…!?;；"
_SENTENCE_TRAILING = "\"'\u201d\u2019\u300d\u300f\u3009\u300b\uff09)\u3011〉 \t"


def _sanitize_for_fish(text: str) -> str:
    """Work around Fish Speech s2-pro bug: ASCII + space + Chinese triggers 500.

    Fish Speech fails on strings like "GPT-5 真的要来了" (ASCII, space, CJK).
    Removing the space between ASCII alphanumerics and CJK characters avoids the
    crash without changing pronunciation materially.
    """
    # Remove spaces between ASCII alphanumerics/punctuation and CJK.
    # Run repeatedly until no more changes because patterns can overlap.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"([a-zA-Z0-9\-._%/+])(\s+)([一-鿿])", r"\1\3", text)
        text = re.sub(r"([一-鿿])(\s+)([a-zA-Z0-9\-._%/+])", r"\1\3", text)
    # Collapse multiple spaces to one.
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _tts_text(text: str, keep_breaks: bool = False) -> str:
    """Prepare text for TTS inference.

    ``||`` is a pipeline pause hint, not a real phoneme. Fish Speech will not
    interpret it as silence; replace it with a comma. Emotion tags such as
    ``[calm]`` and paralinguistic markers such as ``(break)`` are stripped
    because many Fish Speech builds crash when CJK immediately follows an
    ASCII tag. The original line (with tags) is preserved in the manifest.

    keep_breaks (2026-08-13→2026-08-14 修正): 原以为 IndexTTS 识别 ``||`` 为停顿而保留,
    实测 IndexTTS 不认 ``||``、会把它读成"炸"音. 现统一 ``||`` → 逗号 (标点停顿),
    indextts 亦然. keep_breaks 参数保留兼容但不再短路返回.
    """
    # 停顿标记 -0.5s- / -1s- (0914 用户语法): 拼装层垫静音, 后端绝不喂
    # (|| 读"炸"教训同源)。0917 根治: 旧前瞻 (?=\s|$) 只剥行尾/后随空白 — 句中
    # 标记 (编辑按钮光处插入, 如 "A。-1s-B档") 批/reroll 全漏直念"负一S" (033 包
    # 实锤), 现无条件剥离, 引擎零标记; 停顿时长由合成边界层垫入 (lines.py
    # _run_batch / audio.py reroll 按 _split_by_pause_marks 切片)。
    text = _PAUSE_MARK_ANY_RE.sub("", text)
    # Strip emotion / paralinguistic control tags.
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^)]+\)", "", text)
    # Pipeline pause hint -> comma (所有后端含 indextts; IndexTTS 不认 || 会读"炸").
    text = text.replace("||", "，")
    text = re.sub(r"[,，]{2,}", "，", text)
    text = re.sub(r"[,，]\s*([。！？])", r"\1", text)
    # Batch-join artifact: "句。||下一句" -> "句。，下一句" — drop the comma after
    # terminal punctuation, otherwise TTS renders an audible artifact (残音).
    text = re.sub(r"([。！？；])\s*[,，]+", r"\1", text)
    # Leading comma (line started with '||') has nothing to pause after.
    text = re.sub(r"^[,，]+", "", text)
    return text.strip()


def _split_text(
    text: str,
    segment_delimiter: str = "||",
    max_chars: int = 120,
    sentence_delimiters: str = "。；？！\n",
) -> list[str]:
    """Split text into synthesis segments.

    Priority:
      1. Explicit segment_delimiter (e.g. '||' from scripts) produces exact segments.
      2. Falls back to sentence-level splitting by sentence_delimiters.
      3. Hard-truncates any segment exceeding max_chars.
    """
    text = text.strip()
    if not text:
        return []

    # 停顿标记绝不进分段 (_synthesize_segments 路径无切片垫静音, 只能剥掉不念)
    text = _PAUSE_MARK_ANY_RE.sub("", text)

    if segment_delimiter in text:
        raw_segments = _split_by_delimiter(text, segment_delimiter)
    else:
        raw_segments = _split_by_sentence(text, sentence_delimiters)

    segments: list[str] = []
    for s in raw_segments:
        s = _sanitize_for_fish(s)
        if len(s) <= max_chars:
            if s:
                segments.append(s)
        else:
            segments.extend(_truncate_overlong(s, max_chars))
    return segments


def _split_by_delimiter(text: str, segment_delimiter: str) -> list[str]:
    """Split on an explicit delimiter (e.g. '||'), dropping empty pieces."""
    return [s.strip() for s in text.split(segment_delimiter) if s.strip()]


def _split_by_sentence(text: str, sentence_delimiters: str) -> list[str]:
    """Split on sentence-ending punctuation, keeping non-empty sentences."""
    raw: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in sentence_delimiters and current.strip():
            raw.append(current.strip())
            current = ""
    if current.strip():
        raw.append(current.strip())
    return raw


def _truncate_overlong(segment: str, max_chars: int) -> list[str]:
    """Hard-truncate an over-long segment into max_chars-sized chunks."""
    chunks: list[str] = []
    for i in range(0, len(segment), max_chars):
        chunk = segment[i : i + max_chars].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _merge_lines_for_batch(lines: list[str], max_chars: int = 300) -> list[list[int]]:
    """Group line indices into batches where each batch total ≤ max_chars.

    Lines are grouped purely by character count. Each group is synthesized as
    a single TTS call, then split back into individual segments by silence detection.

    Args:
        lines: List of text lines (one per segment).
        max_chars: Soft cap — a batch that ends exactly at max_chars is fine;
            a single line longer than max_chars gets its own batch.

    Returns:
        List of index groups, e.g. [[0, 1], [2, 3, 4], [5]]
    """
    batches: list[list[int]] = []
    current: list[int] = []
    current_chars = 0
    for idx, line in enumerate(lines):
        # Keep batches small enough that intra-sentence pauses don't accumulate
        # to a detectable silence split, but big enough for smooth prosody.
        effective_len = len(line)
        if effective_len > max_chars:
            if current:
                batches.append(current)
                current = []
                current_chars = 0
            batches.append([idx])
            continue
        if current_chars + effective_len > max_chars and current:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(idx)
        current_chars += effective_len
    if current:
        batches.append(current)
    return batches


def _ends_sentence(line: str) -> bool:
    """行尾是否句末 (剥闭合引号/括号后看末字) — 包缝只允许落在这些行后。"""
    s = line.rstrip(_SENTENCE_TRAILING)
    return bool(s) and s[-1] in _SENTENCE_END_CHARS


def _group_lines_semantic(
    lines: list[str], max_chars: int, walls: frozenset[int] | set[int] = frozenset(),
    question_break: bool = True,
) -> list[list[int]]:
    """包模式语义分组 (0913 深夜, 用户令"包缝必须语义切"): 贪心装包, 只在句末收包。

    与 _merge_lines_for_batch 的差别: 超限时回退到包内最后一个句末行收包,
    包缝永远落在句号/问号后 — 包=独立完整解码, 半句包缝 = 下包开头语气重置
    (逐行时代"神经病模式"的微缩版)。包内无句末行才硬切兜底 (同旧行为)。

    walls (情绪墙, P5): 必须开新包的行号集合 (情绪变化点) — 与旧行为一致,
    情绪变化强制断包。包内行因此必属同一情绪段, 回退收包不会跨情绪。

    整行约束: 包 = 连续整行划分 (display.json 分组 / reroll 包内定位 /
    AudioFile.segment_id 都依赖 line_indices 整行 1:1), 绝不腰斩单行。
    单行超限 → 独包 (同旧 overlong 行为; 上限是它自身长度, 140 钳制管不住,
    是既有在案风险 — 拆书稿实测最长行 46 字, 悬崖 ~170 字, 暂不另行切分)。
    """
    import bisect

    # ends: 全局句末行位置 (收包候选 = 句末行之后)
    ends = [i + 1 for i, ln in enumerate(lines) if _ends_sentence(ln)]
    batches: list[list[int]] = []
    start = 0
    while start < len(lines):
        acc = 0
        i = start
        _q_break = False
        while (i < len(lines) and (i == start or i not in walls)
               and acc + len(lines[i]) <= max_chars):
            acc += len(lines[i])
            i += 1
            # 设问停顿 (0914): 问句/停顿标记行强制收包 — 停顿永远落在 wav 边界,
            # 不切已解码音频 (bleed 病源不碰)。此断包边界已语义合法, 跳过溢出回退
            # (回退只认句末标点, 会把 -Xs- 标记行再切出去)
            if question_break and (_is_question(lines[i - 1]) or _pause_after(lines[i - 1]) is not None):
                _q_break = True
                break
        if i == start:  # 首行即超限 → 独包 (单行超限同旧 overlong 行为)
            batches.append([start])
            start += 1
            continue
        if i < len(lines) and not _q_break:
            # 停在溢出行或情绪墙: 回退到 (start, i] 内最后一个句末行, 无则硬切
            k = bisect.bisect_right(ends, i) - 1
            cut = ends[k] if k >= 0 and ends[k] > start else i
        else:
            cut = i
        batches.append(list(range(start, cut)))
        start = cut
    return batches


# ── 设问停顿 (0914 用户令: 设问句停 0.5-1s 留思考; -Xs- 行尾标记逐处覆盖) ──
# 前置守卫 (?<![A-Za-z0-9]): 标记前直接贴 ASCII 字母/数字的是连字词 (如
# "MS-1s-config"), 不是停顿标记 — 用户语法标记只跟在标点/汉字后。
_PAUSE_MARK_RE = re.compile(r"(?<![A-Za-z0-9])-(\d+(?:\.\d+)?)s-\s*$")

# 任意位置停顿标记 (0917 根治): 句中标记三层失守 (不剥/不垫/直念"负一S",
# 033 包实锤) — 行尾死约定废除, -Xs- 语义升级为"合成硬边界, 此处垫 X 秒"。
_PAUSE_MARK_ANY_RE = re.compile(r"(?<![A-Za-z0-9])-(\d+(?:\.\d+)?)s-")


def _split_by_pause_marks(text: str) -> list[tuple[str, float]]:
    """按停顿标记 -Xs- (任意位置) 切片段: [(片段, 片尾停顿秒), ...]。

    每片独立解码、片间垫静音 — 停顿永远落在解码边界 (0914 教义泛化到句中)。
    行首/连续标记产生的空片段丢弃, 其停顿折入前一片段尾; 全标记文本 → []。
    """
    parts = _PAUSE_MARK_ANY_RE.split(text or "")
    out: list[tuple[str, float]] = []
    for i in range(0, len(parts), 2):
        piece = parts[i].strip()
        pause = float(parts[i + 1]) if i + 1 < len(parts) else 0.0
        if piece:
            out.append((piece, pause))
        elif out and pause > 0:
            prev_text, _ = out[-1]
            out[-1] = (prev_text, pause)
    return out


def _is_question(line: str) -> bool:
    """设问句判定: 剥行尾停顿标记/引号/空白后以 ？/? 结尾。"""
    t = _PAUSE_MARK_RE.sub("", (line or "").strip())
    return t.rstrip("”’\"'』」… ").endswith(("？", "?"))


def _pause_after(line: str) -> float | None:
    """行尾停顿标记 -0.5s- / -1s- → 秒数; 无标记 None。"""
    m = _PAUSE_MARK_RE.search((line or "").strip())
    return float(m.group(1)) if m else None
