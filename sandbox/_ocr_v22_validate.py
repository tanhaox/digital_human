# -*- coding: utf-8 -*-
"""v2.2 _ocr 全片验证: 对已完成视频重跑新管线, 与 v2.1 存档 sec_flags 对比脏秒一致率。
跑完恢复原 timeline (切片与之配套, 不动生产数据)。"""
import json, shutil, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.services import material_ingest_service as svc

STAGE = Path("data/materials/youtube")
vid = sys.argv[1] if len(sys.argv) > 1 else "GlkCofOyxUA"
tl = STAGE / vid / "timeline.json"
old = json.loads(tl.read_text(encoding="utf-8"))
bak = tl.with_suffix(".json.v21bak")
shutil.copy2(tl, bak)

class J:
    pass

j = J(); j.video_id = vid; j.entity = "RealLifeLore"; j.title = ""
t0 = time.perf_counter()
r = svc._ocr(j)
el = time.perf_counter() - t0

new = json.loads(tl.read_text(encoding="utf-8"))
o_flags, n_flags = old.get("sec_flags", ""), new.get("sec_flags", "")
n = min(len(o_flags), len(n_flags))
diff = sum(1 for i in range(n) if o_flags[i] != n_flags[i])
old_dirty, new_dirty = old.get("dirty_sec_list", []), new.get("dirty_sec_list", [])
both = len(set(old_dirty) & set(new_dirty))
print(f"耗时 {el/60:.1f}min | 旧脏秒 {len(old_dirty)} 新脏秒 {len(new_dirty)}")
print(f"逐秒位图一致率: {(n-diff)/n*100:.2f}% ({diff}/{n} 秒不同)")
print(f"脏秒集合交集: {both}, ±0容差召回 "
      f"{both/len(old_dirty)*100 if old_dirty else 100:.1f}%")
near = sum(1 for s in old_dirty if any(abs(s-t) <= 1 for t in new_dirty))
print(f"±1s召回: {near/len(old_dirty)*100 if old_dirty else 100:.1f}%")
print(f"干净窗: 旧{len(old.get('clean_windows', []))} 新{len(new.get('clean_windows', []))}")

shutil.copy2(bak, tl)  # 恢复原 timeline (配套切片不失效)
bak.unlink()
print("原 timeline 已恢复, .v21bak 已清")
