"""手动重建关键词词表包 (ID-034).

每次清洗/重新总结本地素材库、或新增素材后, 运行本脚本重建
``data/vocabulary_pack.json``, 使导演提示词的词表包包含最新可用画面词。

用法:
  cd digital_human
  .venv/Scripts/python.exe scripts/rebuild_vocabulary.py [--print]

选项:
  --print   输出包内容 JSON (便于人工审阅选词质量) 到 stdout

说明:
  - 读取真实配置 (config/app.yaml) 并初始化真实 DB (data/pipeline.db),
    只读素材元数据, 不改动任何素材文件。
  - 幂等: 重复运行仅覆盖包文件, 无副作用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许以任意 cwd 运行: 锚定到 digital_human 项目根
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_config, set_config  # noqa: E402
from app.database import get_session_maker, init_db  # noqa: E402
from app.services.director_prompt import rebuild_vocabulary_pack  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重建导演关键词词表包 (ID-034)")
    parser.add_argument(
        "--print", action="store_true",
        help="重建后把包内容打印到 stdout (便于审阅选词质量)",
    )
    args = parser.parse_args()

    # 加载真实配置并初始化 DB (词表包数据源)
    cfg = load_config()
    set_config(cfg)
    init_db(cfg.app.database_url)

    session = get_session_maker()()
    try:
        pack = rebuild_vocabulary_pack(session)
    finally:
        session.close()

    stats = pack.get("stats", {})
    print(f"词表包已重建: {pack.get('built_at')}")
    print(f"  素材数   : {stats.get('assets', 0)}")
    print(f"  总词数   : {stats.get('total_words', 0)}")
    print(f"  每维度数 : {json.dumps(stats.get('per_dim', {}), ensure_ascii=False)}")
    if args.print:
        print("\n" + json.dumps(pack, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
