# -*- coding: utf-8 -*-
"""双维评审 CLI (1103 用户令): 推池7维×听感6维全过才算出厂. 库层在 book_service.dual_review.

用法: .venv/Scripts/python scripts/dual_review.py <稿文件路径> [概念1 概念2 ...]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.book_service.dual_review import all_pass, failed_items, review

if __name__ == '__main__':
    t = open(sys.argv[1], encoding='utf-8').read()
    concepts = sys.argv[2:] or None
    R = review(t, concepts)
    for name, detail, ok in R:
        print(f'{"✓" if ok else "✗"} {name}: {detail}')
    fails = failed_items(R)
    print(f'--- {len(R) - len(fails)}/{len(R)} 过 ---')
    if fails:
        print('未过:', ', '.join(fails))
    sys.exit(0 if all_pass(R) else 1)
