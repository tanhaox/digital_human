"""提示词终串机检 (0927 外部 skill 蒸馏反哺 · 观察模式).

吸收 short-drama-director V6.9.5~V6.9.7 三条实拍教训 (实锤于 Seedance, 对
K2 同为"对否定不敏感、对词根敏感"的扩散模型同样成立):
  C19 禁令失效三改法 — 「没有地图」的词根"地图"留在提示词里就会被当画面词
    渲染; 高关联可画概念必须换词 (词根零出现), 低关联瑕疵才留否定式.
  C18a 首镜主体锚定 — 画面句无主体 → 模型渲染空场景.
  C20b 悬空指代 — 「这只手/那张脸」无先行词, 模型自补一个主体.

我方病根 (0927 产线审计实锤):
  ① k2.PROTECT_TAIL 旧版全否定式 ("没有地图、没有旗帜…"), 被禁词根常驻
    每一条 K2 提示词, 出图后靠 VLM 守门兜底 = 兜住之前已浪费抽卡;
  ② visual_ban_scan 扫的是 doc 字段, 不是最终拼装串 (拼装层: 风格头/
    COMPOSITION_BOOST/PROTECT_TAIL/_scene_eff 注入均不在其扫面).

执法口径: 观察模式 — 只 log + shot["prompt_gate"] 标记, 不阻断不自动改写.
禁区词命中本就跳卡的链路 (auto_fix_banned → political_flag) 不归本闸,
本闸管"终串里还有没有词根漏网 + 还在靠否定式防守的概念数".

用法:
    issues = scan(build_prompt(scene_eff, book), scene_zh=scene_eff,
                  book_title=book)
    if issues: logger.warning("[gate] %s %s", shot_id, brief(issues))
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_VISUAL_JSON = Path(__file__).resolve().parents[3] / "config" / "compliance_visual.json"

# 否定防守句式 (统计信号: 还在靠"没有X"防守的概念清单)
_NEGATION_RE = re.compile(r"(?:没有|并无|不见|不得出现|严禁出现|禁止出现)"
                          r"[^，。；、\s]{1,14}")

# 主体锚启发式: 环境句开头 + 全句无人物指涉 → 空场景风险 (C18a 软信号)
_SCENE_OPENERS = ("雨夜", "清晨", "黄昏", "夜空", "远处", "背景", "天空", "地平线",
                  "画面中只有", "空旷", "寂静")
_PERSON_RE = re.compile(r"[他她它]们?|人影|人物|身影|角色|一人|男子|女子|老者|青年|少年|孩童")


@lru_cache(maxsize=1)
def _hard_roots() -> frozenset[str]:
    """禁区词根集 = 词库 ban 原词 ∪ PROTECT_TAIL 旧版词根 (单真相源: 词库 json)."""
    roots: set[str] = set()
    try:
        data = json.loads(_VISUAL_JSON.read_text(encoding="utf-8"))
        for cat in (data.get("categories") or {}).values():
            for w in cat.get("ban") or []:
                if w:
                    roots.add(w)
    except Exception:  # noqa: BLE001,S110 — 词库读失败不炸闸, 退化为手工根集
        pass
    # PROTECT_TAIL 旧版否定句里的短词根 (词库长词的日常形态; 换词后终串应为零出现).
    # 0927 回测定口径: "旗帜"不入集 (词库真相源只拦 国旗/五星红旗; 《定位》小旗/
    # 彩旗/旗海是合法隐喻道具); "地图"保留 (K2 画风下任何地图有画成中国地图风险,
    # VLM 守门同款口径).
    roots.update(("地图", "国旗", "国徽", "军徽", "警徽", "党徽", "帽徽",
                  "领章", "肩章", "军装", "警服", "制式服装", "政治人物", "领导人"))
    return frozenset(roots)


# 同形异义豁免 (0927 回测实锤): 风格库配方 "16:9横幅构图" 是画幅描述, 词库
# "横幅" (text_bearing_props) 指带字横幅标语 — 上下文豁免, 不算词根命中
_ROOT_EXEMPT_RE = re.compile(r"(横幅)(?:构图|画幅|比例|画面)")


def _root_hits(root: str, text: str) -> int:
    """词根出现次数, 扣同形异义豁免 (如 '横幅构图')."""
    n = text.count(root)
    if root in ("横幅", "旗帜", "旗帜状"):
        n -= len(_ROOT_EXEMPT_RE.findall(text))
    return max(0, n)


def scan(final_prompt: str, *, scene_zh: str = "", book_title: str = "") -> dict:
    """扫最终拼装串 (发给 CLIPTextEncode 的同一份文本).

    返回 issues dict; 全净返回 {} (falsy, 调用方 `if issues:` 即可).
    """
    text = final_prompt or ""
    issues: dict[str, object] = {}

    # ① 硬信号: 禁区词根在终串任何位置出现 (换词法后应恒为零; 命中=拼装层漏网)
    hits = sorted(r for r in _hard_roots() if _root_hits(r, text) > 0)
    if hits:
        issues["hard_roots"] = hits

    # ② 软信号: 还在靠否定式防守的概念 (换词法候选清单, 灭字类实测有效可不改)
    neg_terms = _NEGATION_RE.findall(text)
    if len(neg_terms) >= 4:  # PROTECT_TAIL 灭字三连属正常水位, 超出=新否定句堆叠
        issues["negation_terms"] = neg_terms

    # ③ 软信号: 画面句主体锚缺失 (C18a — 空场景风险)
    scene = (scene_zh or "").strip()
    if scene:
        from . import k2  # 延迟导入 (k2 本模块也引 prompt_gate, 避免循环)
        first_clause = re.split(r"[，。；]", scene, maxsplit=1)[0]
        has_char = any(name in scene
                       for name, _look, _kws in k2._char_looks(book_title))
        if (first_clause.startswith(_SCENE_OPENERS) and not has_char
                and not _PERSON_RE.search(scene)):
            issues["subject_suspect"] = first_clause[:20]

    # ④ 软信号: 画面句超长 (增强器堆叠稀释主体权重; 增强链自身帽 700, 主链收紧)
    if len(scene) > 450:
        issues["scene_len"] = len(scene)

    return issues


def brief(issues: dict) -> str:
    """log 用一句话摘要."""
    parts = []
    if issues.get("hard_roots"):
        parts.append(f"词根漏网={issues['hard_roots']}")
    if issues.get("negation_terms"):
        parts.append(f"否定式防守×{len(issues['negation_terms'])}")
    if issues.get("subject_suspect"):
        parts.append(f"主体锚缺失(开头'{issues['subject_suspect']}…')")
    if issues.get("scene_len"):
        parts.append(f"画面句超长{issues['scene_len']}字")
    return "; ".join(parts) or "clean"
