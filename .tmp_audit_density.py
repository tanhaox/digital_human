# -*- coding: utf-8 -*-
"""一次性: K2 图画面密度审计 — 数值指标(全45) + GLM-4V-Flash 实描述(随机8) + 最爱图对照."""
import base64
import io
import json
import os
import random
import sys

import numpy as np
import requests
from PIL import Image

sys.path.insert(0, ".")

BASE = "outputs/动画/HBO的内容战略/ep1/img"
FAVORITE = "E:/AI/ComfyUI_windows_portable/ComfyUI/output/Krea2-2ST-20260911104907_00001_.png"
KEY = ""
for line in open(".env", encoding="utf-8"):
    if line.startswith("ZHIPU_API_KEY="):
        KEY = line.strip().split("=", 1)[1]


def metrics(path):
    """边缘密度 + 色彩熵 + 分层方差 (画面复杂度代理)."""
    im = Image.open(path).convert("RGB").resize((640, 360))
    a = np.asarray(im, dtype=np.float32)
    g = a.mean(axis=2)
    # Sobel 边缘 (零填充对齐)
    gx = np.zeros_like(g)
    gx[:, 1:] = np.abs(np.diff(g, axis=1))
    gy = np.zeros_like(g)
    gy[1:, :] = np.abs(np.diff(g, axis=0))
    edge = np.sqrt(gx ** 2 + gy ** 2).mean()
    # 色彩熵 (64 bin)
    q = (a // 64).astype(np.int32)
    idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    p = np.bincount(idx.ravel(), minlength=64) / idx.size
    p = p[p > 0]
    entropy = float(-(p * np.log2(p)).sum())
    return edge, entropy


def data_url(path, max_px=768):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def vlm(path):
    prompt = ("数一数画面里可辨识的独立元素(人物/家具/道具/装置/植物各算一个), "
              "并给画面丰富度评分(1=空旷冷清, 10=细节饱满但不乱)。"
              "输出一行 JSON: {\"elements\": 数字, \"score\": 数字, \"desc\": \"前景/中景/背景各一句\"}")
    r = requests.post(
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": "glm-4v-flash", "temperature": 0.2, "max_tokens": 300,
              "messages": [{"role": "user", "content": [
                  {"type": "image_url", "image_url": {"url": data_url(path)}},
                  {"type": "text", "text": prompt}]}]},
        timeout=120,
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    m = txt[txt.find("{"):txt.rfind("}") + 1]
    return json.loads(m)


# 1) 全量数值指标
rows = []
import pathlib
for p in sorted(pathlib.Path(BASE).glob("s*.png")):
    if "_" in p.stem:
        continue
    e, h = metrics(p)
    rows.append((p.stem, e, h))
fe, fh = metrics(FAVORITE)
es = [r[1] for r in rows]; hs = [r[2] for r in rows]
print(f"数值密度 (45镜 vs 最爱图):")
print(f"  边缘密度: 镜均值 {np.mean(es):.2f} (min {min(es):.2f} max {max(es):.2f}) | 最爱 {fe:.2f}")
print(f"  色彩熵:   镜均值 {np.mean(hs):.2f} (min {min(hs):.2f} max {max(hs):.2f}) | 最爱 {fh:.2f}")
rank = sorted(rows, key=lambda r: r[1])[:6]
print("  最空旷6镜(边缘密度倒序):", ", ".join(f"{r[0]}({r[1]:.2f})" for r in rank))

# 2) VLM 随机 8 + 最爱
random.seed(11)
sample = random.sample(rows, 8)
print()
print("VLM 实描述 (glm-4v-flash):")
v = vlm(FAVORITE)
print(f"  最爱参照: elements={v['elements']} score={v['score']} | {v['desc'][:70]}")
scores = []
for stem, _, _ in sample:
    try:
        v = vlm(f"{BASE}/{stem}.png")
        scores.append(v["score"])
        print(f"  {stem}: elements={v['elements']} score={v['score']} | {v['desc'][:70]}")
    except Exception as exc:
        print(f"  {stem}: VLM失败 {exc}")
print(f"\n  抽样评分均值 {np.mean(scores):.1f} (最爱 {v if False else ''}对照见上)")
