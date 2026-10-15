# -*- coding: utf-8 -*-
"""拆书账号人设种子 — 静读书 (2026-08-20) + 老谭读书 (2026-09-07) 幂等建账.

形象/音色不建记录: 运行时经既有 personas/roles/voices 面板绑定 (用户决策)。
静读书品牌: 静读书 / 印章 读书 / 标语 一期拆透一本好书。
老谭读书品牌: 老谭读书 / 印章 拆书 / 标语 读一本书，升一级。
  音色: host.default_voice_id 绑「老谭」(voice name 查询, 用户 0907 拍板不用大学教授)。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Host, Persona

logger = logging.getLogger(__name__)

__all__ = ["ensure_book_account", "ensure_laotan_book_account",
           "BOOK_PERSONA_KEY", "LAOTAN_BOOK_PERSONA_KEY",
           "LAOTAN_PACING", "get_pacing"]

BOOK_PERSONA_KEY = "jingshu"
LAOTAN_BOOK_PERSONA_KEY = "laotan-book"

# ── 人格节拍配置 (0930 重构: 个性化数值从代码搬进数据) ──
# 老谭读书的五分区节拍档: 分区映射(模块名→功能区)/气口/变速/拍点/单集时长。
# 全角 pattern ("拆解（三）") 与 episode_gen 弹性标签字节同源, 禁改写。
LAOTAN_PACING: dict = {
    "target_duration_sec": 300,
    "zone_map": [
        {"match": ["钩子", "回顾+引入"], "zone": "assess"},
        {"match": ["书里怎么说的", "拆解（三）"], "zone": "comment"},
        {"match": ["总结"], "zone": "follow"},
    ],  # 未匹配 → value
    "breath": {"assess": {"question": 0.5, "para": 0.8},
               "value": {"question": 0.8, "para": 1.0},
               "comment": {"question": 1.2, "para": 1.0},
               "follow": {"question": 1.0, "para": 1.2}},
    "tempo": {"assess": 1.08, "value": 1.0, "comment": 1.0, "follow": 0.94},
    "beats": {"enabled": True, "golden_line": 0.8, "before_identity": 0.6,
              "identity_prefix": "我是老谭"},
}


def get_pacing(db: Session, host: Host | None) -> dict | None:
    """host → config_json.pacing; 老谭 host 缺配置时懒种内置档 (一次性 upsert)。

    返回 None = 该人格无节拍配置 → 调用方保持无拍点/无分区行为 (静读书/新闻线现状)。
    """
    if not host:
        return None
    cfg = host.config_json if isinstance(host.config_json, dict) else {}
    if isinstance(cfg.get("pacing"), dict):
        return cfg["pacing"]
    if host.persona_key == LAOTAN_BOOK_PERSONA_KEY:
        merged = {**cfg, "pacing": LAOTAN_PACING}
        host.config_json = merged  # 新引用 — JSON 列同对象赋值不标 dirty
        db.add(host)
        db.commit()
        return LAOTAN_PACING
    return None

# 静姐人设: 温柔知性、闺蜜式共情, 面向 25-40+ 女性成长读者
PERSONA_NAME = "静读书"
PERSONA_TARGET_READER = ("抖音、小红书：25岁迷茫年轻女生 → 40+ 困境宝妈、职场女性；"
                         "痛点是中年内耗、婚姻委屈、角色捆绑、自我丢失；"
                         "讲成长是'历经生活，依然精致从容'，共情大于讲课")


def ensure_book_account(db: Session) -> Host:
    """幂等: 书账号 host+persona 存在即返回, 缺失则建."""
    host = db.query(Host).filter(Host.persona_key == BOOK_PERSONA_KEY).first()
    if host:
        return host
    host = Host(
        name=PERSONA_NAME,
        persona_key=BOOK_PERSONA_KEY,
        brand_name=PERSONA_NAME,
        stamp_name="读书",
        brand_tag="一期拆透一本好书",
    )
    db.add(host)
    db.flush()
    db.add(Persona(
        name=PERSONA_NAME,
        prompt_template="jingshu-book",
        host_id=host.id,
        brand_name=PERSONA_NAME,
        stamp_name="读书",
        brand_tag="一期拆透一本好书",
        # 目标读者画像 (账号人设级): 书级缺省继承, 录入书时不再强制人工补.
        target_reader=PERSONA_TARGET_READER,
    ))
    db.commit()
    logger.info("[book] 书账号人设已建: %s (%s)", PERSONA_NAME, host.id[:8])
    return host


# ── 老谭读书 (2026-09-07): 男性认知向拆书, 与静读书女频成长差异化 ──
LAOTAN_BOOK_NAME = "老谭读书"
LAOTAN_BOOK_TARGET_READER = ("抖音/B站中长视频：25-50 岁男性为主、有升职/升值愿望的职场人；"
                             "想升职加薪、想不可替代、关心人性规律与财富逻辑、怕认知掉队；"
                             "爱听'这本书用在我上班/带团队/做选择上是什么'，拆穿大于陪伴")
LAOTAN_BOOK_TAG = "读一本书，升一级"


def ensure_laotan_book_account(db: Session) -> Host:
    """幂等: 老谭读书 host+persona 存在即返回, 缺失则建 (音色绑「老谭」voice);
    存量 host 缺 pacing 时 upsert-merge 补种 (0930 — 绝不整体覆盖既有键)。"""
    host = db.query(Host).filter(Host.persona_key == LAOTAN_BOOK_PERSONA_KEY).first()
    if host:
        # 存量 host: 只做 pacing 补种 (幂等), 禁走建 Persona 流程 (唯一约束)
        if not (isinstance(host.config_json, dict) and "pacing" in host.config_json):
            host.config_json = {**(host.config_json if isinstance(host.config_json, dict) else {}),
                                "pacing": LAOTAN_PACING}
            db.add(host)
            db.commit()
        return host
    host = Host(
        name=LAOTAN_BOOK_NAME,
        persona_key=LAOTAN_BOOK_PERSONA_KEY,
        brand_name=LAOTAN_BOOK_NAME,
        stamp_name="拆书",
        brand_tag=LAOTAN_BOOK_TAG,
    )
    db.add(host)
    db.flush()
    host.config_json = {**(host.config_json if isinstance(host.config_json, dict) else {}),
                        "pacing": LAOTAN_PACING}
    # 音色: 绑现有「老谭」voice (ef6188, 新闻线同款; 不复制 voice 记录, 仅设 host 默认,
    # 不影响新闻线 — voice.host_id 保持 None 共享)
    from app.models import Voice
    laotan_voice = db.query(Voice).filter(Voice.name == "老谭").first()
    if laotan_voice:
        host.default_voice_id = laotan_voice.id
    db.add(host)
    db.flush()
    db.add(Persona(
        name=LAOTAN_BOOK_NAME,
        prompt_template="laotan-book",
        host_id=host.id,
        brand_name=LAOTAN_BOOK_NAME,
        stamp_name="拆书",
        brand_tag=LAOTAN_BOOK_TAG,
        target_reader=LAOTAN_BOOK_TARGET_READER,
    ))
    db.commit()
    logger.info("[book] 老谭读书账号人设已建: %s (%s, voice=%s)",
                LAOTAN_BOOK_NAME, host.id[:8],
                laotan_voice.name if laotan_voice else "未找到「老谭」voice, 音频页手选")
    return host
