"""风格选择板 (0922 用户令) — 书级动画风格选型: 真内容试镜 + 库轮询 + Kimi 新配方提案.

决策链: 总纲完成 → [讲书页·🎨 风格选型] → ep1 钩子段切 3 试镜镜 →
风格库全员 + Kimi 提案新配方, 样张**全走本地 K2** (选中即可产 — Kimi 只出配方文本,
防"样张≠产线能力": 云端画的风格本地 LoRA/模型链复现不出) →
选择板 (镜头×风格 网格) → 点选落绑定 (选新风格自动入库: json + 参考图)。
开工硬挡板见 anim_service.start_phase (无绑定拒跑)。

公平性: 同镜同种子 (只比风格不赌种子); 渲染串行占 GPU 锁 (与 k2/h3 批互斥)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import comfy
from .config import _safe_name, load, main_color_tint  # 1016 W1 主色尾单源 (style_board 漏挂致试渲必炸 NameError, 1026 补)
from .k2 import bible_negative, build_workflow, render_gated

logger = logging.getLogger(__name__)

_LORA_DIR = Path(r"E:/AI/ComfyUI_windows_portable/ComfyUI/models/loras/Krea2-风格")
_BOARD_SEED = 20260922  # 试镜固定种子基 (同镜同种子跨风格公平比)

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

_SHOTS_SYS = (
    "你是动画试镜规划器。给定书的第一集总纲与灵魂三问, 规划 3 个试镜画面 (供动画风格选型用): "
    "①钩子镜 — 第一集最强情绪的画面化 ②人物镜 — 贯穿人物在关键处境中的一个定格 "
    "③概念镜 — 核心概念具象化成的场景。三镜画面互不重复、各代表一种镜头类型。"
    "**红线: 全链卡通大品类 — 画面主体 (含人物) 一律以卡通/动画形象呈现, 禁真人写实、禁照片感**。"
    "**丰富度规格 (0922 用户令, 全书出图以此为基准)**: 画面句必须高元素密度 — "
    "主体+配角/道具+环境细节+光线层次都要落字; 人物**必须露脸带表情** (禁无脸/背影/剪影), "
    "**服饰细节到位且能区分人物关系** (身份/阵营/地位靠服装一眼可辨, 写明具体单品)。"
    "**时代锚 (1009 用户令, 治古代服饰)**: 本书人物默认当代职场时空 — 服饰锚定 "
    "衬衫/针织衫/西装/西裤/休闲裤系 (现代单品, 禁长袍马褂古装宽袖); 仅书里明写的历史人物"
    "按其真实年代着装 (如 20 世纪初企业家=三件套西装怀表, 也是近代正装而非古装)。"
    "**域优先+禁令 (1026 用户令, 治'样张本身就是错误示例'— 顾问书试镜镜曾出会议室+落地窗"
    "天际线+悬浮卡片)**: ① 画面优先取材本书题材域与目标读者真实处境 (材料有赛道锚则锚定), "
    "域内场景自带背景板; ② 通用商务空镜默认禁, 出现即废 — 会议室开会/白板PPT/咖啡厅对坐/"
    "落地窗望城市天际线/报表笔记本电脑特写 (零信息, 跟任何书都兼容); ③ 概念镜禁悬浮卡片/"
    "图鉴/图标陈列 — 概念画成域内的物理过程或人物动作; ④ 禁写全局调色指令 (色调偏冷/"
    "性冷淡/低饱和这类词) — 调色权归风格配方, 镜句只写画面事实, 违者整图被带偏泛白; "
    "⑤ 光线层次要写, 但主光源禁用'白色'字样 (顶灯白色日光/白色光这类) — 会渲成大面积"
    "过曝露白 (1026 style0_1 实锤), 写柔光/暖白/漫射光。"
    "image_prompt_zh 为 K2 文生图用中文自然语言长句: 主体+动作+场景+构图+光线, "
    "画面里**禁任何文字/字幕/招牌/品牌**。输出严格 JSON: "
    '{"shots":[{"name":"短名","image_prompt_zh":"画面长句"}]}; 恰好 3 镜。'
)

_PROPOSE_SYS = (
    "你是动画风格配方设计师。给定书题材、试镜画面、风格库现有成员与可用 LoRA 清单, "
    "提案**若干个互不相同的新风格配方** — 每个与库内现有成员及彼此明显差异化 "
    "(品类/线条/上色/质感至少三维不同)。"
    "【已排除风格】= 用户不喜欢**那个具体配方** (可能只是执行/人物形态不对), "
    "**不封杀品类方向** — 同方向换个执行再来合法 (如 2D 版被排除, 出一版 3D 锚定的同方向是最佳状态); "
    "仅同名配方不得再出。军规: "
    "①卡通大类红线 (0922 用户令): 一切画面主体 (含人物) 必须是卡通/动画形象 — "
    "禁真人写实、禁照片感、禁写实人脸、禁 3D 超写实人像, 风格词里禁出现 真人/写实/照片/realistic; "
    "②适配该书题材与受众 (老谭读书·商业拆书, 观众要看得懂画面里的商业场景); "
    "③K2 引擎可执行, 提示词遵守 K2 家规 (违反即废): "
    "style_prompt_head = **一句定调 ≤60字** (风格名+2~3个核心质感词, 以冒号结尾) — 禁长串堆砌; "
    "**head 必须锚定人物形态** (明确 2D平面动画人物/3D卡通人物/Q版三选一 — 只锚质感不锚人物形态, "
    "同风格内人物会一会2D一会3D); "
    "style_prompt_tail = **轻锚 ≤40字** (构图+16:9+至多一个质感词); "
    "**同一风格词禁在头尾出现两次** (重复稀释主体权重); "
    "质感词总量 ≤5 — 堆砌互相打架画面会脏, 画面要经得起放大看 (线条干净/上色利落); "
    "④画面禁文字元素 (防幻觉字); ⑤h3_style 为英文一句 (图生视频一致性用); "
    "⑥lora 只能从清单内选一个文件名, 或 null (纯提示词方案 — 同样合法)。"
    "输出严格 JSON: "
    '{"proposals":[{…}, …]} — 恰好按要求数量给, 逐个字段: '
    '{"name":"短英文名","desc":"中文说明: 设计理由+预期效果+与库内差异点",'
    '"style_prompt_head":"…：","style_prompt_tail":"…16:9…",'
    '"h3_style":"english one sentence","lora":"清单内文件名或null","lora_strength":0.6}'
)

# 0923 宪法进每个入口 (用户令): 画面宪法统一注入 试镜镜/提案/重调 三个 sys —
# 改 compliance_visual.json 即全部同步 (源头规避, VLM/OCR 守门只是兜底)
# 1012 视觉链权限宪法同注 (治"国风→朱砂红→衬衫变红"全链传导 — 用户令修链不修点)
try:
    from app.services.compliance import visual_constitution_prompt as _vcp
    _VISUAL_LAW = _vcp()
except Exception:  # noqa: BLE001 — 宪法加载失败不炸模块, 空块=只靠下游守门
    _VISUAL_LAW = ""
from .chain_law import VISUAL_CHAIN_LAW

_SHOTS_SYS += _VISUAL_LAW + VISUAL_CHAIN_LAW
_PROPOSE_SYS += _VISUAL_LAW + VISUAL_CHAIN_LAW


# ── 路径 ─────────────────────────────────────────────────────

def _assets() -> Path:
    return Path(load().output_root) / "_资产"


def _board_dir(book_title: str) -> Path:
    return _assets() / "风格选型" / _safe_name(book_title)


def _binding_path(book_title: str) -> Path:
    return _assets() / f"风格绑定_{_safe_name(book_title)}.json"


def _excl_path(book_title: str) -> Path:
    return _board_dir(book_title) / "排除.json"


def _direction_path(book_title: str) -> Path:
    return _board_dir(book_title) / "方向.json"


def _fp_path(book_title: str) -> Path:
    return _board_dir(book_title) / "渲染指纹.json"


def _render_fp(st: dict, shot: dict, direction: str, neg: str = "") -> str:
    """渲染配方指纹 (1009 小坑7): 决定这张图'配方没变'的所有输入的哈希 —
    风格头尾/k2 覆写/LoRA+强度/军规文本/方向/镜描述。增量跳过的前提是
    '同镜同种子配方未变', 配置升级 (如军规改版/方向指定) 后旧图自动失效重渲,
    防同板混新旧两版配方 (22:57 base 版留存列 vs k2+军规新列实锤).
    1010: neg (圣经负面词) 参与指纹 — 空值不追加, 字节不变 → 无负词书旧指纹不失效."""
    from .k2 import SKIN_HAIR_LAW as _shl_f
    k2ov = (st.get("page_overrides") or {}).get("k2") or {}
    blob = "|".join(str(x or "") for x in (
        st.get("style_prompt_head"), st.get("style_prompt_tail"),
        k2ov.get("style_prompt_head"), k2ov.get("style_prompt_tail"),
        st.get("lora"), st.get("lora_strength"), st.get("h3_style"),
        _shl_f(), direction, shot.get("image_prompt_zh")))
    if neg:
        blob += "|neg:" + neg
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _load_fps(book_title: str) -> dict:
    try:
        return dict(json.loads(_fp_path(book_title).read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return {}


def _save_fps(book_title: str, fps: dict) -> None:
    try:
        _fp_path(book_title).parent.mkdir(parents=True, exist_ok=True)
        _fp_path(book_title).write_text(
            json.dumps(fps, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — 指纹落盘失败不阻断 (下轮全量重验)
        logger.warning("[style-board] 渲染指纹落盘失败: %s", exc)


def get_direction(book_title: str) -> str:
    """用户指定的大风格方向 (1009 小坑6): 如 "2.5D 立体感, 禁平面扁平" / "水墨" —
    注入 Kimi 提案与重调 prompt, 圈内探索不乱撞; 空串 = 无约束 (随机差异化)."""
    try:
        return str(json.loads(_direction_path(book_title).read_text(encoding="utf-8"))
                   .get("direction") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def set_direction(book_title: str, direction: str) -> str:
    _board_dir(book_title).mkdir(parents=True, exist_ok=True)
    _direction_path(book_title).write_text(
        json.dumps({"direction": (direction or "").strip()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return (direction or "").strip()


def exclusions(book_title: str) -> list[str]:
    try:
        return list(json.loads(_excl_path(book_title).read_text(encoding="utf-8"))["styles"])
    except Exception:  # noqa: BLE001
        return []


def set_exclusion(book_title: str, name: str, on: bool) -> list[str]:
    """排除/恢复 (0922 用户令): 排除后该书再选型不再入板, Kimi 提案补位且避开该方向."""
    cur = [s for s in exclusions(book_title) if s != name]
    if on:
        cur.append(name)
    _board_dir(book_title).mkdir(parents=True, exist_ok=True)
    _excl_path(book_title).write_text(
        json.dumps({"styles": cur}, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def bound_style(book_title: str) -> str | None:
    p = _binding_path(book_title)
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("style")
    except Exception:  # noqa: BLE001
        return None


# ── LLM 步 ───────────────────────────────────────────────────

def _llm():
    from app.services.llm_service import get_llm_service as _get
    return _get()


def _parse(raw: str) -> dict:
    from app.services.llm_service import _parse_json
    return _parse_json(raw)


def _plan_shots(material: dict) -> list[dict]:
    raw = _llm().chat(_SHOTS_SYS, json.dumps(material, ensure_ascii=False),
                      model="pro", temperature=0.4)
    shots = _parse(raw).get("shots") or []
    shots = [s for s in shots if isinstance(s, dict) and (s.get("image_prompt_zh") or "").strip()]
    if not 2 <= len(shots) <= 3:
        raise RuntimeError(f"试镜镜规划异常: 得 {len(shots)} 镜 (须 2-3)")
    return shots[:3]


def _library_styles() -> list[dict]:
    lib = _assets() / "风格库"
    out: list[dict] = []
    for p in sorted(lib.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[style-board] 库成员坏跳过 %s: %s", p.name, exc)
    return out


# 卡通红线确定性守门 (0922 用户令): 不靠 LLM 自觉 — 提示词字段含真人系词即弃
_BANNED_STYLE_WORDS = ("真人", "写实", "照片", "photoreal", "realistic", "real person", "realistic face")


def _banned(recipe_text: str) -> str | None:
    t = recipe_text.lower()
    return next((w for w in _BANNED_STYLE_WORDS if w.lower() in t), None)


def _propose_n(material: dict, shots: list[dict], styles: list[dict],
               excl: list[str], n: int, direction: str = "") -> list[dict]:
    """Kimi 提案 ×N (0922 排除补位): 被排除风格由新提案顶替, 且提案避开排除方向.
    1009 小坑6: direction = 用户指定大方向 (如 "2.5D 立体, 禁平面"), 硬约束圈内探索."""
    loras = sorted(p.name for p in _LORA_DIR.glob("*.safetensors")) if _LORA_DIR.exists() else []
    lib_brief = "\n".join(
        f"- {s.get('name')}: {str(s.get('desc') or '')[:80]} | head={str(s.get('style_prompt_head'))[:40]}"
        for s in styles)
    user = (f"书名: 《{material.get('书名')}》\n核心主张: {material.get('核心主张')}\n"
            f"【第1集总纲】\n{json.dumps(material.get('第1集') or {}, ensure_ascii=False)}\n"
            f"【试镜画面】\n" + "\n".join(f"- {s.get('name')}: {s.get('image_prompt_zh')}" for s in shots)
            + f"\n【风格库现有成员 (提案必须与它们差异化)】\n{lib_brief or '(空)'}"
            + (f"\n【已排除风格 (用户不喜欢, 提案方向也须避开)】\n" + "\n".join(f"- {x}" for x in excl)
               if excl else "")
            + f"\n【要求数量】恰好 {n} 个互不相同的提案"
            + f"\n【可用 LoRA 清单 (Krea2-风格 目录, 只能从这里选或 null)】\n" + "\n".join(loras))
    try:
        raw = _llm().chat(_PROPOSE_SYS, user, model="pro", temperature=0.6)
        data = _parse(raw)
        items = data.get("proposals") or ([data] if data.get("name") else [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[style-board] 新配方提案失败 (跳过, 板内仅库成员): %s", exc)
        return []
    out: list[dict] = []
    taken = {s.get("name") for s in styles} | set(excl)
    for data in items:
        if not isinstance(data, dict) or not (data.get("style_prompt_head") or "").strip():
            continue
        bad = _banned(" ".join(str(data.get(k) or "") for k in
                               ("style_prompt_head", "style_prompt_tail", "h3_style")))
        if bad:  # 卡通红线 (确定性, 不靠 LLM 自觉)
            logger.warning("[style-board] 提案踩卡通红线 (词 '%s'), 弃: %s",
                           bad, data.get("name"))
            continue
        name = str(data.get("name") or "").strip()[:24] or f"新提案{len(out) + 1}"
        if name in taken:
            name += f"_v{len(out) + 2}"
        taken.add(name)
        lora = data.get("lora")
        if lora and loras and str(lora) not in loras:  # 幻觉文件名 → 纯提示词
            logger.warning("[style-board] 提案 LoRA 不在清单, 降纯提示词: %s", lora)
            lora = None
        try:
            strength = max(0.0, min(1.0, float(data.get("lora_strength") or 0.6)))
        except (TypeError, ValueError):
            strength = 0.6
        out.append({
            "name": name, "desc": str(data.get("desc") or "")[:300],
            "style_prompt_head": (data.get("style_prompt_head")
                                  or DEFAULT_STYLE["style_prompt_head"]),
            "style_prompt_tail": (data.get("style_prompt_tail")
                                  or DEFAULT_STYLE["style_prompt_tail"]),
            "h3_style": data.get("h3_style") or DEFAULT_STYLE["h3_style"],
            "lora": f"Krea2-风格\\{lora}" if lora else "",
            "lora_strength": strength, "is_new": True,
        })
        if len(out) >= n:
            break
    return out


# ── 渲染 + job ───────────────────────────────────────────────

def _pub(job: dict, msg: str) -> None:
    job["log"].append(msg)
    job["log"] = job["log"][-80:]
    logger.info("[style-board] %s", msg)
    _emit(job, msg)


def _run_board(job: dict) -> None:
    from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK
    try:
        shots = _plan_shots(job["material"])
        job["shots"] = shots
        _pub(job, f"试镜镜规划 ✓ {len(shots)} 镜: " + " / ".join(s["name"] for s in shots))
        excl = exclusions(job["book_title"])
        lib_all = _library_styles()
        styles = [s for s in lib_all if s.get("name") not in excl]
        out = _board_dir(job["book_title"])
        out.mkdir(parents=True, exist_ok=True)
        # 0922 排除补位 + 1009 小坑1 修: 板上风格总数封顶 6 — 旧公式"基础 1 个
        # 新鲜血液"在库满员 (6) 时叠出 7 风格×3 镜=21 图, 页面撑爆且选不过来。
        # 补位语义: 排除几个补几个; 库不满 6 时最多补到 6。
        n_prop = min(3, max(0, 6 - len(styles)) + (len(lib_all) - len(styles)))
        direction = get_direction(job["book_title"])
        if direction:
            _pub(job, f"风格方向已指定: {direction} — 提案在圈内探索")
        proposals = _propose_n(job["material"], shots, styles, excl, n_prop,
                               direction=direction) if n_prop else []
        if proposals:
            styles += proposals
            # 完整配方落盘 (select 入库时吃这份, 板上只有摘要)。
            # 1009 小坑4: 合并而非覆盖 — 旧提案配方 (含 retune fork 的 _改 列) 是
            # select 入库/绑定快照的源, 整文件覆盖会断链
            _old_props: list = []
            try:
                _pl = json.loads((out / "提案.json").read_text(encoding="utf-8"))
                _old_props = (_pl.get("proposals") if isinstance(_pl, dict) else _pl) or []
            except Exception:  # noqa: BLE001 — 无旧文件 = 纯新提案
                pass
            _new_names = {p.get("name") for p in proposals}
            _merged = [p for p in _old_props
                       if isinstance(p, dict) and p.get("name") not in _new_names] + proposals
            (out / "提案.json").write_text(
                json.dumps({"proposals": _merged}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            _pub(job, f"Kimi 新配方提案 ✓ ×{len(proposals)}: "
                      + " / ".join(f"{p['name']} ({p['lora'] or '纯提示词'})" for p in proposals))
        else:
            _pub(job, "新配方提案跳过 (板内仅库成员)")
        out = _board_dir(job["book_title"])
        # 1009 小坑4 修 (增量选型): 列号 = **稳定 idx** — 同名风格跨轮继承旧 board
        # 的 idx, 新风格取 max+1 递增。旧版每轮 enumerate 重排, 排除几个后新列号
        # 会撞旧文件名 (排除 2 个重启 → 新提案占用 style4_*.png = 张冠李戴串图)。
        _old_idx: dict[str, int] = {}
        _max_idx = -1
        try:
            _old = json.loads((out / "board.json").read_text(encoding="utf-8"))
            for s in _old.get("styles") or []:
                if isinstance(s, dict) and s.get("name") is not None \
                        and s.get("idx") is not None:
                    _old_idx[str(s["name"])] = int(s["idx"])
                    _max_idx = max(_max_idx, int(s["idx"]))
        except Exception:  # noqa: BLE001 — 无旧板/坏板 = 全新编号
            pass
        stable: list[int] = []
        for s in styles:
            nm = str(s.get("name"))
            if nm in _old_idx:
                stable.append(_old_idx[nm])
            else:
                _max_idx += 1
                stable.append(_max_idx)
        n_total = len(styles) * len(shots)
        n_skip = 0
        fps = _load_fps(job["book_title"])
        # 1012: 接 GPU 托管 (用户实锤 "动画工坊K2按钮能自动拉起, 选型台却409") —
        # 与 k2/h3 同款 session("comfyui"): 不在线自动拉起+排队互斥+保温;
        # 外部已起的实例 (如手动 bat) 直接复用不杀
        from app.services.gpu_service_manager import get_gpu_service_manager
        with get_gpu_service_manager().session(
                "comfyui", status_callback=lambda m: _pub(job, m)), GPU_JOB_LOCK:
            for k, st in enumerate(styles):
                si = stable[k]
                for ci, sh in enumerate(shots):
                    if job.get("cancel"):
                        job["status"] = "cancelled"
                        return
                    tag = _safe_name(str(st.get("name")))
                    dest = out / f"style{si}_{ci + 1}.png"
                    # 1009 小坑4: 已有样张跳过 — 同镜同种子 (只比风格不赌种子),
                    # 配方未变时重 roll 出的就是同图, 排除后重启只应渲新提案列。
                    # 小坑7 补: 跳过须过**配方指纹** — 军规改版/方向指定/k2 覆写变化
                    # 都会让旧图失效重渲 (防同板混两版配方)。
                    fp = _render_fp(st, sh, direction, bible_negative(job["book_title"]))
                    if dest.exists() and fps.get(dest.name) == fp:
                        n_skip += 1
                        job["done_n"] += 1
                        job.setdefault("cells", []).append(
                            {"style_idx": si, "shot_idx": ci, "file": dest.name})
                        continue
                    if dest.exists():
                        _pub(job, f"{st.get('name')} · 镜{ci + 1}: 配方已变 (军规/方向/卡更新) → 重渲")
                    _pub(job, f"K2 渲染 {st.get('name')} · 镜{ci + 1} "
                              f"({job['done_n'] + 1}/{n_total})…")
                    # 0925 修: p 默认值定义时求值, 彼时 i 未绑定 (循环变量改名 i→ci
                    # 漏改, 0923 重构后首跑即 NameError 秒炸) — f-string 直取 ci
                    # 1009 大坑修 (backlog ID-080): 试镜渲染走 page="k2" 产线配方 —
                    # 旧走 base (LoRA 全量+无皮肤头发军规) = A 货架试穿 B 货架交货,
                    # 用户按选型图定的风格, 产线交付的是另一套参数 (塑料感根因)。
                    render_gated(
                        lambda sd, s=st, c=sh, i=ci, p=f"styleboard/{_safe_name(job['book_title'])}/{tag}/m{ci + 1}":
                            build_workflow(c["image_prompt_zh"], sd, p, style=s, page="k2",
                                           book_title=job["book_title"]),
                        dest, _BOARD_SEED + ci,
                        f"styleboard {st.get('name')} m{ci + 1}")
                    fps[dest.name] = fp
                    _save_fps(job["book_title"], fps)  # 1012: 增量落盘 — 崩一次不再丢整轮指纹账本
                    job["done_n"] += 1
                    job.setdefault("cells", []).append(
                        {"style_idx": si, "shot_idx": ci, "file": dest.name})
        _save_fps(job["book_title"], fps)
        if n_skip:
            _pub(job, f"增量选型: 沿用已有样张 {n_skip} 张 (配方指纹一致; 强制重掷=删对应 png)")
        board = {
            "book_title": job["book_title"], "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "direction": direction,
            "shots": shots,
            "styles": [{"name": s.get("name"), "desc": s.get("desc") or "",
                        "lora": s.get("lora") or "", "is_new": bool(s.get("is_new")),
                        "idx": stable[k]}
                       for k, s in enumerate(styles)],
            "excluded": excl,
            "selected": None,
        }
        (out / "board.json").write_text(json.dumps(board, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        job["status"] = "done"
        _pub(job, f"选择板就绪 ✓ {len(styles)} 风格 × {len(shots)} 镜")
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(exc)
        logger.exception("[style-board] 失败")


def start_board(book_id: str, book_title: str, material: dict) -> dict:
    with _JOBS_LOCK:
        running = next((j for j in _JOBS.values() if j["status"] == "running"), None)
        if running:
            raise RuntimeError(f"风格选型进行中 (《{running['book_title']}》), 稍后再试")
        # 1012: 旧版此处 comfy.health() 硬门禁 — 已改接 GPU 托管 session("comfyui")
        # 不在线自动拉起 (与动画工坊 K2 按钮同款), 启动进度走 job 日志
        job = {"id": str(uuid.uuid4()), "status": "running", "book_id": book_id,
               "book_title": book_title, "material": material, "shots": [],
               "done_n": 0, "cells": [], "log": [], "error": None, "cancel": False}
        _JOBS[job["id"]] = job
        if len(_JOBS) > 20:
            for old in [j for j in _JOBS.values() if j["status"] != "running"][:-10]:
                _JOBS.pop(old["id"], None)
    threading.Thread(target=_run_board, args=(job,), daemon=True,
                     name="style-board").start()
    return _view(job)


def _view(job: dict) -> dict:
    return {"id": job["id"], "status": job["status"], "done_n": job["done_n"],
            "log": job["log"][-12:], "error": job["error"],
            "shots": [{"name": s.get("name")} for s in job.get("shots") or []]}


def library_view(book_title: str) -> list[dict]:
    """风格库预筛清单 (1009 小坑5): 新书未启动选型时, 先把库成员列出来让用户
    预否一批 — 排除即写入书级 排除.json (与板上排除同一存储), 启动后 Kimi 提案
    自动避开该方向, 不渲注定被否的列 (省 GPU 省提案 quota)。
    """
    excl = exclusions(book_title)
    out: list[dict] = []
    for s in _library_styles():
        nm = str(s.get("name") or "")
        if not nm:
            continue
        ref = _assets() / "风格库" / f"{_safe_name(nm)}_参考_选择板.png"
        out.append({
            "name": nm,
            "desc": str(s.get("desc") or "")[:160],
            "lora": str(s.get("lora") or ""),
            "has_ref": ref.exists(),
            "excluded": nm in excl,
        })
    return out


def lib_ref_img(book_title: str, name: str) -> Path:
    """库成员参考图 (防穿越同 board_img)."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise FileNotFoundError(name)
    p = _assets() / "风格库" / f"{_safe_name(name)}_参考_选择板.png"
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def board_view(book_id: str, book_title: str = "") -> dict:
    with _JOBS_LOCK:
        running = next((j for j in _JOBS.values()
                        if j["status"] == "running" and j["book_id"] == book_id), None)
        any_running = next((j for j in _JOBS.values() if j["status"] == "running"), None)
        # 0925 修: 非运行 job 也回显 (error/done) — 旧版只回 running, job 报错被
        # 吞 → 页面无失败提示, 按钮看着像"失效" (ComfyUI 未起实锤)
        mine = running or next(
            (j for j in reversed(list(_JOBS.values())) if j["book_id"] == book_id), None)
    view: dict[str, Any] = {"job": _view(mine) if mine else None,
                            "busy_other": bool(any_running and not running),
                            "direction": get_direction(book_title)}
    rj = _RETUNE_JOBS.get(book_id)
    if rj:
        view["retune"] = {k: rj.get(k) for k in ("status", "style", "error", "result")}
    # 板子优先从盘读 (0922 修: 重启后内存 job 失忆, board.json 才是事实源)
    if not running:
        with _JOBS_LOCK:
            job = next((j for j in reversed(list(_JOBS.values()))
                        if j["book_id"] == book_id and j["status"] == "done"), None)
        b = _read_board(job["book_title"] if job else book_title)
        if b:
            view["board"] = b
    # 1009 小坑5: 未启动选型 (无板) 时带库预筛清单 — 页面先列库让用户预否一批
    # 小坑5补修: job running 期间也不带 — 旧条件只看"无 board", 渲染中每 3s 轮询
    # 都把预筛区发下去, 前端把进行中的板覆盖成预筛 (用户实锤"选定/启动后预筛再现")
    _j_running = bool(mine and mine.get("status") == "running")
    if not view.get("board") and not _j_running:
        view["library"] = library_view(book_title)
    return view


