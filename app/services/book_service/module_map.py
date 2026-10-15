# -*- coding: utf-8 -*-
"""六拍模块总线 (0917 架构令): 文档结构 = 全链唯一结构真相源.

正文的【A-B秒｜段名】标签只是人读视图, 标记存活与否不再影响下游 —
生成/修稿时把结构解析成模块表落库 (Episode.module_json), TTS 分包
(模块墙)/切场(场=模块)/分镜(refs) 全部从模块表取结构。

标签内的时间数字一律弃用: 时长真相 = TTS 实测 (架构定案, 防第二套
时间真相源)。模块表只保留 段名/顺序/末行锚文本(tail) — tail 用于在
任意文本形态 (原稿/清洗稿/改字后稿) 上重新定位模块边界, 幂等且耐编辑。
"""
from __future__ import annotations

import re

# 容错口径 (0917): 三种实盘形态全认 —
#   ①【0-28 秒｜钩子】(ep3+ 生成稿, 数字/秒/竖线周围任意空白)
#   ②【钩子】(人工存档, 无时间数字 — 0917 用户 ep2 存档实锤此形态)
#   ③【核心概念拆解（一）｜撞墙测试】(主名｜副题)
# 正文【】是标签专用符号 (生成铁律), 放宽不误伤; 主名用于模块准入, 副题存 label。
MODULE_LABEL_RE = re.compile(
    r"【\s*(?:\d+\s*-\s*\d+\s*秒\s*[｜|]\s*)?([^】｜|]+?)\s*(?:[｜|]\s*([^】]+?))?\s*】")

# 拆书六段名 (规范形, 与 script_parser._BOOK_LABELS 对齐; 匹配时去空白)
BOOK_MODULE_NAMES = ["钩子", "回顾+引入", "核心概念拆解（一）", "核心概念拆解（二）",
                     "核心概念拆解（三）", "总结+下期预告"]

# 标签行/打字卡行不属任何模块正文 (与 clean_episode_script 剥离口径一致)
_NON_SPEECH_PREFIXES = ("打字卡：", "打字卡:")


def norm_name(name: str) -> str:
    """段名规范化: 去所有空白 (「核心概念拆解（一）」vs「核心概念拆解(一)」等变体归一)."""
    return re.sub(r"\s+", "", name or "")


def norm_tail(text: str) -> str:
    """锚文本规范化: 仅去空白 — 保留文字本身, 文字被改 = 匹配失败 (诚实暴露, 不模糊兜底)."""
    return re.sub(r"\s+", "", text or "")


_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")


def _last_sentence(line: str) -> str:
    """行文本 → 最后一个完整句 (按句末符切).

    0918 实锤: TTS 行空间是句级+短句合并 ("<10 字并入前句"), 末句文本在合并中
    保持完整且必居行尾 — 末句锚比整行锚耐合并 (ep2 总结末行被吞实锤)。"""
    parts = [p for p in _SENT_SPLIT_RE.split(line.strip()) if p.strip()]
    return parts[-1] if parts else line.strip()


def _is_non_speech(line: str) -> bool:
    s = line.strip()
    return bool(MODULE_LABEL_RE.fullmatch(s)) or any(
        s.startswith(p) for p in _NON_SPEECH_PREFIXES)


def _canon_name(name: str) -> str | None:
    """段名 → 标准段名 (别名归一); 非标准返回 None (不入模块表).

    ep5 实锤变体「总结+下集预告」(下集≠下期) — 前缀归一收纳;
    「双评论钩子」startsWith 检验不误入 (钩子段必须以钩子开头)。"""
    n = norm_name(name)
    if n.startswith("钩子"):
        return "钩子"
    if n.startswith("回顾"):
        return "回顾+引入"
    if n.startswith("核心概念拆解"):  # 一/二/三各自保留 (多段拆解由书定)
        return n
    if n.startswith("总结"):
        return "总结+下期预告"
    return None


def parse_modules(script_text: str) -> list[dict]:
    """原稿 → 模块表 [{"idx", "name", "tail", "label"}] (按出现顺序).

    准入 (0917 存档实锤后定): 段名经 _canon_name 归一须是标准段名 —
    【钩子】【核心概念拆解（一）｜撞墙测试】入表 (副题存 label),
    「总结+下集预告」别名归一收纳 (ep5 实锤);
    【双评论钩子】等非标准名标签 = 非语音行 (剥离不念), 不入模块表。
    tail = 模块正文最后一句 (末句锚 — 耐 TTS 短句合并, 定位墙用)。
    空模块 (连续标签) tail 继承上一模块 tail — 墙定位时自然并到同一行。
    无标签返回 [] (调用方决定: 补标记轮 / 按 module_json 旧表对齐恢复)。
    """
    modules: list[dict] = []
    cur_name: str | None = None
    cur_label: str = ""
    cur_tail: str | None = None
    for line in (script_text or "").splitlines():
        m = MODULE_LABEL_RE.fullmatch(line.strip())
        if m:
            if cur_name is not None:
                modules.append({"idx": len(modules), "name": cur_name,
                                "tail": cur_tail or "", "label": cur_label})
            name_raw, label = m.group(1), (m.group(2) or "").strip().lstrip("｜|").strip()
            canon = _canon_name(name_raw)
            if canon:
                cur_name = canon
                cur_label = label
            else:  # 非标准段名标签: 终止当前段 (后续行不属任何语音模块)
                cur_name = None
                cur_label = ""
            cur_tail = None
            continue
        if _is_non_speech(line):
            continue
        if line.strip():
            cur_tail = _last_sentence(line)
    if cur_name is not None:
        modules.append({"idx": len(modules), "name": cur_name, "tail": cur_tail or "",
                        "label": cur_label})
    # 空模块 tail 继承前一模块 (定位时并到同一行)
    prev = ""
    for mod in modules:
        if not mod["tail"]:
            mod["tail"] = prev
        prev = mod["tail"]
    return modules


