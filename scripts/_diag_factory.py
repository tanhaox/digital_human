# -*- coding: utf-8 -*-
"""诊断 _new_ocr NameError."""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
try:
    from app.services.material_ingest_service import _new_ocr
    ocr = _new_ocr()
    print("工厂 OK:", type(ocr).__name__)
except Exception as e:
    import traceback
    print("工厂失败:", type(e).__name__, str(e)[:200])
    traceback.print_exc()
