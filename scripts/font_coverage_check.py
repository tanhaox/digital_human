# -*- coding: utf-8 -*-
"""字体中文覆盖预检 (0915 用户令: 字体缺字提前测, 别等成片翻车).

原理: 艺术字体 (书法/手写) 大多只做 GB2312 常用字表 (6763 字), 缺字时剪映
自动回退默认体 → 风格错乱 ("似繁非繁"错形). 本工具扫字体 cmap, 提前报缺.

用法:
  python scripts/font_coverage_check.py --book "HBO的内容战略" --ep 1        # 本书文字语料
  python scripts/font_coverage_check.py --text "定价权 美剧之王 归零"        # 直接给字
  python scripts/font_coverage_check.py --book ... --fonts D:/某字体目录      # 指定字体目录
  (默认扫 C:/Windows/Fonts 的中文字体 + 可选剪映字体目录)

语料来源: shots.json 全部 text_layer + 口播 narration + 打字卡/品牌句 + 集标题。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from fontTools.ttLib import TTFont, TTLibError  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Windows 常见中文字体 (名字→文件)
WIN_CN_FONTS = {
    "微软雅黑": "msyh.ttc",
    "黑体(SimHei)": "simhei.ttf",
    "宋体(SimSun)": "simsun.ttc",
    "楷体(KaiTi)": "simkai.ttf",
    "仿宋(FangSong)": "simfang.ttf",
}


def collect_corpus(book: str | None, ep: int | None, text: str | None) -> str:
    """文字语料: 指定 text 或本书全部会出现在画面上的字."""
    parts: list[str] = []
    if text:
        parts.append(text)
    if book:
        shots_p = PROJECT_ROOT / "outputs" / "动画" / book / f"ep{ep or 1}" / "shots.json"
        if shots_p.exists():
            d = json.loads(shots_p.read_text(encoding="utf-8"))
            parts.append(str(d.get("ep_title") or ""))
            for s in d.get("shots", []):
                parts.append(str(s.get("narration") or ""))
                for tl in s.get("text_layer") or []:
                    parts.append(str(tl.get("text") or ""))
        else:
            print(f"(找不到 {shots_p} — 只测显式 text)")
    # 品牌句/打字卡 (config)
    try:
        from app.services.anim_pipeline.config import load
        cfg = load()
        for rule in cfg.brand_card.match_rules:
            parts.append("".join(rule))
    except Exception:
        pass
    return "".join(parts)


def font_chars(path: Path) -> set[str] | None:
    """字体 cmap 字符集 (ttc 取全部 face 并集)."""
    chars: set[str] = set()
    try:
        if path.suffix.lower() == ".ttc":
            from fontTools.ttLib import TTCollection
            coll = TTCollection(str(path), lazy=True)
            fonts = list(coll.fonts)
        else:
            fonts = [TTFont(str(path), lazy=True)]
        for f in fonts:
            for table in f["cmap"].tables:
                if table.isUnicode():
                    chars.update(chr(cp) for cp in table.cmap if cp >= 0x4E00)
    except Exception:
        return None
    return chars or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None)
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--text", default=None)
    ap.add_argument("--fonts", default=None, help="字体目录或单个字体文件 (默认扫系统中文字体)")
    args = ap.parse_args()

    corpus = collect_corpus(args.book, args.ep, args.text)
    cchars = sorted({c for c in corpus if "\u4e00" <= c <= "\u9fff"})
    if not cchars:
        print("语料里没有中文字 — 没什么可测")
        return
    print(f"语料: {len(corpus)} 字符, 去重中文 {len(cchars)} 字\n")

    targets: list[tuple[str, Path]] = []
    if args.fonts:
        p = Path(args.fonts)
        files = [p] if p.is_file() else sorted(p.glob("*.[to]tf")) + sorted(p.glob("*.ttc"))
        targets = [(f.stem, f) for f in files]
    else:
        sysdir = Path("C:/Windows/Fonts")
        targets = [(n, sysdir / fn) for n, fn in WIN_CN_FONTS.items() if (sysdir / fn).exists()]

    if not targets:
        print("没找到可测字体")
        return

    for name, path in targets:
        chars = font_chars(path)
        if chars is None:
            print(f"[跳过] {name}: 不是有效字体")
            continue
        missing = [c for c in cchars if c not in chars]
        if missing:
            print(f"[缺字] {name} ({path.name}, 中文 {len(chars)} 字): 缺 {len(missing)} 字 → {''.join(missing[:30])}")
        else:
            print(f"[全绿] {name} ({path.name}, 中文 {len(chars)} 字): 语料全覆盖 ✓")


if __name__ == "__main__":
    main()
