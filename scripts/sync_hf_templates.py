# -*- coding: utf-8 -*-
"""同步 HF v3 编辑纸墨模板: F仓 templates/hf_prep_v3/ → E盘 hf_template_root。

E:\\AI\\digital_human\\hf_prep 不在 git —— 模板源码以 F 仓为唯一事实源,
此脚本做增量复制部署 (仅新增/覆盖 v3 系目录, 不触碰旧黑金模板)。

用法:
    python scripts/sync_hf_templates.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("sync_hf_templates")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "templates" / "hf_prep_v3"
# 0910 修: 硬编码清单漏了 bs1_* 十三页型家族 (veil 改动同步不到 E 盘实锤) —
# 改为源目录全量扫描, 源里有什么同步什么 (目标侧旧黑金模板不在源里, 天然不触碰)
TEMPLATE_DIRS = None  # 动态: main() 里取 SRC_ROOT 全部子目录


def _target_root() -> Path:
    from app.config import get_config, load_config, set_config

    try:
        cfg = get_config()
    except RuntimeError:
        cfg = load_config()
        set_config(cfg)  # 脚本独立运行时 lifespan 未跑, 显式落全局
    return Path(cfg.defaults.hf_template_root)


def main(dry_run: bool = False) -> int:
    sys.path.insert(0, str(REPO_ROOT))
    dst_root = _target_root()
    if not SRC_ROOT.is_dir():
        logger.error("模板源缺失: %s", SRC_ROOT)
        return 1
    if not dst_root.is_dir():
        logger.error("hf_template_root 不存在: %s", dst_root)
        return 1

    copied: list[str] = []
    names = sorted(d.name for d in SRC_ROOT.iterdir() if d.is_dir())
    for name in names:
        src = SRC_ROOT / name
        if not src.is_dir():
            logger.warning("跳过缺失目录: %s", src)
            continue
        dst = dst_root / name
        for f in src.iterdir():
            if not f.is_file():
                continue
            target = dst / f.name
            action = "copy" if not target.exists() else "overwrite"
            copied.append(f"{action:9s} {name}/{f.name}")
            if dry_run:
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)

    for line in copied:
        logger.info(line)
    logger.info("完成: %d 个文件 → %s", len(copied), dst_root)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只列出动作不落盘")
    args = parser.parse_args()
    raise SystemExit(main(dry_run=args.dry_run))