def _read_board(book_title: str) -> dict | None:
    if not book_title:
        return None
    try:
        return json.loads((_board_dir(book_title) / "board.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def board_img(book_title: str, name: str) -> Path:
    """选择板图片 (防穿越: 纯文件名校验)."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise FileNotFoundError(name)
    p = _board_dir(book_title) / name
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def _persist_proposal(book_title: str, hit: dict) -> bool:
    """新配方入库 (0922): 风格库 json + 首镜样张参考图. select/keep 共用."""
    new_json = _board_dir(book_title) / "提案.json"
    if not new_json.exists():
        return False
    try:
        payload = json.loads(new_json.read_text(encoding="utf-8"))
        proposals = payload.get("proposals") if isinstance(payload, dict) else payload
        recipe = next((p for p in (proposals or [])
                       if isinstance(p, dict) and p.get("name") == hit["name"]), None)
        if not recipe:
            return False
        lib_p = _assets() / "风格库" / f"{_safe_name(hit['name'])}.json"
        data = {k: v for k, v in recipe.items() if k != "is_new"}
        data["name"] = hit["name"]
        data["desc"] = (hit.get("desc") or "") + " (0922 风格选择板提案入库)"
        lib_p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        ref_src = _board_dir(book_title) / f"style{hit.get('idx')}_1.png"
        if ref_src.exists():
            (lib_p.parent / f"{_safe_name(hit['name'])}_参考_选择板.png").write_bytes(
                ref_src.read_bytes())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[style-board] 新配方入库失败: %s", exc)
        return False


_RETUNE_SYS = (
    "你是动画风格配方调优师。用户对某提案配方说'方向对, 但画面粗糙/还原不足'。"
    "给定原配方与试镜画面, 出**一版修正配方**: 风格方向与名字不变, 只准动这些旋钮 — "
    "①lora: 换清单内更贴合该方向的 LoRA, 或 null 走纯提示词; "
    "②lora_strength: ±0.1~0.2 微调; "
    "③style_prompt_head/tail: 按 K2 家规精修 (head ≤60字一句定调, tail ≤40字轻锚, "
    "禁头尾重复, 质感词总量≤5 — 画面要经得起放大看); "
    "**head 必须锚定人物形态** (2D平面动画人物/3D卡通人物/Q版 三选一 — 不锚则同风格人物 2D/3D 乱漂); "
    "④h3_style 随动。"
    "红线: 卡通大类, 禁真人/写实/照片系词。输出严格 JSON 单对象 (同提案 schema)。"
)


_RETUNE_SYS = _RETUNE_SYS + _VISUAL_LAW


def retune(book_title: str, style_name: str, note: str = "") -> dict:
    """重调 (0922 用户令·已确认): 方向对但画面糙 → Kimi 修配方旋钮, 单列重渲 3 张.

    单列回路不动全板; kept 过的配方同步更新风格库; 板 created_at 刷新破缓存。
    1009 小坑2 修 (backlog ID-080 附): **库成员也可重调** — 用户"整体喜欢但某点
    不对 (如头发塑料感)"此前零入口 (旧版仅提案列有按钮)。库成员重调不改库卡
    (跨书共用), 产出 `_改` 新配方作为新提案列追加, 原列保留可对照。
    """
    if not (note or "").strip():
        note = "画面粗糙, 请精修还原度"
    board_p = _board_dir(book_title) / "board.json"
    board = json.loads(board_p.read_text(encoding="utf-8"))
    hit = next((s for s in board["styles"] if s["name"] == style_name), None)
    if not hit:
        raise KeyError(f"选择板无此风格: {style_name}")
    # 1009: 库成员重调 → 产出独立新列 (_改), 不动库卡
    fork_mode = not hit.get("is_new")
    # 源配方: 提案.json 优先, kept/库成员以风格库为准
    recipe = None
    try:
        payload = json.loads((_board_dir(book_title) / "提案.json").read_text(encoding="utf-8"))
        proposals = payload.get("proposals") if isinstance(payload, dict) else payload
        recipe = next((p for p in (proposals or [])
                       if isinstance(p, dict) and p.get("name") == style_name), None)
    except Exception:  # noqa: BLE001
        pass
    if recipe is None:
        try:
            recipe = json.loads((_assets() / "风格库" / f"{_safe_name(style_name)}.json")
                                .read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    if not recipe:
        raise KeyError(f"配方源缺失: {style_name} (提案.json/风格库均无)")

    loras = sorted(p.name for p in _LORA_DIR.glob("*.safetensors")) if _LORA_DIR.exists() else []
    user = (f"【用户判词】{note}\n【原配方】\n{json.dumps(recipe, ensure_ascii=False)}\n"
            f"【试镜画面】\n" + "\n".join(f"- {s.get('name')}: {s.get('image_prompt_zh')}"
                                         for s in board.get("shots") or [])
            + "\n【可用 LoRA 清单 (只能从这里选或 null)】\n" + "\n".join(loras))
    raw = _llm().chat(_RETUNE_SYS, user, model="pro", temperature=0.3)
    data = _parse(raw)
    if not isinstance(data, dict) or not (data.get("style_prompt_head") or "").strip():
        raise RuntimeError("重调输出不可解析")
    if _banned(" ".join(str(data.get(k) or "") for k in
                        ("style_prompt_head", "style_prompt_tail", "h3_style"))):
        raise RuntimeError("重调配方踩卡通红线 (真人/写实系词), 已弃")
    lora = data.get("lora")
    if lora and loras and str(lora) not in loras:
        lora = None
    try:
        strength = max(0.0, min(1.0, float(data.get("lora_strength") or recipe.get("lora_strength") or 0.6)))
    except (TypeError, ValueError):
        strength = 0.6
    out_name = f"{style_name}_改" if fork_mode else style_name
    new_recipe = {
        "name": out_name,
        "desc": (str(data.get("desc") or recipe.get("desc") or "")[:250]
                 + (f" (由 {style_name} 重调)" if fork_mode else "")),
        "style_prompt_head": data["style_prompt_head"],
        "style_prompt_tail": data.get("style_prompt_tail") or DEFAULT_STYLE["style_prompt_tail"],
        "h3_style": data.get("h3_style") or DEFAULT_STYLE["h3_style"],
        "lora": f"Krea2-风格\\{lora}" if lora else "",
        "lora_strength": strength, "is_new": True, "retuned": True,
    }
    # 配方回写: 提案.json (fork=追加新条目; 提案列=替换同名); kept 过的同步风格库
    prop_p = _board_dir(book_title) / "提案.json"
    try:
        payload = json.loads(prop_p.read_text(encoding="utf-8"))
        lst = payload.get("proposals") if isinstance(payload, dict) else payload
        lst = [new_recipe if (p or {}).get("name") == style_name else p for p in (lst or [])]
        if not any((p or {}).get("name") == out_name for p in lst):
            lst.append(new_recipe)
        prop_p.write_text(json.dumps({"proposals": lst}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[style-board] 重调配方回写提案.json 失败: %s", exc)
    if hit.get("kept") and not fork_mode:
        try:
            lib_p = _assets() / "风格库" / f"{_safe_name(style_name)}.json"
            lib = {k: v for k, v in new_recipe.items() if k not in ("is_new", "retuned")}
            lib_p.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[style-board] 重调同步风格库失败: %s", exc)

    # 1009 fork: 板上追加新列 (原列保留对照); 提案列维持原列重渲。
    # 列号取板上 max idx+1 (稳定编号 — 增量重启后 len ≠ max, 用 len 会撞旧文件名)
    if fork_mode:
        si = max([int(s.get("idx") or 0) for s in board["styles"]] or [-1]) + 1
        board["styles"].append({
            "name": out_name, "desc": new_recipe["desc"],
            "lora": new_recipe["lora"] or "", "is_new": True, "idx": si,
        })
    else:
        si = int(hit.get("idx") or 0)

    # 单列重渲 3 张 (同镜同种子, 与首轮可对照) — 文字守门同产线
    # 1012 修: _dir 此前未定义 (e4da0ffb 引入) — 重调渲完第 1 张即 NameError,
    # 板刷新/指纹落盘全断; 方向入指纹与 _run_board 同口径
    _dir = get_direction(book_title)
    from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK
    from app.services.gpu_service_manager import get_gpu_service_manager
    _board_dir(book_title).mkdir(parents=True, exist_ok=True)
    with get_gpu_service_manager().session("comfyui"), GPU_JOB_LOCK:
        fps = _load_fps(book_title)
        for ci, sh in enumerate(board.get("shots") or []):
            dest = _board_dir(book_title) / f"style{si}_{ci + 1}.png"
            # 1009 大坑修: 重调重渲同走 page="k2" — 与产线/选型同源 (否则调满意了
            # 产线还是另一套); 小坑7: 重渲后更新配方指纹 (防下轮选型重复重渲)
            render_gated(
                lambda sd, c=sh, i=ci: build_workflow(
                    c["image_prompt_zh"], sd,
                    f"styleboard/{_safe_name(book_title)}/{_safe_name(out_name)}/r{i + 1}",
                    style=new_recipe, page="k2", book_title=book_title),
                dest, _BOARD_SEED + ci,
                f"styleboard-retune {out_name} m{ci + 1}")
            fps[dest.name] = _render_fp(new_recipe, sh, _dir, bible_negative(book_title))
        _save_fps(book_title, fps)
    # 板刷新: desc 标重调 + created_at 破缓存 (fork 模式新列已在上面 append, 原列不动)
    if not fork_mode:
        hit["desc"] = (new_recipe.get("desc") or hit.get("desc") or "")[:300]
        hit["retuned"] = True
    board["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    board_p.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"book_title": book_title, "style": out_name, "retuned": True, "forked": fork_mode,
            "recipe": {k: new_recipe[k] for k in ("style_prompt_head", "style_prompt_tail",
                                                  "lora", "lora_strength")}}


# ── 重调 job (0922): 单列回路, 页面轮询 ───────────────────────
_RETUNE_JOBS: dict[str, dict] = {}


def start_retune(book_id: str, book_title: str, style_name: str, note: str = "") -> dict:
    j = _RETUNE_JOBS.get(book_id)
    if j and j.get("status") == "running":
        raise RuntimeError(f"重调进行中 ({j.get('style')}), 稍后再试")
    j = {"id": str(uuid.uuid4()), "status": "running", "book_id": book_id,
         "style": style_name, "log": [], "error": None, "result": None}
    _RETUNE_JOBS[book_id] = j

    def _run():
        try:
            j["result"] = retune(book_title, style_name, note)
            j["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            j["status"] = "error"
            j["error"] = str(exc)
            logger.exception("[style-board] 重调失败")

    threading.Thread(target=_run, daemon=True, name="style-retune").start()
    return {"id": j["id"], "status": j["status"], "style": style_name}


# ── 艺术圣经页 (0922 用户令流程: 大纲→风格选型→艺术圣经独立页) ──
_BIBLE_JOBS: dict[str, dict] = {}


def _bible_job_gate(book_id: str) -> dict | None:
    """圣经/试渲共用一槽互斥 (0923 修×2: 运行中重复触发不再 409 — 回当前 job 让页面进轮询,
    弹窗绝迹; >20min 僵尸才接管). 返回 dict=已有 job 在跑, None=可启动."""
    j = _BIBLE_JOBS.get(book_id)
    if j and j.get("status") == "running":
        if time.time() - float(j.get("t0") or 0) < 1200:
            return j
        logger.warning("[style-bible] 僵尸 job 接管 (%s, %.0f 分钟)",
                       j.get("kind"), (time.time() - float(j.get("t0") or 0)) / 60)
    return None


_BIBLE_DONE: dict[str, dict] = {}   # book_id → {"kind","t","samples"} 最近完成 (冷却用)


def _emit(job: dict, msg: str, typ: str = "info") -> None:
    """SSE 推送 (0923): 选型/圣经/试渲/重调全上实时事件流, 页面免瞎等."""
    try:
        from app.services.job_events import _publish as _jp
        _jp(str(job.get("id") or ""), {"type": typ, "message": msg})
    except Exception:  # noqa: BLE001 — SSE 挂了不影响渲染
        pass


def _bible_done_recently(book_id: str, kind: str) -> dict | None:
    """冷却 (0923 幽灵客户端实锤: Edge 卡死标签页一夜 POST 14万次, 幂等放行导致 GPU 反复点火):
    同类 job 3 分钟内完成过 → 直接回结果, 不再重启渲染。"""
    rec = _BIBLE_DONE.get(book_id)
    if rec and rec.get("kind") == kind and time.time() - rec.get("t", 0) < 180:
        return {"status": "done", "cached": True, "kind": kind, "samples": rec.get("samples")}
    return None


def _samples_fp(book_title: str) -> tuple[str, str, list[str]]:
    """试渲指纹 (1026 抽公共): 渲染路径与冷却守卫同源 — 旧两处各算各的, 格式
    永不相等 → 冷却永远穿透 (连点必全量重渲, 晚间实锤)。返回 (fp, hue, scenes)。"""
    from .config import pick_main_hue, resolve_style
    from .k2 import SKIN_HAIR_LAW as _shl_f
    d = _board_dir(book_title)
    board = json.loads((d / "board.json").read_text(encoding="utf-8"))
    shots = board.get("shots") or []
    from .director2 import _bible_cache_path
    b = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
    hue = pick_main_hue(str(b.get("color_palette") or ""))
    scenes = [str(s.get("image_prompt_zh") or "") + main_color_tint(hue, brief=True)
              for s in shots]
    recipe = resolve_style(book_title)
    k2ov = (recipe.get("page_overrides") or {}).get("k2") or {}
    blob = "bv3|" + "|".join(str(x or "") for x in (
        recipe.get("style_prompt_head"), recipe.get("style_prompt_tail"),
        k2ov.get("style_prompt_head"), k2ov.get("style_prompt_tail"),
        recipe.get("lora"), recipe.get("lora_strength"),
        _shl_f(), hue, *scenes))
    neg = bible_negative(book_title)
    if neg:  # 1010: 负词入指纹, 空值字节不变 (无负词书旧试渲不失效)
        blob += "|neg:" + neg
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12], hue, scenes


def render_bible_samples(book_id: str, book_title: str, force: bool = False) -> dict:
    """圣经试渲 (0922 用户令: 纯文字难转图形效果): 色板/质感/角色策略烘进画面渲 2 张.

    用选定配方 (head/tail/LoRA) + 圣经字段组景; 产 圣经试渲_1/2.png 于板目录
    (经既有 style-board img 端点供页). 占 GPU 锁, 后台 job."""
    running = _bible_job_gate(book_id)
    if running:
        return {"status": "already-running", "kind": running.get("kind")}
    cached = None if force else _bible_done_recently(book_id, "samples")
    if cached:
        # 1009 小坑8-E: 冷却命中但试渲指纹不符 (圣经/配方已变) → 不放行, 重渲
        # 1026 修: 改用 _samples_fp 公共源 (旧内联版与主指纹格式永不相等=冷却死守卫)
        try:
            _fp0, _, _ = _samples_fp(book_title)
            _fps0 = _load_fps(book_title)
            if not _fps0.get("圣经试渲_1.png") or _fps0.get("圣经试渲_1.png") != _fp0:
                cached = None  # 指纹不符 → 穿透冷却强制重渲
        except Exception:  # noqa: BLE001 — 判不了就按冷却放行 (原行为)
            pass
        if cached:
            return cached
    j = {"id": str(uuid.uuid4()), "kind": "samples", "status": "running", "error": None,
         "samples": [], "t0": time.time()}
    _BIBLE_JOBS[book_id] = j

    def _run():
        from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK
        _emit(j, "试渲启动: 原镜+角色卡+主色, 3 张 + 文字守门")
        try:
            view = bible_view(book_id, book_title)
            bible = view.get("bible") or {}
            # 1009 小坑8-B: 配方吃 resolve_style (绑定快照优先) — bible_view 直读
            # 风格库文件绕过了快照, 库卡被别书 retune 改动会连带漂移
            from .config import resolve_style
            recipe = dict(resolve_style(book_title))
            if not recipe.get("style_prompt_head"):
                raise RuntimeError("无风格绑定")
            # 试渲画面 = 原试镜镜 (LLM 画面长句) + 圣经调味 — 同镜同种子,
            # 与板原图并排 A/B, 差异纯 = 圣经贡献 (0922 用户实锤: 首版两句占位文画面过瘦)
            try:
                board = json.loads((_board_dir(book_title) / "board.json").read_text(encoding="utf-8"))
                shots = board.get("shots") or []
            except Exception:  # noqa: BLE001
                shots = []
            if not shots:
                raise RuntimeError("板上无试镜镜 (先跑风格选型)")
            # 0922 调味改确定性追加: LLM 调味器两版都偷偷缩句掉细节 (密度比 0.5x 实锤),
            # 正则抽圣经主色 + 纯字符串拼接, 零改写风险
            # 0925 用户令撤角色注入: 原镜句已按露脸军规自带人物, 硬塞轮换角色 =
            # 多一个人 + 串人 (镜2 霍普金斯本尊在场还被前置董事长实锤); 角色造型
            # 预览归定妆照 (角色设计台), 试渲 A/B 差异纯 = 圣经主色贡献
            # 1026: hue/scenes/指纹收口 _samples_fp (与冷却守卫同源, 治两处漂移;
            # brief 尾语义: 试渲只预览主色贡献, 全尾军规对高光滚降过重实锤)
            _fp, hue, scenes = _samples_fp(book_title)
            d = _board_dir(book_title)
            d.mkdir(parents=True, exist_ok=True)
            (d / "调味句.json").write_text(
                json.dumps({"hue": hue, "scenes": scenes}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            fps = _load_fps(book_title)
            # 1012: force=全量重掷且换种子 (同种子重掷=渲出同一张图, 无意义);
            # 稳态沿用是正确行为但用户要有强制入口 (UX 洞实锤)
            _stale = ([f"圣经试渲_{ci}.png" for ci in range(1, len(scenes) + 1)]
                      if force else
                      [f"圣经试渲_{ci}.png" for ci in range(1, len(scenes) + 1)
                       if fps.get(f"圣经试渲_{ci}.png") != _fp])
            # 1012: 接 GPU 托管 (同选型台) — 不在线自动拉起, 外部实例复用
            from app.services.gpu_service_manager import get_gpu_service_manager
            with get_gpu_service_manager().session(
                    "comfyui", status_callback=lambda m: _emit(j, m)), GPU_JOB_LOCK:
                for ci, scene in enumerate(scenes, 1):
                    f = f"圣经试渲_{ci}.png"
                    if (d / f).exists() and not _stale:
                        j["samples"].append(f)
                        _emit(j, f"试渲 {ci} 沿用 (指纹一致; 配方没变) — 强制重掷=🎲重掷试渲")
                        continue
                    # 1009 小坑8-A: 试渲走 page="k2" 与产线同源 (旧 base 配方 = 圣经页
                    # 看到的和产线出的又是两套, 选型台大坑同款)
                    # 1012 修: 种子对齐镜号 — 旧 +ci(1起) vs 板样张 +ci(0起) 差一位,
                    # 同文不同种子 = 构图全换, "同镜同种子 A/B" 被种子噪声污染
                    # (用户实锤 试镜镜1 vs 圣经试渲1 感觉不一样); 试渲_N=镜N 同种子
                    render_gated(
                        lambda sd, sc=scene, i=ci: build_workflow(
                            sc, sd, f"styleboard/{_safe_name(book_title)}/bible/s{i}",
                            style=recipe, page="k2", book_title=book_title),
                        d / f, _BOARD_SEED + (ci - 1) + (int(time.time()) % 997 if force else 0),
                        f"bible-sample s{ci}" + ("-force" if force else ""))
                    fps[f] = _fp
                    j["samples"].append(f)
                    _emit(j, f"试渲 {ci}/{len(scenes)} ✓ (已过文字守门)")
                _save_fps(book_title, fps)
            j["status"] = "done"
            _BIBLE_DONE[book_id] = {"kind": "samples", "t": time.time(),
                                    "samples": list(j["samples"])}
            _emit(j, "试渲完成 ✓", "samples_done")
        except Exception as exc:  # noqa: BLE001
            j["status"] = "error"
            j["error"] = str(exc)
            _emit(j, f"试渲失败: {exc}", "samples_error")
            j["error"] = str(exc)
            logger.exception("[style-bible] 试渲失败")

    threading.Thread(target=_run, daemon=True, name="bible-sample").start()
    return {"status": "running", "id": j["id"]}


def render_motion_sample(book_id: str, book_title: str) -> dict:
    """动画试渲 (1012 用户令, 治 H3 运动随机 — "液态/雾气"实锤): 圣经试渲首帧
    × 中性节拍 × 书级动画律 (圣经 motion_style) → H3 图生视频 5s。

    圣经页眼验闭环: 看片 → 手调动画律 → 保存 → 再试渲, 直到运动气质锁定;
    产线 h3/r2v 同吃该字段 → 所见=批产所产。节拍刻意中性 (呼吸/眨眼/微推),
    运动气质只来自动画律 — 试的正是锚本身。"""
    running = _bible_job_gate(book_id)
    if running:
        return {"status": "already-running", "kind": running.get("kind")}
    # 1012: 旧 health() 硬门禁已撤 — GPU 托管 session 自动拉起 (同动画工坊K2按钮)
    j = {"id": str(uuid.uuid4()), "kind": "motion", "status": "running", "error": None,
         "samples": [], "t0": time.time()}
    _BIBLE_JOBS[book_id] = j

    def _run():
        from app.services.anim_pipeline.gpu_lock import GPU_JOB_LOCK
        _emit(j, "动画试渲启动: 圣经试渲首帧 + 中性节拍 + 书级动画律, 5s H3 图生视频")
        try:
            from . import h3
            d = _board_dir(book_title)
            # 首帧: 圣经试渲 (带圣经主色) 优先, 退回选定风格板样张
            frames = sorted(d.glob("圣经试渲_*.png"))
            if not frames:
                try:
                    board = json.loads((d / "board.json").read_text(encoding="utf-8"))
                    hit = next((s for s in board.get("styles", [])
                                if s.get("name") == bound_style(book_title)), None)
                    if hit and hit.get("idx") is not None:
                        frames = [d / f"style{hit['idx']}_1.png"]
                except Exception:  # noqa: BLE001
                    pass
            if not frames:
                raise RuntimeError("无可试渲首帧 (先跑🎨圣经试渲或风格选型)")
            frame = frames[0]
            # 画面句: 调味句.json 与试渲帧同源; 退回板上第一试镜镜
            try:
                scenes = json.loads((d / "调味句.json").read_text(encoding="utf-8"))["scenes"]
            except Exception:  # noqa: BLE001
                scenes = []
            try:
                board = json.loads((d / "board.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                board = {}
            opening = ""
            if scenes:
                opening = str(scenes[0])
            elif (board.get("shots") or []):
                opening = str((board["shots"][0] or {}).get("image_prompt_zh") or "")
            if not opening:
                raise RuntimeError("试渲画面句缺失 (板上无试镜镜)")
            anim = {
                "duration_s": 5.0,
                "opening_desc": opening,
                "physical_lock": "镜头视角锁定首帧构图, 仅允许极轻微推拉",
                "beats": [
                    {"t_start": 0.0, "t_end": 2.5,
                     "motion": "主角轻微动作: 呼吸、眨眼、视线微移; "
                               "背景已有物件自然活动, 无人类语音"},
                    {"t_start": 2.5, "t_end": 5.0,
                     "motion": "镜头极轻微缓推, 画面情绪保持稳定, 无人类语音"},
                ],
            }
            # 指纹: 动画律+风格锚+时代锁+首帧(含mtime=首帧重渲连带)+画面句+节拍模板
            _law = h3.motion_style(book_title)
            _fp = hashlib.sha1("|".join(str(x or "") for x in (
                "motion-v6", h3.style_h3(book_title, page="k2"), _law,
                h3.era_anchor(book_title),
                frame.name, f"{frame.stat().st_mtime:.0f}",
                anim["duration_s"], opening,
                *[b["motion"] for b in anim["beats"]])).encode("utf-8")).hexdigest()[:12]
            dest = d / "动画试渲_1.mp4"
            fps = _load_fps(book_title)
            # 1012: GPU 托管 (同上) — 拉起进度走 SSE
            from app.services.gpu_service_manager import get_gpu_service_manager
            with get_gpu_service_manager().session(
                    "comfyui", status_callback=lambda m: _emit(j, m)), GPU_JOB_LOCK:
                if dest.exists() and fps.get(dest.name) == _fp:
                    _emit(j, "动画试渲沿用 (动画律/配方未变); 强制重渲=删 动画试渲_1.mp4")
                else:
                    # 1012: 先清旧片 — 浏览器 <video> 播放中偶发持有句柄, Windows
                    # 下搬运覆盖会败 (重试4次失败实锤); 清不掉=提示关播放
                    try:
                        dest.unlink(missing_ok=True)
                    except OSError as _exc:
                        _emit(j, f"旧片被占用 (页面正在播放?) 关闭后重试: {_exc}")
                        raise
                    image_name = comfy.upload_image(frame)
                    from . import (
                        video_qc,  # 1012: 出片机检 (红旗/衣色漂移) + 重掷到过关
                    )
                    _qc_hist: list = []
                    _shot_qc = {"shot_id": "motion-sample",
                                "image_file": f"{_safe_name(book_title)}/{frame.name}"}
                    _final_sus: dict | None = None
                    for _attempt in range(3):  # 确定性验收不过 → 带禁令换种子重掷 (H3漂移靠掷)
                        # 前缀掺时间戳: 同配方重掷会被 ComfyUI 执行缓存 0.03s 秒回放
                        # 不产新文件 → 搬运工找旧文件失败 (m1_00004 实锤); 破缓存必掺
                        _wf = h3.build_workflow(
                            image_name, anim, _BOARD_SEED + 7 + _attempt * 13,
                            f"video/styleboard/{_safe_name(book_title)}/motion/"
                            f"m{_attempt + 1}_{int(time.time())}",
                            book_title=book_title, qc_history=_qc_hist or None)
                        comfy.run_workflow(_wf, dest=dest, timeout_sec=600 + 240 * 5,
                                           label=f"motion-sample#{_attempt}",
                                           soft_timeout_sec=90 * 5 + 120,
                                           interrupt_grace_sec=load().comfy.interrupt_grace_sec)
                        _sus = video_qc.check(dest, _shot_qc, {"book_title": book_title})
                        _bad = [k for k in (_sus.get("kinds") or [])
                                if k in ("红旗", "衣色漂移", "首帧漂移")]
                        if not _bad:
                            _final_sus = _sus or None
                            _emit(j, "机检干净 ✓" if not _sus else
                                  f"机检通过 (观察项 {_sus.get('kinds')})")
                            break
                        _qc_hist.append({"kind": ",".join(_bad),
                                         "what": str(_sus.get("what") or "")[:60],
                                         "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                        _final_sus = _sus
                        _emit(j, f"第{_attempt + 1}掷命中 {_bad} → 带禁令换种子重掷…")
                    else:
                        _emit(j, f"⚠ 三掷仍 {((_final_sus or {}).get('kinds') or [])} — 请人检/调动画律")
                    fps[dest.name] = _fp
                    _save_fps(book_title, fps)
                    _emit(j, "动画试渲出片 ✓ (5s H3)")
            j["status"] = "done"
            j["samples"] = [dest.name]
            _emit(j, "动画试渲完成 ✓ 圣经页可播 — 眼验运动气质, 不满意改动画律再试", "motion_done")
        except Exception as exc:  # noqa: BLE001
            j["status"] = "error"
            j["error"] = str(exc)
            _emit(j, f"动画试渲失败: {exc}", "motion_error")
            logger.exception("[style-bible] 动画试渲失败")

    threading.Thread(target=_run, daemon=True, name="bible-motion").start()
    return {"status": "running", "id": j["id"]}


def bible_view(book_id: str, book_title: str) -> dict:
    """圣经现值 + 书级绑定摘要 (无绑定=流程未到, 页面挡)."""
    from .director2 import _bible_cache_path
    bible = None
    try:
        bible = json.loads(_bible_cache_path(book_title).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    bound = bound_style(book_title)
    recipe = None
    if bound:
        # 1009 小坑8-B: 走 resolve_style (绑定快照优先) — 旧直读库文件会被别书
        # retune 改卡连带漂移
        try:
            from .config import resolve_style
            recipe = resolve_style(book_title)
        except Exception:  # noqa: BLE001
            pass
    j = _BIBLE_JOBS.get(book_id)
    # 样张 (0922): 选定风格的板样张 (style{idx}_1..3) + 圣经试渲 (如有)
    samples: list[str] = []
    try:
        board = json.loads((_board_dir(book_title) / "board.json").read_text(encoding="utf-8"))
        hit = next((s for s in board.get("styles", []) if s.get("name") == bound), None)
        if hit and hit.get("idx") is not None:
            samples += [f"style{hit['idx']}_{ci}.png" for ci in (1, 2, 3)]
    except Exception:  # noqa: BLE001
        pass
    samples += sorted(p.name for p in _board_dir(book_title).glob("圣经试渲_*.png"))
    # 1012 动画试渲: H3 图生视频样片 (圣经页 <video> 播, 眼验书级动画律)
    motion = _board_dir(book_title) / "动画试渲_1.mp4"
    # 1012 修: 样张破缓存版本号 = 样张/样片最新 mtime — 试渲一天重渲多轮,
    # 页面无版本号时浏览器一直吃第一轮旧图 (用户实锤"多出黑色描边"实为旧版)
    _files = [_board_dir(book_title) / n for n in samples] + ([motion] if motion.exists() else [])
    _rev = int(max((f.stat().st_mtime for f in _files if f.exists()), default=0))
    return {"bound": bound, "style_recipe": recipe, "bible": bible,
            "samples": samples, "motion_sample": motion.name if motion.exists() else None,
            "samples_rev": _rev,
            "job": {k: j.get(k) for k in ("id", "kind", "status", "error", "bible", "samples")} if j else None}


def start_bible(book_id: str, book_title: str, force: bool = False) -> dict:
    running = _bible_job_gate(book_id)
    if running:
        return {"status": "already-running", "kind": running.get("kind")}
    j = {"id": str(uuid.uuid4()), "kind": "bible", "status": "running", "error": None,
         "bible": None, "t0": time.time()}
    _BIBLE_JOBS[book_id] = j

    def _run():
        import hashlib as _hl
        # 0925 修: 前奏全进 try — chars_before 行 bible_view 返回 {"bible": None} 时
        # .get("bible", {}) 拿到 None (key 在, 默认不生效) → AttributeError 炸在 try
        # 块外 → 线程静默死, job 僵尸 running, 页面转圈 (首次生成圣经必中)
        try:
            _emit(j, "圣经生成中 (灵魂三问+受众+总纲贯穿人物+樊登稿+选定配方)…")
            # 1012: 缓存且绑定未变 → 秒回但给出可见反馈 (旧版静默复用,
            # 用户实锤"点生成无任何消息不知道工作了没有")
            try:
                from .director2 import _bible_cache_path as _bcp
                _cached = _bcp(book_title).exists()
                if _cached and not force:
                    _emit(j, "已有圣经且风格绑定未变 — 缓存复用 (换绑会自动重掷; 强换=🎲重掷)")
            except Exception:  # noqa: BLE001
                pass
            chars_before = _hl.md5(json.dumps(
                [{"n": c.get("name"), "l": c.get("look")}
                 for c in (bible_view(book_id, book_title).get("bible") or {}).get("characters") or []],
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        except Exception:  # noqa: BLE001 — 前奏失败不挡生成, 视为无旧卡
            chars_before = ""
        try:
            from .director2 import load_or_derive_bible
            j["bible"] = load_or_derive_bible(book_id, book_title, force=force)
            if not j["bible"].get("style_prefix"):
                j["status"] = "error"
                j["error"] = "生成结果缺 style_prefix (走空)"
                _emit(j, "生成失败: 缺 style_prefix", "bible_error")
                return
            # 0923 status 时序修: 定妆照链跑完才置 done (提前置 done 页面以为完事, 链还在渲)
            # 0923 撕裂修复 (用户令): 角色卡变了 → 定妆照自动重渲, 同一条流滚完, 不用跨页追
            # 1012 补: 绑定变化也触发 — 旧经新风格时角色卡未变但定妆照全是旧风格渲的
            # (render_missing 指纹已并入配方, 此处只是把触发条件带上)
            _bind_before = ""
            try:
                _bind_before = str((bible_view(book_id, book_title).get("bible") or {})
                                   .get("_bound_style") or "")
            except Exception:  # noqa: BLE001
                pass
            chars_after = _hl.md5(json.dumps(
                [{"n": c.get("name"), "l": c.get("look")} for c in j["bible"].get("characters") or []],
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            _bind_after = str(j["bible"].get("_bound_style") or "")
            _bind_changed = bool(_bind_before and _bind_after and _bind_before != _bind_after)
            if force or chars_after != chars_before or _bind_changed:
                _emit(j, "圣经生成 ✓ "
                      + ("风格绑定已变 → " if _bind_changed else "角色卡已变更 → ")
                      + "定妆照按新配方重渲…")
                try:
                    from .character_sheets import render_missing
                    n_new, _ = render_missing(book_title, lambda m: _emit(j, m))
                    _emit(j, f"定妆照沉淀 ✓ 新渲{n_new}张 (角色设计台可看)", "bible_done")
                except Exception as exc:  # noqa: BLE001
                    _emit(j, f"定妆照自动重渲失败 (可去角色设计台手动): {exc}", "bible_done")
                j["status"] = "done"
            else:
                _emit(j, "圣经生成 ✓ (角色卡未变, 定妆照不动)", "bible_done")
                j["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            j["status"] = "error"
            j["error"] = str(exc)
            _emit(j, f"生成失败: {exc}", "bible_error")
            logger.exception("[style-bible] 生成失败")

    threading.Thread(target=_run, daemon=True, name="style-bible").start()
    return {"status": "running", "id": j["id"]}


def save_bible(book_title: str, bible: dict) -> dict:
    """手工修经落盘 (= 写缓存, plan 全系列复用; 手调优先于重掷).

    1010 补修 (圣经页配合收尾): 旧版全量覆盖 — 页面只传六项基础字段时,
    characters (角色卡)/era_anchor/道层 identity 等全部被抹 (0928 只修了重掷
    保卡路径, 手调保存这条路还在抹卡)。改为**合并语义**: 传入字段覆盖, 未传
    字段保留盘上旧值。"""
    if not (bible or {}).get("style_prefix"):
        raise ValueError("style_prefix 不可为空")
    from .director2 import _bible_cache_path
    p = _bible_cache_path(book_title)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, Any] = {}
    try:
        merged = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(merged, dict):
            merged = {}
    except Exception:  # noqa: BLE001 — 无旧缓存 = 纯新写
        merged = {}
    merged.update(bible)
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"saved": True}


def keep(book_title: str, style_name: str) -> dict:
    """留存 (0922 用户令): 这本书不用它, 但风格好 — 配方入库备用, 不绑本书不落选定."""
    p = _board_dir(book_title) / "board.json"
    board = json.loads(p.read_text(encoding="utf-8"))
    hit = next((s for s in board["styles"] if s["name"] == style_name), None)
    if not hit:
        raise KeyError(f"选择板无此风格: {style_name}")
    if not hit.get("is_new"):
        return {"book_title": book_title, "style": style_name, "kept": False,
                "note": "库成员本就在库, 无需留存"}
    ok = _persist_proposal(book_title, hit)
    if ok:
        hit["kept"] = True
        p.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"book_title": book_title, "style": style_name, "kept": ok}


def select(book_title: str, style_name: str) -> dict:
    """点选落绑定; 选中新提案配方 → 自动入库 (json + 首镜样张作参考图).

    1009 大坑修 (backlog ID-080): 绑定落**全配方快照** — 旧版只落风格名, 库卡跨书
    共用被别书 retune 改动会连带漂移, 且圣经生成只拿到一行 desc 自由发挥
    (塑料感"表面无纹理"药即从这来)。快照 = 书级冻结, resolve_style 优先吃它。
    """
    p = _board_dir(book_title) / "board.json"
    board = json.loads(p.read_text(encoding="utf-8"))
    hit = next((s for s in board["styles"] if s["name"] == style_name), None)
    if not hit:
        raise KeyError(f"选择板无此风格: {style_name}")
    # 新配方入库: 完整配方来自板上 提案.json
    if hit.get("is_new"):
        _persist_proposal(book_title, hit)
    # 全配方快照: 提案.json (新提案) → 风格库 (库成员), 取到才落
    recipe = None
    for src in (_board_dir(book_title) / "提案.json",
                _assets() / "风格库" / f"{_safe_name(style_name)}.json"):
        try:
            payload = json.loads(src.read_text(encoding="utf-8"))
            # 1012 修: 库卡=无 proposals 键的单配方 dict — 旧写法 .get("proposals")
            # 对它返回 None → 空搜索 → 库成员选定从未带上快照 (换绑国风chibi实锤,
            # 1009 存量快照是手工补的所以一直没炸)
            if isinstance(payload, dict) and "proposals" in payload:
                items = payload["proposals"]
            else:
                items = [payload]
            recipe = next((r for r in (items or [])
                           if isinstance(r, dict) and r.get("name") == style_name), None)
        except Exception:  # noqa: BLE001 — 源坏试下一个
            recipe = None
        if recipe:
            break
    binding = {"style": style_name, "note": "0922 风格选择板选定; 1009 起带全配方快照"}
    if recipe:
        binding["recipe"] = {k: recipe.get(k) for k in
                             ("name", "desc", "style_prompt_head", "style_prompt_tail",
                              "h3_style", "lora", "lora_strength", "page_overrides")
                             if recipe.get(k) is not None}
    _binding_path(book_title).write_text(
        json.dumps(binding, ensure_ascii=False, indent=1), encoding="utf-8")
    board["selected"] = style_name
    p.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"book_title": book_title, "style": style_name,
            "with_recipe": bool(recipe)}
