# -*- coding: utf-8 -*-
"""离线蒸馏器  — 全书 → 精华 txt (规模.txt 式), 本地 Qwen3.6 批量跑.

架构: 重计算推离线 (4090 闲时批量), 拆书产线只吃 6-15k 精华 L0 (单发上下文)。
方法论借 cangjie-skill 阶段0+1 (Adler 整书理解 + 提取), 单模型轻量版;
重点书仍可手动跑完整 cangjie (Claude), 其 DIGEST.md 可直接落书库当精华。

底座 2026-10-12 换防: gemma-4-31b-crack (多轮产线服役) → Qwen3.6-35B-A3B
Uncensored Aggressive (太白金星同卷对决胜出: 池 375vs213 / 健康闸 4/4 / 2.2x tok/s)。
llama-server 生命周期礼仪 (用户决策 2026-08-19): 端口活则复用不启; 批次结束**全杀腾卡**
(llama-server.exe 全实例按 PID 精确 kill, 用户已授权含手动实例)。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

import requests

from app.services.book_service.reader import clean_book_title, read_book
from app.services.prompt_guard import wrap_source

logger = logging.getLogger(__name__)

__all__ = ["GemmaClient", "ensure_gemma", "shutdown_gemma_all",
           "distill_book", "pending_distill_books", "DISTILL_SUFFIX"]

DISTILL_SUFFIX = ".蒸馏"
# ── 本地蒸馏底座: Qwen3.6-35B-A3B Uncensored Aggressive (1012 对决换防) ──
# 2026-10-12 太白金星同卷对决: Qwen3.6 池 375 vs gemma 213 条 / 健康闸 4/4 vs 3/4 /
# 131 vs 59 tok/s; gemma 已退役删除。类名 GemmaClient 保留 (多模块引用, 仅是
# llama-server OpenAI 客户端, 与底座无关)。原 gemma 配置的 mmproj/draft-mtp 一并移除
# (Qwen3.6 纯文本无视觉件); -ub 512→4096 (大包 non-causal assert 崩溃坑, 1012 实弹)。
GEMMA_URL = "http://127.0.0.1:12348"
GEMMA_MODEL = "qwen36-aggressive"
_LLAMA_BIN = r"E:\Llama-cpp-13\llama-server.exe"
_LLAMA_CWD = r"E:\Llama-cpp-13"  # exe 依赖同目录 cublas DLL, 必须以它为工作目录
# 参数与 1012 对决实跑逐字一致 (temp 0.2 利提取稳定性)
_LLAMA_ARGS = [
    "-m", r"E:\Llama-cpp\models\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q5_K_P.gguf",
    "--alias", GEMMA_MODEL,
    "--temp", "0.2", "--top-p", "0.95", "--top-k", "20", "--min-p", "0.00",
    "--port", "12348", "--host", "127.0.0.1",
    "-ngl", "999", "-c", "131072", "-b", "4096", "-ub", "4096",
    "-fa", "on", "-ctk", "f16", "-ctv", "f16", "--device", "CUDA0",
    "--parallel", "1", "--fit", "on", "--fit-target", "4096",
    "--cache-ram", "6144",
    "-lv", "1",
]
# 全书(需蒸馏) vs 精华(人写/蒸馏产物) 的体量分界
_FULL_TEXT_MIN = 20000

_EXTRACT_SYS = (
    "你是书籍深度提取器(借 cangjie RIA-TV++ 阶段1)。对给定文本块做**穷尽式**提取,"
    "宁多勿漏, 不要过度概括。输出严格 JSON: "
    "{\"观点\":[str], \"概念\":[{\"name\":str,\"mech\":str}], "
    "\"故事\":[{\"title\":str,\"背景\":str,\"主体\":str,\"决策与转折\":str,\"结果\":str,\"启示\":str}], "
    "\"案例\":[{\"desc\":str,\"point\":str}], "
    "\"方法\":[{\"name\":str,\"步骤\":[str],\"来源\":\"书中明示|从案例归纳\",\"适用\":str,\"边界\":str}], "
    "\"路径\":[{\"阶段\":str,\"触发\":str,\"动作\":str,\"结果\":str}], "
    "\"数据\":[str], \"原句\":[str]}。"
    "概念必须带机制解释; **故事必须是叙事体**(谁/何时/什么处境/做了什么决定/转折/结果 — "
    "保留年份人物代名作等具体物, 禁压缩成一句干条); 方法=书里教的或从案例归纳的可执行步骤/清单"
    "(步骤逐条列出); 路径=全书主线的关键台阶(什么触发了什么动作, 导致了什么结果); "
    "反模式=书中的坑/失败教训(什么情境下做了什么导致坏结果, 书给的正解); "
    "概念mech尽量带决策规则形态(遇到X→书中认为该Y); "
    "原句必须逐字不得改写; 人物头衔/职称/年代严格照原文 (书里没写的头衔不加——如书只说'某人'"
    "不得提取成'CEO某人'); 只提取文本中存在的, 不得编造。"
)

_COMPOSE_SYS = (
    "你是书籍精华编纂器。把【提取池】(全书穷尽提取的结构化信息)编纂成一篇面向讲书创作的精华稿。"
    "讲书创作吃三类料 : 听得进的故事、带得走的方法、看得见的路径 — 三类缺一, 下游全链无米。"
    "**多形态去重律 (1005 治本, 同日二修收窄 — 治薄事故)**: 仅当池中出现**明显同一事件/同一原句**"
    "的重复变体 (源 epub 多形态复制所致) 才合并取最详一份; **相近主题但角度/细节/案例不同的条目"
    "不算重复, 全量保留** — 宁可多编不删料, 薄稿比重复更伤下游。"
    "**产量地板 (1005 健康闸对齐)**: 成品总字数 ≥ 池字数的 40% (且不低于 1.2 万字, 池小从池); "
    "故事库=池中【故事】全量入此 (一条不删, 允许合并整理); 方法与清单同理全量 — "
    "编纂是整理者不是删减者, 十节宁长勿薄。"
    "产物结构十节:\n"
    "## 元问题 — 一句大白话问句: 作者到底在回答一个什么问题 , "
    "全稿一切内容为回答它而存在\n"
    "## 核心哲学 (3-6句)\n"
    "## 核心概念与机制 (逐条: 反驳了什么旧认知/核心机制/精确策略/数据/防御或应用, 有条写条; "
    "圈出 1~2 个颠覆常识的推导点, 标注【反直觉】)\n"
    "## 故事库 — 池中【故事】全量入此, 每条独立成段且带可识别条目信号: 【故事标题】+ "
    "背景/主体/决策与转折/结果/启示 五要素逐行 (与池同构, 只整理不重写不合并), "
    "保留年份/人物/代名作等全部具体物, 禁压缩成干条; "
    "**禁「关于X的案例」式分组散文** (分组=丢料, 质检按条目信号计数); 短案例可附在条目后\n"
    "## 方法与清单 — 池中【方法】全量入此: 方法名+步骤逐条(1.2.3.)+来源标注(书中明示/从案例归纳)+适用场景+边界(什么情况下不适用/依赖的前提 — cangjie B元素, 收尾反调直接料); "
    "若池无方法条但故事含可归纳步骤, 允许归纳并标注\n"
    "## 成功路径 — 池中【路径】串成全书主线的台阶(阶段→触发→动作→结果, 一阶一阶往上), "
    "这是讲书'上台阶'叙事的骨架\n"
    "## 反模式与坑 — 池中【反模式】全量入此: 坑名+什么情境+错误做法+后果+书给的正解; 这是收尾反调/避坑段落的直接料\n"
    "## 可引用原句 (全量保留)\n"
    "## 一页核  — 最终只留: "
    "1 个核心命题 + 2~3 个支撑论据 + 1 个绝佳案例, 便签纸容量\n"
    "只整理编排, 严禁编造池外信息, 严禁丢弃池中信息点。直接输出稿体, 不要 JSON。"
)


_AUDIT_SYS = (
    "你是短视频平台合规审计器(抖音=关键词+语义上下文双重审核)。按【规则集】审计【提取池】,"
    "输出严格 JSON: {\"delete\":[str], \"rewrite\":[{\"orig\":str,\"dir\":str}], \"book_rules\":[str]}。"
    "delete=高风险须物理删除项(侮辱蔑称/历史悲剧阴谋论化/群体财富阴谋论/大段宗教教义/疗效描述),"
    "给池内原文片段(可子串匹配); rewrite=中风险可保含义须转述项, dir 给改写方向; "
    "book_rules=本书次要风险注意点(视角限定/禁堆砌/禁映射现实)。只输出命中项, 宁严勿漏, 不凑数。"
)


def load_book_rules() -> dict:
    p = Path(__file__).resolve().parents[3] / "config" / "compliance_rules_book.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[distill] 规则集加载失败: %s", exc)
        return {}


_AUDIT_CHUNK = 30000   # 分块审计段大小 (字符)
_AUDIT_TIMEOUT = 180   # 小包超时: 旧 600s 是为单大包排队设的, 分块后不该等


def audit_compliance(pool: dict, cb=None) -> dict:
    """Gate A : pro 模型按 compliance_rules_book.json 审计提取池 (0922 分块版).

    旧版单包 90k (≈5万token) 进网关排队, 保活连接拖死读超时且三级链逐家再等
    (llm_service docstring 实测: deepseek 过载单调用拖满 900s, 客户端超时拦不住)。
    0922 用户令改少喂多次: 池 JSON 切 ~30k 字/段, 单段 180s, 失败重试 1 次后跳过
    该段 — 失败粒度远细于旧版"整包失败=全放行"。段边界在 JSON 串上硬切, 旧版
    [:90000] 截断同理, 审计按条目命中不依赖整体结构。硬黑名单确定性层不受影响。
    返回 {delete, rewrite, book_rules}; 调用侧物理删除 delete 项。
    """
    from app.services.llm_service import get_llm_service
    full = json.dumps(pool, ensure_ascii=False)[:90000]
    if not full:
        return {"delete": [], "rewrite": [], "book_rules": []}
    rules = json.dumps(load_book_rules(), ensure_ascii=False)
    chunks = [full[i:i + _AUDIT_CHUNK] for i in range(0, len(full), _AUDIT_CHUNK)]
    llm = get_llm_service()
    delete: list[str] = []
    rewrite: list[dict] = []
    book_rules: list[str] = []
    for i, ck in enumerate(chunks, 1):
        if cb:
            cb(f"合规审计中 (云端 pro, 段 {i}/{len(chunks)})…")
        audit = {}
        for _attempt in range(2):  # 单段重试 1 次, 仍败跳过并记录
            try:
                out = llm.chat(_AUDIT_SYS,
                               f"【规则集】\n{rules}\n【提取池 第{i}/{len(chunks)}段 (JSON 可能截断)】\n{ck}",
                               model="pro", temperature=0.2, timeout=_AUDIT_TIMEOUT)
                audit = GemmaClient.parse_json_block(out)
                break
            except Exception as exc:
                logger.warning("[distill] 合规审计段 %d/%d 失败 (attempt %d, 跳过该段): %s",
                               i, len(chunks), _attempt + 1, exc)
        d = [str(x) for x in (audit.get("delete") or [])]
        r = [x for x in (audit.get("rewrite") or []) if isinstance(x, dict)]
        b = [str(x) for x in (audit.get("book_rules") or [])]
        if cb:
            cb(f"审计段 {i}/{len(chunks)} ✓: 删除{len(d)} 改写{len(r)} 注意{len(b)}")
        delete += d
        rewrite += r
        book_rules += b
    # 段间去重合并 (同条目多段命中/截断边界两侧重报)
    _seen: set[str] = set()
    rw: list[dict] = []
    for r in rewrite:
        k = str(r.get("orig") or "")[:60]
        if k and k in _seen:
            continue
        if k:
            _seen.add(k)
        rw.append(r)
    audit = {
        "delete": list(dict.fromkeys(delete))[:50],
        "rewrite": rw[:50],
        "book_rules": list(dict.fromkeys(book_rules))[:30],
    }
    if cb:
        cb(f"合规审计完成: 删除{len(audit['delete'])} 改写{len(audit['rewrite'])} 注意{len(audit['book_rules'])}")
    return audit


def _apply_delete(pool: dict, frags: list[str]) -> int:
    """物理删除: 池内任何文本字段含 frag 即移除 (高危内容不进管线)."""
    def hit(s: str) -> bool:
        return any(f and (f in s or s in f) for f in frags)
    n = 0
    for k in ("观点", "数据", "原句"):
        before = len(pool[k])
        pool[k] = [x for x in pool[k] if not hit(x)]
        n += before - len(pool[k])
    for k in ("概念", "案例"):
        before = len(pool[k])
        pool[k] = [x for x in pool[k]
                   if not hit(x.get("name", "") or x.get("desc", ""))]
        n += before - len(pool[k])
    return n


class GemmaClient:
    """llama-server OpenAI 兼容客户端 + 稳健 JSON 提取."""

    def __init__(self, url: str = GEMMA_URL, timeout: int = 560):
        self.url = url
        self.timeout = timeout

    def healthy(self) -> bool:
        try:
            return requests.get(f"{self.url}/v1/models", timeout=5).status_code == 200
        except Exception:
            return False

    def chat(self, user: str, system: str = _EXTRACT_SYS) -> str:
        r = requests.post(f"{self.url}/v1/chat/completions",
                          json={"model": GEMMA_MODEL, "temperature": 0.2,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    @staticmethod
    def parse_json_block(text: str) -> dict:
        """取首个平衡花括号块 (容忍 ```json 围栏与尾部杂质)."""
        start = text.find("{")
        if start < 0:
            return {}
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return {}
        return {}


# ── 生命周期 ─────────────────────────────────────────────────────
def ensure_gemma(client: GemmaClient | None = None) -> tuple[GemmaClient, bool]:
    """端口活 → 复用 (started=False); 否则自启 llama-server 等健康 (最长 600s)."""
    client = client or GemmaClient()
    if client.healthy():
        return client, False
    logger.info("[distill] 自启 llama-server …")
    proc = subprocess.Popen([_LLAMA_BIN, *_LLAMA_ARGS], cwd=_LLAMA_CWD,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(120):
        if client.healthy():
            return client, True
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server 启动失败 exit={proc.poll()}")
        time.sleep(5)
    proc.kill()
    raise RuntimeError("llama-server 600s 未健康")


def _llama_pids() -> list[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        parts = line.replace('"', "").split(",")
        if len(parts) >= 2 and parts[1].strip().isdigit():
            pids.append(int(parts[1]))
    return pids


def shutdown_gemma_all(own_pid: int | None = None) -> list[int]:
    """全杀腾卡 (用户授权): llama-server.exe 全实例按 PID 精确 kill."""
    pids = _llama_pids()
    if own_pid and own_pid not in pids:
        pids.append(own_pid)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    if pids:
        logger.info("[distill] 已释放 VRAM: kill pids %s", pids)
    return pids


# ── 蒸馏 ─────────────────────────────────────────────────────────
def _dedupe(items: list[str], seen: set[str]) -> list[str]:
    out = []
    for it in items:
        it = (it or "").strip()
        if not it or it in seen or any(it and it in s for s in seen):
            continue
        seen.add(it)
        out.append(it)
    return out


def _clean_latex(s: str) -> str:
    """模型 LaTeX 习惯清洗: $\\rightarrow$ 等 → 箭头, 去残留 $ / \\text{} (原句除外调用侧控制)."""
    s = re.sub(r"\$?\\(rightarrow|Rightarrow|longrightarrow|to|mapsto)\$?", "→", s)
    s = re.sub(r"\$?\\(leftarrow|Leftarrow)\$?", "←", s)
    s = re.sub(r"\\(text|mathrm|emph)\{([^}]*)\}", r"\2", s)
    return s.replace("$", "").replace("\\", "")


def _chunk_text(text: str, size: int = 12000) -> list[str]:
    """句界分块 (不截断): 保证全书每字都被提取."""
    chunks, cur = [], ""
    for sent in re.split(r"(?<=[。！？\n])", text):
        if len(cur) + len(sent) > size and cur:
            chunks.append(cur)
            cur = ""
        cur += sent
    if cur.strip():
        chunks.append(cur)
    return chunks


_BATCH_MAX = 15000   # 碎节合并包上限 (0922 用户令·A案); 单块 40k 军规不变


def _pack_secs(secs: list[tuple[str, str]]) -> dict:
    """节列表 → 一个提取包: 逐节标记拼接 (零截断), name 兼容旧 chunks.json 结构."""
    body = "\n\n".join(f"〔第{i}节·{n}〕\n{t}" for i, (n, t) in enumerate(secs, 1))
    name = secs[0][0] if len(secs) == 1 else f"{secs[0][0]} 等{len(secs)}节"
    return {"name": name, "text": body}


def build_batches(chapters: list[dict]) -> list[dict]:
    """章节 → 提取包 (0922 碎节合并, 用户令 A 案).

    v2 军规 (0908) 不变: 单块 ≤40k 零截断, 超长章 _chunk_text 句界细分。
    0922 动机: zhihailib 版《定位》133 节均 913 字, 逐节单调 = 每节付一次全价
    结构化生成 (8 类目脚手架+系统提示+质检重试), 133 次×75s = 2h47m; 相邻微节
    合并 ~15k/包后包内逐节标记 (章节语境零丢失), 调用次数 133 → ~9。
    """
    secs: list[tuple[str, str]] = []
    for ch in chapters:
        if len(ch["text"]) < 500:
            continue
        parts = [ch["text"]] if len(ch["text"]) <= 40000 else _chunk_text(ch["text"], 40000)
        secs += [(ch["name"], p) for p in parts]
    packs: list[dict] = []
    cur: list[tuple[str, str]] = []
    cur_len = 0
    for name, text in secs:
        if cur and cur_len + len(text) > _BATCH_MAX:
            packs.append(_pack_secs(cur))
            cur, cur_len = [], 0
        cur.append((name, text))
        cur_len += len(text)
    if cur:
        packs.append(_pack_secs(cur))
    return packs


def _merge_pool(pool: dict, data: dict, seen: set) -> None:
    """v2 深提取合并: 概念{name,mech}/案例{desc,point} 结构化去重."""
    for v in data.get("观点") or []:
        v = _clean_latex((v or "").strip())
        if v and v not in seen:
            seen.add(v); pool["观点"].append(v)
    for c in data.get("概念") or []:
        if isinstance(c, dict) and (c.get("name") or "").strip():
            key = _clean_latex(c["name"].strip())
            if key not in seen:
                seen.add(key); pool["概念"].append({"name": key, "mech": _clean_latex((c.get("mech") or "").strip())})
        elif isinstance(c, str) and c.strip() and c not in seen:
            seen.add(c); pool["概念"].append({"name": c.strip(), "mech": ""})
    for c in data.get("案例") or []:
        if isinstance(c, dict) and (c.get("desc") or "").strip():
            key = c["desc"].strip()[:40]
            if key not in seen:
                seen.add(key); pool["案例"].append({"desc": _clean_latex((c.get("desc") or "").strip()), "point": _clean_latex((c.get("point") or "").strip())})
        elif isinstance(c, str) and c.strip() and c not in seen:
            seen.add(c); pool["案例"].append({"desc": c.strip(), "point": ""})
    for s in data.get("故事") or []:
        if isinstance(s, dict) and (s.get("title") or s.get("决策与转折") or "").strip():
            key = _clean_latex(((s.get("title") or "") + (s.get("决策与转折") or ""))[:40])
            if key and key not in seen:
                seen.add(key)
                pool["故事"].append({k: _clean_latex(str(s.get(k) or "")) for k in
                                     ("title", "背景", "主体", "决策与转折", "结果", "启示")})
    for m in data.get("方法") or []:
        if isinstance(m, dict) and (m.get("name") or "").strip():
            key = _clean_latex(m["name"].strip())
            if key not in seen:
                seen.add(key)
                steps = [str(x) for x in (m.get("步骤") or []) if str(x).strip()]
                pool["方法"].append({"name": key, "步骤": steps,
                                     "来源": str(m.get("来源") or ""), "适用": _clean_latex(str(m.get("适用") or "")),
                                     "边界": _clean_latex(str(m.get("边界") or ""))})
    for st in data.get("路径") or []:
        if isinstance(st, dict) and (st.get("阶段") or st.get("动作") or "").strip():
            key = _clean_latex(((st.get("阶段") or "") + (st.get("动作") or ""))[:40])
            if key and key not in seen:
                seen.add(key)
                pool["路径"].append({k: _clean_latex(str(st.get(k) or "")) for k in
                                     ("阶段", "触发", "动作", "结果")})
    for d in data.get("数据") or []:
        d = _clean_latex((d or "").strip())
        if d and d not in seen:
            seen.add(d); pool["数据"].append(d)
    for q in data.get("原句") or []:
        q = (q or "").strip()
        if q and q not in seen:
            seen.add(q); pool["原句"].append(q)


def split_sections(essence_text: str, book_title: str) -> dict:
    """蒸馏产物按 `## ` 节拆独立文件 → data/l0/{书}/sections/ 料仓 .

    下游按需给料 (金句给金句文件/素材包给故事库), 不再整 txt 灌注。
    节名做文件名 (安全字符); 返回 {节名: 字数}。
    """
    out_dir = Path(__file__).resolve().parents[3] / "data" / "l0" / book_title / "sections"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = re.split(r"(?m)^## ", essence_text or "")
    stats: dict[str, int] = {}
    for part in parts[1:]:
        lines = part.split("\n", 1)
        title_raw = lines[0].strip()
        fname = re.sub(r'[\\/:*?"<>|\s]', "_", title_raw)[:40] or "未命名"
        body = lines[1] if len(lines) > 1 else ""
        (out_dir / f"{fname}.txt").write_text(f"## {title_raw}\n{body}", encoding="utf-8")
        stats[title_raw[:20]] = len(body)
    if parts and parts[0].strip():
        (out_dir / "_头部.txt").write_text(parts[0], encoding="utf-8")
    return stats


def _post_verify(book_title: str, cb=None) -> dict:
    """1005 蒸馏后验双闸 (确定性, 用户令: 重蒸必查以往 bug):
    ①原句逐字校验 — 「可引用原句」每条必须在原始语料 (pipe chunks) 逐字命中,
      未命中=释义冒充原句 (值得信赖 0929 A/B 实锤的新流程暗伤) → 剔除;
    ②蒸馏健康闸 — 核心层字数/故事/方法/原句条数下限 (定位 4k 字薄产无人拦的教训),
      不达标 warning (人闸可见, 不硬断)。
    报告落 sections/_蒸馏校验.json; 任何异常不破主流程。"""
    try:
        import json as _json
        base = Path(__file__).resolve().parents[3] / "data" / "l0" / book_title
        sec = base / "sections"
        rep: dict = {"book": book_title}

        def _norm(t: str) -> str:
            return re.sub(r"[\s「」『』“”‘’\"'·*•\-—、。；;：:，,]", "", t or "")

        # 语料 = pipe chunks 原文 (无 pipe 时退 l0 chapters)
        corpus = ""
        cj = _pipe_dir(book_title) / "chunks.json"
        if cj.exists():
            pj = _json.loads(cj.read_text(encoding="utf-8"))
            corpus = "".join(str(c.get("text") or "") for c in (pj.get("chunks") or []))
        elif (base / "chapters").is_dir():
            import json as _j2
            corpus = "".join(_j2.loads(p.read_text(encoding="utf-8")).get("text") or ""
                             for p in (base / "chapters").glob("*.json"))
        cn = _norm(corpus)

        def _cnt(fname: str) -> int:
            p = sec / fname
            if not p.exists():
                return 0
            t = p.read_text(encoding="utf-8")
            if fname == "故事库.txt":
                # 1005 三修: 故事计数用条目信号 (【标题】/背景：) — 分组散文/多故事合段
                # 下空行块数失真 (二轮 39故事计成7块的教训); 与 _check_compose 同口径
                return max(len(re.findall(r"【[^】]{2,24}】", t)),
                           len(re.findall(r"背景[：:]", t)))
            return len([x for x in re.split(r"\n\s*\n", t) if x.strip()])

        def _size(fname: str) -> int:
            p = sec / fname
            return len(p.read_text(encoding="utf-8")) if p.exists() else 0

        # ① 原句逐字校验
        qf = sec / "可引用原句.txt"
        if qf.exists() and cn:
            lines = qf.read_text(encoding="utf-8").splitlines()
            kept, dropped = [], []
            for l in lines:
                s = l.strip()
                if not s or s.startswith("#"):
                    kept.append(l)
                    continue
                core = _norm(s)
                if len(core) < 8 or core in cn:   # 太短无从校验, 保守保留
                    kept.append(l)
                else:
                    dropped.append(s)
            if dropped:
                qf.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
            n_kept = len([l for l in kept if l.strip() and not l.strip().startswith("#")])
            rep["quotes"] = {"kept": n_kept, "dropped": len(dropped),
                             "verbatim_rate": round(n_kept / max(n_kept + len(dropped), 1), 2)}
            if dropped and cb:
                cb(f"   原句校验: 剔除释义冒充 {len(dropped)} 条 (保留 {n_kept})")
        # ② 健康闸
        core_chars = sum(_size(f) for f in ("核心哲学.txt", "核心概念与机制.txt", "方法与清单.txt",
                                            "故事库.txt", "成功路径.txt", "反模式与坑.txt",
                                            "一页核.txt", "元问题.txt"))
        n_st, n_me = _cnt("故事库.txt"), _cnt("方法与清单.txt")
        n_q = rep.get("quotes", {}).get("kept", _cnt("可引用原句.txt"))
        floors = {"core_chars": (core_chars, 8000), "stories": (n_st, 10),
                  "methods": (n_me, 10), "quotes": (n_q, 30)}
        rep["health"] = {k: {"value": v, "floor": fl, "pass": v >= fl} for k, (v, fl) in floors.items()}
        fails = [k for k, d in rep["health"].items() if not d["pass"]]
        if fails and cb:
            cb(f"   健康闸 ⚠ 未达标: {', '.join(fails)} (细节见 sections/_蒸馏校验.json)")
        (sec / "_蒸馏校验.json").write_text(
            _json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        return rep
    except Exception as exc:  # 后验永不破主流程
        if cb:
            cb(f"   后验双闸异常(跳过): {exc}")
        return {}


def _check_extract(data: dict, chunk_len: int) -> list[str]:
    """v2 提取质检 : 确定性验 — JSON 结构/重类目要素/原句非空."""
    if not isinstance(data, dict) or not any(data.get(k) for k in
            ("观点", "概念", "故事", "案例", "方法", "路径", "反模式", "数据", "原句")):
        return ["全类目为空"]
    issues = []
    stories = [s for s in (data.get("故事") or []) if isinstance(s, dict)]
    if chunk_len > 8000 and not stories:
        issues.append("长章无故事")
    hollow = sum(1 for s in stories
                 if not (s.get("决策与转折") or "").strip() or not (s.get("结果") or "").strip())
    if stories and hollow / len(stories) > 0.5:
        issues.append(f"故事六要素空洞 {hollow}/{len(stories)}")
    if chunk_len > 8000 and not (data.get("原句") or []):
        issues.append("长章无原句")
    return issues


def _check_compose(essence: str, pool: dict) -> list[str]:
    """v2 编纂质检 : 节头齐备 + 故事不丢料 (计数比对 ≥池×0.7)."""
    need = ("元问题", "核心哲学", "概念与机制", "故事库", "方法与清单",
            "成功路径", "反模式", "可引用原句", "一页核")
    # 0908 修: 节头可带前缀 (如「核心概念与机制」) — 关键词含于任一 ## 行即算在
    issues = [f"缺节:{k}" for k in need
              if not re.search(rf"(?m)^##+ .{{0,6}}{k}", essence)]
    n_pool = len(pool.get("故事") or [])
    if n_pool:
        body = essence[essence.find("故事库"):essence.find("## ", essence.find("故事库") + 5)]
        n_out = len(re.findall(r"(?m)^(?:\*\s|\d+[.、]|[-•]\s|###+\s)|背景[：:]", body or ""))
        # 编纂合并同类故事, 0.7 容忍度; 条目信号弱时退存在性判断
        if "故事库" not in essence:
            issues.append("池有故事但无故事库节")
        elif n_pool >= 4 and n_out and n_out < n_pool * 0.7:
            issues.append(f"故事丢料 {n_out}/{n_pool}")
    if not re.search(r"## 一页核[\s\S]{20,}", essence):
        issues.append("一页核空")
    return issues


def load_section(book_title: str, section: str) -> str:
    """v2 料仓按需取料 : sections/{节名}.txt — 短名前缀匹配
    (如 '故事库'→'故事库 — 池中….txt'); 缺失返回空串 (调用方自行 fallback)."""
    d = Path(__file__).resolve().parents[3] / "data" / "l0" / book_title / "sections"
    if not d.is_dir():
        return ""
    exact = d / f"{section}.txt"
    if exact.exists():
        return exact.read_text(encoding="utf-8", errors="ignore")
    for f in d.glob("*.txt"):
        if f.stem.startswith(section) or section in f.stem[:12]:
            return f.read_text(encoding="utf-8", errors="ignore")
    return ""


def distill_book(path: str | Path, client: GemmaClient,
                 progress: dict | None = None, cb=None,
                 audit: bool = True) -> str:
    """单书蒸馏 v2 : 全文分块穷尽提取 + LLM 编纂轮.

    v1 三层信息丢失修复: ①截断6000→分块全读; ②裸串→结构化深提取;
    ③编纂硬上限→LLM 编纂轮全量整理(128k 上下文), 机械编纂仅兜底。
    """
    parsed = read_book(path)
    # v2 L0 覆盖率质检 (0908 设计令): 解析字数/包内文本总字数 <80% = 整书漏读, 拒跑
    if str(path).lower().endswith(".epub"):
        try:
            import zipfile as _zf
            _z = _zf.ZipFile(path)
            _zip_chars = sum(len(re.sub(r"<[^>]+>", "", _z.read(n).decode("utf-8", errors="ignore")))
                             for n in _z.namelist()
                             if n.lower().endswith((".html", ".xhtml", ".htm")))
            if _zip_chars and parsed["total_chars"] / _zip_chars < 0.8:
                raise RuntimeError(
                    f"epub 解析覆盖率 {100 * parsed['total_chars'] / _zip_chars:.0f}% <80% "
                    f"({parsed['total_chars']:,}/{_zip_chars:,}) — 整书漏读, 拒绝蒸馏")
            if cb:
                cb(f"解析覆盖率 {100 * parsed['total_chars'] / max(_zip_chars, 1):.0f}% ✓")
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("[distill] 覆盖率检查跳过: %s", exc)
    seen: set[str] = set()
    pool: dict = {"观点": [], "概念": [], "案例": [], "数据": [], "原句": [],
                  "故事": [], "方法": [], "路径": []}
    chunks = build_batches(parsed["chapters"])  # 0922 碎节合并 (~15k/包; ≤40k 军规不变)
    for ci, c in enumerate(chunks):
        cname, ck = c["name"], c["text"]
        if progress is not None:
            progress.update({"book": parsed["book_title"], "chapter": f"{ci + 1}/{len(chunks)}"})
        if cb:
            cb(f"块 {ci + 1}/{len(chunks)} ({cname}) 提取中…")
        data = None
        for _attempt in range(2):  # v2 提取质检 (0908): 不过重提 1 次
            data = client.parse_json_block(client.chat(f"【所在章节】{cname}\n【章节内容】{ck}"))
            qc_issues = _check_extract(data, len(ck))
            if not qc_issues:
                break
            if cb and _attempt == 0:
                cb(f"章质检不过({'; '.join(qc_issues[:2])}), 重提: {cname[:20]}")
        _merge_pool(pool, data or {}, seen)
    if cb:
        cb(f"提取完成: 观点{len(pool['观点'])} 概念{len(pool['概念'])} "
           f"案例{len(pool['案例'])} 数据{len(pool['数据'])} 原句{len(pool['原句'])}")
        distill_book._last_pool = pool  # 1005: 供 _run_single 池镜像落盘 (编纂可单独重试)

    # Gate A 合规审计 (成稿前): 高危物理删除, 改写方向进编纂轮.
    # audit=False = _raw 摸规则模式 (用户 2026-08-19): 保留全量信息送豆包测边界。
    audit_res = {"delete": [], "rewrite": [], "book_rules": []}
    deleted = 0
    if audit:
        # 硬黑名单 (豆包逐字清单, 确定性删除, 不依赖 LLM 命中)
        hard = load_book_rules().get("hard_blacklist_quotes") or []
        hard_n = _apply_delete(pool, hard)
        if hard_n and cb:
            cb(f"硬黑名单确定性删除 {hard_n} 条")
        audit_res = audit_compliance(pool, cb=cb)
        deleted = hard_n + _apply_delete(pool, audit_res["delete"])
        if cb:
            cb(f"高危物理删除共 {deleted} 条")

    # 编纂轮: 全池进 128k 上下文, LLM 编纂成规模式精华 (不编不丢)
    pool_text = json.dumps(pool, ensure_ascii=False)
    comp_sys = _COMPOSE_SYS
    if audit_res["rewrite"] or audit_res["book_rules"]:
        comp_sys += (
            "\n【合规改写方向(必须执行)】"
            + "; ".join(f"{(r.get('orig') or '')[:40]}→{r.get('dir') or ''}" for r in audit_res["rewrite"])
            + "\n【本书注意点】" + "; ".join(audit_res["book_rules"]))
    essence = None
    if pool_text:
        if cb:  # 编纂轮是本地长生成 (10-30min) 无中间进度, 先播报防"断档像死机"
            cb(f"编纂轮: 全池 {len(pool_text) // 1000}k 字入本地 Gemma 成文中, 预计 10-30min…")
        for _attempt in range(2):  # v2 编纂质检 (0908): 节不齐/丢料重编 1 次
            try:
                out = client.chat(f"书名: 《{parsed['book_title']}》\n【提取池】\n{pool_text[:90000]}",
                                  system=comp_sys)
                if len(out) < max(1500, len(pool_text) // 15):
                    break
                essence = _clean_latex(out)
                c_issues = _check_compose(essence, pool)
                if not c_issues:
                    break
                if cb:
                    cb(f"编纂质检不过({'; '.join(c_issues[:3])})"
                       + (", 重编" if _attempt == 0 else ", 放行带病"))
            except Exception as exc:
                logger.warning("[distill] 编纂轮失败, 回退机械编纂: %s", exc)
                break
    # 0925 修: ## 节头 <4 = 结构性废稿 (LLM 放行带病产物拆不了料仓) — 弃稿走
    # 机械编纂兜底, 不让 sections/ 只剩 _头部.txt 卡死樊登全书稿
    if essence and len(re.findall(r"(?m)^## ", essence)) < 4:
        if cb:
            cb("编纂稿 ## 节头不足, 弃稿回退机械编纂 (保料仓结构)")
        logger.warning("[distill] 编纂稿结构废 (## 节头<4), 回退机械编纂")
        essence = None
    if essence and (audit_res["rewrite"] or audit_res["book_rules"] or deleted):
        essence += (
            "\n\n## 合规备注 (Gate A 审计, 成稿前必遵)\n"
            + "".join(f"- 改写: {(r.get('orig') or '')[:50]} → {r.get('dir') or ''}\n" for r in audit_res["rewrite"])
            + "".join(f"- 注意: {r}\n" for r in audit_res["book_rules"])
            + f"- 已物理删除高危 {deleted} 条, 成稿禁用其原文\n")
    # Gate 0.5 (0908): 合规映射结构化落 L0 目录 — 下游确定性过滤的机器可读源
    # (文本备注是给人看的, JSON 是给 compliance_gate 吃的; rewrite 的 dir 是方向,
    #  safe 由过滤层从 dir 引号抽取/后续蒸馏升级直接产逐字安全版)
    try:
        import json as _json

        from .l0 import _l0_dir as _d
        _map = {"rules": [{"orig": r.get("orig") or "", "safe": r.get("safe") or "",
                           "note": r.get("dir") or ""} for r in audit_res["rewrite"]],
                "hard_notes": list(audit_res["book_rules"] or []),
                "deleted_count": int(deleted)}
        _p = _d(parsed["book_title"]) / "合规映射.json"
        _p.parent.mkdir(parents=True, exist_ok=True)
        _p.write_text(_json.dumps(_map, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        logger.warning("[distill] 合规映射 JSON 落盘失败 (Gate0.5 回退文本解析): %s", exc)
    final = essence or _compose_essence_fallback(parsed["book_title"], pool)
    # sections/ 料仓 (0908): 按节拆文件, 下游按需调用
    try:
        stats = split_sections(final, parsed["book_title"])
        _post_verify(parsed["book_title"], cb)
        if cb:
            cb(f"sections 料仓落盘: {len(stats)} 节")
    except Exception as exc:
        logger.warning("[distill] sections 拆分失败(不阻断): %s", exc)
    return final


def _compose_essence_fallback(title: str, p: dict) -> str:
    """机械编纂兜底 (0925 修): `## ` 规范节头 + 全池覆盖.

    旧版两洞 (卓有成效/赢 实锤: 料仓仅 _头部, 樊登全书稿 0 字):
    ①裸行节头 split_sections 拆不了 → sections 料仓空;
    ②漏渲 故事/方法/路径 三类池料 → 樊登喂料 (_full_material 六节) 缺大腿.
    节名对齐 _full_material/料仓消费方: 概念与机制/故事库/方法与清单/成功路径.
    """
    parts = [f"# 《{title}》蒸馏精华\n"]

    parts.append("\n## 核心哲学\n")
    parts += [f"- {v}\n" for v in p["观点"]] or ["- （无）\n"]

    parts.append("\n## 概念与机制\n")
    for i, c in enumerate(p["概念"], 1):
        parts.append(f"{i}. {c['name']}\n")
        if c["mech"]:
            parts.append(f"   核心机制: {c['mech']}\n")
        rel = [x for x in p["案例"] if c["name"][:4] in x["desc"] or c["name"][:4] in x["point"]]
        for r in rel[:2]:
            parts.append(f"   案例: {r['desc']}" + (f"（{r['point']}）" if r["point"] else "") + "\n")
        rel_d = [d for d in p["数据"] if c["name"][:4] in d]
        for d in rel_d[:2]:
            parts.append(f"   数据: {d}\n")
    if not p["概念"]:
        parts.append("- （无）\n")

    parts.append("\n## 故事库\n")
    for s in p["故事"]:
        head = f"《{s['title']}》" if s["title"] else "故事"
        seg = " ".join(x for x in (s["背景"], s["主体"], s["决策与转折"], s["结果"], s["启示"]) if x)
        parts.append(f"- {head} {seg}\n")
    if not p["故事"]:
        parts.append("- （无）\n")

    parts.append("\n## 方法与清单\n")
    for m in p["方法"]:
        parts.append(f"- {m['name']}" + (f"（来源: {m['来源']}）" if m["来源"] else "") + "\n")
        for st in m["步骤"]:
            parts.append(f"  {st}\n")
        if m["适用"]:
            parts.append(f"  适用: {m['适用']}\n")
        if m["边界"]:
            parts.append(f"  边界: {m['边界']}\n")
    if not p["方法"]:
        parts.append("- （无）\n")

    parts.append("\n## 成功路径\n")
    for st in p["路径"]:
        seg = " → ".join(x for x in (st["阶段"], st["触发"], st["动作"], st["结果"]) if x)
        parts.append(f"- {seg}\n")
    if not p["路径"]:
        parts.append("- （无）\n")

    parts.append("\n## 案例库\n")
    parts += [f"- {x['desc']}" + (f"（{x['point']}）" if x["point"] else "") + "\n"
              for x in p["案例"]] or ["- （无）\n"]

    parts.append("\n## 数据\n")
    parts += [f"- {d}\n" for d in p["数据"]] or ["- （无）\n"]

    parts.append("\n## 可引用原句\n")
    parts += [f"- {q}\n" for q in p["原句"]] or ["- （无）\n"]
    return "".join(parts)


def pending_distill_books(sources: list[dict]) -> list[dict]:
    """需蒸馏者: epub 全书, 或 >2万字 txt; 且同名 .蒸馏.txt 尚不存在."""
    def _base(fn: str) -> str:
        return clean_book_title(fn.rsplit(".", 1)[0].replace(DISTILL_SUFFIX, ""))
    have = {_base(s["filename"]) for s in sources if DISTILL_SUFFIX in s["filename"]}
    out = []
    for s in sources:
        if DISTILL_SUFFIX in s["filename"]:
            continue
        if s["ext"] == "epub" or (s["ext"] in ("txt", "md") and s["size"] > _FULL_TEXT_MIN * 3):
            if _base(s["filename"]) not in have:
                out.append(s)
    return out


# ── 后台批次 (线程 + 状态轮询) ───────────────────────────────────
import threading

_BATCH: dict = {"running": False, "progress": {}, "done": [], "error": None, "events": []}


def _evt(msg: str, level: str = "info", **extra) -> None:
    """关键节点事件: 环形缓冲 (轮询兜底) + SSE 总线 (channel=distill)."""
    from app.services.director_events import publish
    e = {"ts": time.strftime("%H:%M:%S"), "level": level, "msg": msg, **extra}
    _BATCH["events"].append(e)
    if len(_BATCH["events"]) > 300:
        _BATCH["events"] = _BATCH["events"][-300:]
    publish("distill", e)


def batch_status() -> dict:
    return _BATCH


def clear_state() -> dict:
    """清空蒸馏状态/事件缓冲 : 前端确认完成后调用, 恢复正常页面."""
    if _BATCH["running"]:
        raise RuntimeError("蒸馏运行中, 不能清空")
    _BATCH.update(running=False, done=[], error=None, progress={}, events=[])
    return _BATCH


def start_batch(root: str) -> dict:
    if _BATCH["running"]:
        raise RuntimeError("蒸馏批次已在运行")
    _BATCH.update(running=True, done=[], error=None, progress={}, events=[])
    threading.Thread(target=_run_batch, args=(root,), daemon=True).start()
    return _BATCH


# ── 单本蒸馏 (2026-08-20): 复用批量线程/状态/SSE 模式, 与批量互斥 ──
def start_single(path: str | Path, root: str) -> dict:
    """单本书蒸馏: 拉起本地 Gemma → 蒸馏 → 写 .蒸馏.txt → 腾卡."""
    if _BATCH["running"]:
        raise RuntimeError("已有蒸馏任务在运行")
    _BATCH.update(running=True, done=[], error=None, progress={}, events=[])
    threading.Thread(target=_run_single, args=(path, root), daemon=True).start()
    return _BATCH


def _auto_l0_facing(src: Path, title: str) -> None:
    """蒸馏产物落盘后自动补 L0+facing .

    L0/facing 均复用本地 Gemma (l0/facing 直接 import distiller.GemmaClient) —
    批内 llama 已载入, 蒸馏完顺跑再腾卡, 零 API 费零额外加载;
    幂等 (已有 L0/某 facing 则跳过); 失败只警示 — 建书 prep 机制仍可兜底重试,
    绝不判蒸馏失败。此前 L0 是建书时才补 (books._prepare_l0_facing), 每本新书
    建书都要等一轮 — 流程割裂, 用户实锤 "为什么蒸馏完还要补 L0"。
    """
    try:
        from app.services.book_service.facing import FACINGS, run_facings
        from app.services.book_service.l0 import _l0_dir, run_l0
        d = _l0_dir(title)
        if not (d / "l0-chapter-v1.json").exists():
            _evt(f"《{title}》 L0 章节提取中 (本地Gemma)…")
            run_l0(src)
        missing = [n for n, spec in FACINGS.items()
                   if not (d / "facing" / spec["file"]).exists()]
        if missing:
            _evt(f"《{title}》 facing 补跑: {','.join(missing)}…")
            run_facings(d, facings=missing)
        _evt(f"✓ 《{title}》 L0/facing 就绪, 建书免等待", "ok")
    except Exception as exc:
        logger.warning("[distill] %s L0/facing 自动补跑失败 (建书时 prep 兜底): %s", title, exc)
        _evt(f"《{title}》 L0/facing 补跑失败, 建书时将自动重试", "warn")


def _run_single(path: str | Path, root: str) -> None:
    p = Path(path)
    try:
        title = clean_book_title(p.stem)
        _evt(f"▶ 《{title}》 单本蒸馏开始", "ok")
        client, started = ensure_gemma()
        _evt("llama-server " + ("自启完成 (17G 载入)" if started else "复用已运行实例"), "ok")
        t0 = time.monotonic()
        essence = distill_book(
            p, client, _BATCH["progress"],
            cb=lambda m: _evt(f"《{title}》 {m}"))
        out = Path(root) / f"{clean_book_title(Path(p.name).stem)}{DISTILL_SUFFIX}.txt"
        out.write_text(essence, encoding="utf-8")
        # 1005 治本: 单发流程把产物镜像到分段 pipe (chunks/pool) — 编纂可单独重试
        # (单发池纯内存不落盘 = 编纂翻车就得整本重提, 实锤于卓有成效治薄重跑)
        try:
            _pd = _pipe_dir(title)
            parsed = read_book(p)
            _chunks = build_batches(parsed["chapters"])
            _pd.joinpath("chunks.json").write_text(json.dumps(
                {"meta": {"book_title": title, "total_chars": parsed["total_chars"],
                          "n_chapters": len(parsed["chapters"]), "n_chunks": len(_chunks),
                          "done": len(_chunks)}, "chunks": _chunks}, ensure_ascii=False),
                encoding="utf-8")
            _pd.joinpath("pool.json").write_text(json.dumps(
                {"done": len(_chunks), "pool": getattr(distill_book, "_last_pool", {}) or {},
                 "seen": []}, ensure_ascii=False), encoding="utf-8")
        except Exception as _exc:
            logger.warning("[distill] 单发池镜像落盘失败(不影响产物): %s", _exc)
        _BATCH["done"].append(out.name)
        _evt(f"✓ 《{title}》 → {out.name} ({len(essence)}字, {time.monotonic()-t0:.0f}s)", "ok",
             type="distill_done")
        # 0907: 蒸馏完成即全就绪 — L0+facing 自动接续, 建书免等待
        _auto_l0_facing(p, title)
    except Exception as exc:
        logger.exception("[distill] 单本蒸馏失败: %s", p.name)
        _BATCH["error"] = str(exc)
        _BATCH["done"].append(f"{p.name} FAILED: {exc}")
        _evt(f"✗ 《{p.stem}》 蒸馏失败: {exc}", "error", type="distill_error")
    finally:
        try:
            killed = shutdown_gemma_all()  # 与批量一致: 结束全杀腾卡
            if killed:
                _evt(f"已释放显存 (kill {killed})", "ok")
        except Exception:
            pass
        _BATCH["running"] = False


def _run_batch(root: str) -> None:
    from app.services.book_service.reader import scan_book_sources
    try:
        pend = pending_distill_books(scan_book_sources(root))
        _BATCH["progress"] = {"total": len(pend), "book": "", "chapter": ""}
        if not pend:
            _evt("无待蒸馏任务 (全书均已有精华)", "warn")
            _publish_done()
            return
        _evt(f"批量蒸馏启动: {len(pend)} 本待处理", "ok")
        client, started = ensure_gemma()
        _evt("llama-server " + ("自启完成 (17G 载入)" if started else "复用已运行实例"), "ok")
        for i, s in enumerate(pend, 1):
            title = s["book_title"]
            t0 = time.monotonic()
            _evt(f"▶ {i}/{len(pend)} 《{title}》 开始蒸馏")
            try:
                essence = distill_book(
                    s["path"], client, _BATCH["progress"],
                    cb=lambda m: _evt(f"《{title}》 {m}"))
                out = Path(root) / f"{clean_book_title(Path(s['filename']).stem)}{DISTILL_SUFFIX}.txt"
                out.write_text(essence, encoding="utf-8")
                _BATCH["done"].append(out.name)
                _evt(f"✓ {i}/{len(pend)} 《{title}》 → {out.name} ({len(essence)}字, {time.monotonic()-t0:.0f}s)", "ok")
                # 0907: 蒸馏完成即全就绪 — L0+facing 自动接续 (DeepSeek, 不占本地卡)
                _auto_l0_facing(Path(s["path"]), title)
            except Exception as exc:
                logger.exception("[distill] 失败: %s", s["filename"])
                _BATCH["done"].append(f"{s['filename']} FAILED: {exc}")
                _evt(f"✗ 《{title}》 蒸馏失败: {exc}", "error")
        killed = shutdown_gemma_all()  # 用户决策: 批次结束全杀腾卡
        _evt(f"批次结束: 成功 {len([d for d in _BATCH['done'] if 'FAILED' not in d])}/{len(pend)}; "
             f"kill pids {killed}, 显存已释放", "ok", type="distill_done")
    except Exception as exc:
        logger.exception("[distill] 批次异常")
        _BATCH["error"] = str(exc)
        _evt(f"批次异常: {exc}", "error", type="distill_error")
    finally:
        _BATCH["running"] = False


def _publish_done() -> None:
    _evt("批次结束", "info", type="distill_done")


# ══════════════════════════════════════════════════════════════════
# v2 阶段化执行 (0908 用户令: 分开跑/逐个验证/断点续跑)
#   ① stage_parse    解析+分块 (秒)       → _pipeline/chunks.json
#   ② stage_extract  逐章提取 (每章增量写盘, from_chapter 续跑) → _pipeline/pool.json
#   ③ stage_gate_a   池合规审计 (分钟)     → 池内删 + 合规映射.json
#   ④ stage_compose  编纂+质检+产物 (分钟) → 蒸馏txt + sections/
# 验证序 (先小后大): ①秒 → ②单章 → ②小批+续跑 → ③④小池验机制 → ②全量夜跑 → ④真池
# ══════════════════════════════════════════════════════════════════

def _pipe_dir(book_title: str) -> Path:
    d = Path(__file__).resolve().parents[3] / "data" / "l0" / book_title / "_pipeline"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_parse(path: str | Path, cb=None) -> dict:
    """阶段①: 解析+分块+覆盖率闸门 → chunks.json。秒级, 跑完人工验收章节清单."""
    parsed = read_book(path)
    # 覆盖率闸门 (epub)
    if str(path).lower().endswith(".epub"):
        import zipfile as _zf
        _z = _zf.ZipFile(path)
        _zip_chars = sum(len(re.sub(r"<[^>]+>", "", _z.read(n).decode("utf-8", errors="ignore")))
                         for n in _z.namelist()
                         if n.lower().endswith((".html", ".xhtml", ".htm")))
        if _zip_chars and parsed["total_chars"] / _zip_chars < 0.8:
            raise RuntimeError(
                f"epub 解析覆盖率 {100 * parsed['total_chars'] / _zip_chars:.0f}% <80% — 整书漏读, 拒绝")
        if cb:
            cb(f"解析覆盖率 {100 * parsed['total_chars'] / max(_zip_chars, 1):.0f}% ✓")
    chunks = build_batches(parsed["chapters"])  # 0922 碎节合并: 与 distill_book 同一刀
    meta = {"book_title": parsed["book_title"], "total_chars": parsed["total_chars"],
            "n_chapters": len(parsed["chapters"]), "n_chunks": len(chunks),
            "done": 0}
    d = _pipe_dir(parsed["book_title"])
    (d / "source_path.txt").write_text(str(Path(path).resolve()), encoding="utf-8")
    (d / "chunks.json").write_text(
        json.dumps({"meta": meta, "chunks": chunks}, ensure_ascii=False), encoding="utf-8")
    if cb:
        cb(f"① 完成: {meta['n_chapters']} 章 → {meta['n_chunks']} 块, 总字 {meta['total_chars']:,}")
    return meta


def _load_pipe(book_title: str) -> tuple[dict, list[dict], dict, set]:
    d = _pipe_dir(book_title)
    pj = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
    pool = {"观点": [], "概念": [], "案例": [], "数据": [], "原句": [],
            "故事": [], "方法": [], "路径": [], "反模式": []}
    seen: set[str] = set()
    pf = d / "pool.json"
    if pf.exists():
        saved = json.loads(pf.read_text(encoding="utf-8"))
        pool = saved.get("pool") or pool
        seen = set(saved.get("seen") or [])
        pj["meta"]["done"] = saved.get("done", 0)
    return pj["meta"], pj["chunks"], pool, seen


def stage_extract(book_title: str, client: "GemmaClient | None" = None,
                  limit: int = 0, cb=None) -> dict:
    """阶段②: 逐章提取, 每章完成即增量写盘 (断点安全); limit=N 验证用只提 N 章."""
    if client is None:
        client, _started = ensure_gemma(GemmaClient)
    meta, chunks, pool, seen = _load_pipe(book_title)
    start = int(meta.get("done") or 0)
    end = len(chunks) if not limit else min(len(chunks), start + limit)
    for ci in range(start, end):
        c = chunks[ci]
        if cb:
            cb(f"② 提取 {ci + 1}/{len(chunks)} ({c['name'][:24]})")
        data = None
        for _attempt in range(2):
            data = client.parse_json_block(
                client.chat(wrap_source(f"【所在章节】{c['name']}\n【章节内容】{c['text']}", label="书籍原文")))
            if not _check_extract(data, len(c["text"])):
                break
            if cb and _attempt == 0:
                cb(f"   章质检不过, 重提: {c['name'][:20]}")
        _merge_pool(pool, data or {}, seen)
        # 增量落盘 (每章) — 进程死不丢已提章节
        _pipe_dir(book_title).joinpath("pool.json").write_text(
            json.dumps({"done": ci + 1, "pool": pool, "seen": sorted(seen)},
                       ensure_ascii=False), encoding="utf-8")
    stats = {k: len(v) for k, v in pool.items()}
    if cb:
        cb(f"② 完成: 提取至 {end}/{len(chunks)} 章 | " +
           " ".join(f"{k}{v}" for k, v in stats.items()))
    return {"done": end, "total": len(chunks), "stats": stats}


def stage_gate_a(book_title: str, cb=None) -> dict:
    """阶段③: 池合规审计 — 高危物理删 + 合规映射.json 落盘."""
    meta, chunks, pool, seen = _load_pipe(book_title)
    rules = load_book_rules
    hard = rules.get("hard_blacklist_quotes") or []
    hard_n = _apply_delete(pool, hard)
    audit_res = audit_compliance(pool, cb=cb)
    deleted = hard_n + _apply_delete(pool, audit_res["delete"])
    _map = {"rules": [{"orig": r.get("orig") or "", "safe": r.get("safe") or "",
                       "note": r.get("dir") or ""} for r in audit_res["rewrite"]],
            "hard_notes": list(audit_res["book_rules"] or []),
            "deleted_count": int(deleted)}
    d = _pipe_dir(book_title)
    d.joinpath("pool.json").write_text(
        json.dumps({"done": meta.get("done", 0), "pool": pool, "seen": sorted(seen)},
                   ensure_ascii=False), encoding="utf-8")
    d.parent.joinpath("合规映射.json").write_text(
        json.dumps(_map, ensure_ascii=False, indent=1), encoding="utf-8")
    if cb:
        cb(f"③ 完成: 硬删{hard_n}+审计删{deleted - hard_n}, 改写{len(audit_res['rewrite'])}, 注意{len(audit_res['book_rules'])}")
    return {"deleted": deleted, "rewrite": len(audit_res["rewrite"])}


def stage_compose(book_title: str, client: "GemmaClient | None" = None, cb=None) -> str:
    """阶段④: 编纂(质检重试1次) → 蒸馏txt + 合规备注 + sections/ 料仓."""
    if client is None:
        client, _started = ensure_gemma(GemmaClient)
    meta, chunks, pool, seen = _load_pipe(book_title)
    pool_text = json.dumps(pool, ensure_ascii=False)
    if not pool_text or pool_text == "{}":
        raise RuntimeError("池为空, 先跑 stage_extract")
    essence = None
    for _attempt in range(2):
        out = client.chat(wrap_source(f"书名: 《{book_title}》\n【提取池】\n{pool_text[:90000]}", label="书籍提取池"),
                          system=_COMPOSE_SYS)
        if len(out) < max(1500, len(pool_text) // 15):
            break
        essence = _clean_latex(out)
        c_issues = _check_compose(essence, pool)
        if not c_issues:
            break
        if cb:
            cb(f"   编纂质检: {c_issues[:3]}" + (" 重编" if _attempt == 0 else " 放行"))
    # 0925 修: 同 distill_book — ## 节头 <4 弃稿, 机械兜底保料仓结构
    if essence and len(re.findall(r"(?m)^## ", essence)) < 4:
        if cb:
            cb("   编纂稿 ## 节头不足, 弃稿回退机械编纂")
        essence = None
    if not essence:
        essence = _compose_essence_fallback(book_title, pool)
    if "合规映射.json" and True:
        _m = json.loads((_pipe_dir(book_title).parent / "合规映射.json").read_text(encoding="utf-8")) \
            if (_pipe_dir(book_title).parent / "合规映射.json").exists() else {"rules": [], "hard_notes": []}
        essence += ("\n\n## 合规备注 (Gate A 审计, 成稿前必遵)\n"
                    + "".join(f"- 改写: {r.get('orig', '')[:50]} → {r.get('note', '')}\n"
                              for r in _m.get("rules") or [])
                    + "".join(f"- 注意: {h}\n" for h in _m.get("hard_notes") or []))
    out_path = _pipe_dir(book_title).parent / f"{book_title}.蒸馏.txt"  # L0 正本
    out_path.write_text(essence, encoding="utf-8")
    # 双写源书目录 (0908 用户习惯位): chunks.json meta 里没存源路径, 从 _pipeline/归档参数取
    src_hint = _pipe_dir(book_title) / "source_path.txt"
    if src_hint.exists():
        import shutil as _sh
        _src = Path(src_hint.read_text(encoding="utf-8").strip())
        if _src.parent.is_dir():
            _sh.copy2(out_path, _src.parent / f"{book_title}.蒸馏.txt")
    stats = split_sections(essence, book_title)
    _post_verify(book_title, cb)
    if cb:
        cb(f"④ 完成: {len(essence):,}字 → {out_path.name} + sections {len(stats)} 节")
    return str(out_path)
