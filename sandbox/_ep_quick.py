# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from app.services.material_ingest_service import _new_ocr

o = _new_ocr()
print("det:", o.text_detector.infer.session.get_providers()[0])
print("rec:", o.text_recognizer.session.get_providers()[0])