def find_walls(lines: list[str], modules: list[dict]) -> tuple[set[int], list[str]]:
    """在语音行序列 (与 tts_lib._split_line_indices 同口径: strip+去空行) 上定位
    每模块末行号 → (墙行号集合, 未对齐模块名列表).

    顺序贪心: 模块 i 的 tail 在 (上一墙, 模块 i+1 tail] 范围内找规范化匹配行。
    未对齐 = 正文被改/删段 — 如实报告, 不静默猜。
    """
    walls: set[int] = set()
    missed: list[str] = []
    search_from = 0
    for i, mod in enumerate(modules):
        want = norm_tail(mod.get("tail") or "")
        if not want:
            missed.append(mod["name"])
            continue

        def _match(j: int, anchor: str) -> bool:
            """末句锚匹配 (0918): TTS 行=句级+短句合并 — 目标行以锚句结尾
            (短句并前句/独立成行两态全中); 超短锚 (<6 字) 退全等防误配。"""
            n = norm_tail(lines[j])
            return n == anchor or (len(anchor) >= 6 and n.endswith(anchor))

        # 候选窗: 本模块 tail 位置必须早于下一模块 tail (若有) 的位置
        next_want = norm_tail(modules[i + 1].get("tail") or "") if i + 1 < len(modules) else ""
        hit = -1
        limit_next = -1
        if next_want:
            for j in range(search_from, len(lines)):
                if _match(j, next_want):
                    limit_next = j
                    break
        upper = limit_next if limit_next > 0 else len(lines)
        for j in range(search_from, upper):
            if _match(j, want):
                hit = j
                break
        if hit < 0:
            missed.append(mod["name"])
            continue
        walls.add(hit)
        search_from = hit + 1
    return walls, missed


def find_para_walls(script_text: str, lines: list[str]) -> tuple[set[int], list[str]]:
    """稿件段落/节标 → 段首墙行号 (1004 用户令: 写稿结构沿用为音频结构).

    段首 = 空行后的第一个语音行, 或 【…】 节标后的第一个语音行 (节标不念)。
    合成文本按 "\\n" 拼接无空行 → 纯字数贪心把身份句与下节首句焊进一包
    (ep6 008 包实锤), 气口引擎的段落检测也同盲空转 — 段首墙两头都救。
    锚匹配与 find_walls 同口径 (末句锚 endswith, 改稿=失配如实上报不猜);
    篇首 (行0) 天然开包, 不计入。返回 (墙行号集, 未对齐段首文本列表)。
    """
    starts: list[str] = []
    after_break = False  # 篇首不算墙 (行0天然开包)
    for raw in (script_text or "").splitlines():
        s = raw.strip()
        if not s:
            after_break = True
            continue
        if s.startswith("【") and s.endswith("】"):
            after_break = True
            continue
        if after_break:
            starts.append(s)
        after_break = False
    walls: set[int] = set()
    missed: list[str] = []
    cursor = 1
    for k, anchor in enumerate(starts):
        want = norm_tail(_last_sentence(anchor) or anchor)
        if not want:
            continue
        # 下一锚的最早可能位 = 当前锚位之后 — 用剩余锚数放宽窗口防中间失配连环
        limit = len(lines) - (len(starts) - k - 1)
        hit = -1
        for j in range(cursor, max(cursor, limit + 1)):
            n = norm_tail(lines[j])
            if n == want or (len(want) >= 6 and n.endswith(want)):
                hit = j
                break
        if hit < 0:
            missed.append(anchor[:12])
            continue
        walls.add(hit)
        cursor = hit + 1
    return walls, missed


def walls_for_cleaned(cleaned_text: str, modules: list[dict]) -> tuple[set[int], list[str]]:
    """清洗后文本 (标签行/打字卡行已剥) → 模块墙行号 (0 基, 对齐 TTS 行序列)."""
    lines = [l for l in (ln.strip() for ln in (cleaned_text or "").splitlines()) if l]
    return find_walls(lines, modules)
