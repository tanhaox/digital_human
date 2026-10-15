# -*- coding: utf-8 -*-
"""料包注册表 + 料包使用总则 (0913 宪法 v1, 用户网页版四模块优选定稿).

菜单: 喂什么料、什么角色、谁产的 — 拼装与审计都以此为准。
台账: episode_gen 落盘 03_feeds.json (每包实喂字数), 反查零人肉。
"""
from __future__ import annotations

from pathlib import Path

# key: 03_user 块标记前缀 (台账扫描用); gen: 蒸馏代际
FEEDS: list[dict] = [
    {"key": "【樊登讲述参考", "name": "拐棍切片", "supplier": "樊登全书稿切片", "gen": 2,
     "role": "事实银行 — 铁证钉唯一来源 (数字+结局保真), 每钉≤25字, 禁整段转述"},
    {"key": "本集路线图", "name": "路线图JSON+字段料", "supplier": "总编剧", "gen": 3,
     "role": "骨架 — 结构/判定刻度/动作/秘籍; 字段里的故事引用=索引非讲稿"},
    {"key": "【全书底座", "name": "全书底座", "supplier": "蒸馏回填", "gen": 1,
     "role": "概念锚 — 只供概念名, 非内容源"},
    {"key": "【本集主题单元", "name": "主题单元卡", "supplier": "单元重切", "gen": 3,
     "role": "共鸣锚 — 职场场景钩的料"},
    {"key": "【本单元书内金句", "name": "金句/案例/实体", "supplier": "L0章节层", "gen": 1,
     "role": "引用库 — 金句可原引 (⟦⟧包裹)"},
    {"key": "【本集钩子·design", "name": "钩子design", "supplier": "facing-hooks", "gen": 3,
     "role": "备选钩子句/结尾互动/下集预告"},
    {"key": "前序覆盖清单", "name": "覆盖清单+上集尾部", "supplier": "产线状态", "gen": 0,
     "role": "咬合件 — 回顾段专用 (悬念回收)"},
]

# 料包使用总则 (拼在 user_p 顶部 — 给 LLM 的宪法)
REGULA_BLOCK = """【料包使用总则 · 优先级最高】
- 主脊柱 = 路线图字段料 (骨架/判定刻度/动作) + 拐棍切片 (事实银行)。
- 拐棍切片的用法 = 取钉: 每颗钉 ≤25字, 「数字+结局」双要素 (如"收视极高、零广告商、从未重播"),
  禁把切片任何一段整段转述进稿; 路线图字段里出现的故事 = 索引, 讲它去切片取钉。
- 单元卡读者共鸣 = 职场锚 (钩子/回顾可直取); 钩子design = 备选钩子句与下集预告。
- 冲突裁决: 路线图定结构, 切片定事实, 概念名全稿只出场一次。
- 你是老谭: 所有料过你的嘴重说, 任何料包原句禁直接成段。
"""


def load_ban_list() -> str:
    """持久负面清单 (只增不减) → 注入块; 文件缺失返回空串不阻断."""
    p = Path(__file__).resolve().parents[3] / "config" / "ban_list.txt"
    try:
        lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
    except Exception:
        return ""
    if not lines:
        return ""
    return "\n【持久负面清单 · 逐条必守 (ban_list, 校验器同步扫描)】\n" + "\n".join(lines) + "\n"


def feed_ledger(user_p: str) -> list[dict]:
    """03_user 台账: 每包实喂字数 (按注册表 key 定位块边界)."""
    pos = sorted([(user_p.find(f["key"]), f) for f in FEEDS if user_p.find(f["key"]) >= 0])
    out = []
    for j, (i, f) in enumerate(pos):
        end = pos[j + 1][0] if j + 1 < len(pos) else len(user_p)
        out.append({"name": f["name"], "role": f["role"][:14], "supplier": f["supplier"],
                    "gen": f["gen"], "chars": end - i})
    return out
