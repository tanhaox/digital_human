# -*- coding: utf-8 -*-
"""空镜退役执行段 (2026-09-02 用户令全修): 存量剥标签 + 词表包重建."""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import load_config, set_config  # noqa: E402
from app.database import init_db, get_session_maker  # noqa: E402
from app.models import VideoAsset  # noqa: E402

set_config(load_config())
init_db("sqlite:///data/pipeline.db")

db = get_session_maker()()
rows = db.query(VideoAsset).all()
stripped = 0
for r in rows:
    shots = r.shot_types or []
    if "空镜" in shots:
        r.shot_types = [s for s in shots if s != "空镜"]
        stripped += 1
db.commit()
print(f"存量剥空镜: {stripped} 行", flush=True)

from app.services.director_prompt._vocabulary import rebuild_vocabulary_pack
pack = rebuild_vocabulary_pack(db)
print("词表重建:", pack["stats"], flush=True)
print("shot_types 枚举:", pack["dimensions"]["shot_types"], flush=True)
print("keywords 前 10:", pack["dimensions"]["keywords"][:10], flush=True)
db.close()
