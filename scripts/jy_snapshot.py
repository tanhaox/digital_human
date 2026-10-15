# -*- coding: utf-8 -*-
"""剪映草稿明文快照收割器 — 通用版.

用法:
    python scripts/jy_snapshot.py            # 收割草稿区全部已打开草稿的明文
    python scripts/jy_snapshot.py <草稿名>   # 只收割指定草稿

收割内容 (→ data/jy_template_snapshot/<草稿名>/):
  - Timelines/*/template.json     (剪映打开后生成的明文工作时间轴, 打开过才有)
  - 顶层 template.json(.bak)      (包内自带的明文镜像, 若幸存)
  - subdraft/ 全量                (复合片段实体, 从不加密)

G 盘原始包不会被此脚本触碰; 快照库是 J 线研究的唯一代码侧资产。
收割后可重跑 scripts/_scan_jy_effect_catalog.py 刷新效果目录。
"""
import glob
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DRAFTS = r"C:\Users\tanhaox\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
SNAP = r"F:\AI-Agent-Local\digital_human\data\jy_template_snapshot"


def snapshot_one(draft_dir: str) -> int:
    name = os.path.basename(draft_dir)
    dest = os.path.join(SNAP, name)
    n = 0
    # 1) Timelines 明文工作副本 (打开过的草稿才有)
    for p in glob.glob(os.path.join(draft_dir, "Timelines", "*", "template.json")):
        if open(p, "rb").read(1) in (b"{", b"["):
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(p, os.path.join(dest, "Timelines_template.json"))
            n += 1
    # 2) 顶层明文镜像 (包自带)
    for f in ("template.json", "template.json.bak"):
        p = os.path.join(draft_dir, f)
        if os.path.exists(p) and open(p, "rb").read(1) in (b"{", b"["):
            os.makedirs(dest, exist_ok=True)
            if not os.path.exists(os.path.join(dest, f)):
                shutil.copy2(p, os.path.join(dest, f))
            n += 1
    # 3) subdraft 全量 (从不加密)
    sub = os.path.join(draft_dir, "subdraft")
    if os.path.isdir(sub) and not os.path.isdir(os.path.join(dest, "subdraft")):
        os.makedirs(dest, exist_ok=True)
        shutil.copytree(sub, os.path.join(dest, "subdraft"))
        n += len(glob.glob(os.path.join(sub, "*", "draft_content.json")))
    return n


def main() -> None:
    os.makedirs(SNAP, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    targets = [os.path.join(DRAFTS, d) for d in sorted(os.listdir(DRAFTS))
               if os.path.isdir(os.path.join(DRAFTS, d))
               and (only is None or only in d)
               and not d.startswith(".")]
    total, taken = 0, 0
    for d in targets:
        try:
            n = snapshot_one(d)
        except Exception as exc:
            print(f"  !! {os.path.basename(d)}: {exc}")
            continue
        if n:
            taken += 1
            total += n
    print(f"收割 {taken} 个草稿, {total} 个明文文件 -> {SNAP}")
    print("刷新效果目录: python scripts/_scan_jy_effect_catalog.py")


if __name__ == "__main__":
    main()
