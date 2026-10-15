# -*- coding: utf-8 -*-
"""分镜拼图草稿产线 (通用, 0913 定稿备用).

玩法: 分镜表 md → 剪映草稿。没素材的镜 = 制作位纯色占位卡 (动画系粉/剪辑系绿),
assets 目录里出现 SXX.mp4 / SXX.png 即自动替换该镜真实素材 (拼图式逐步完整),
voice.mp3 出现即挂口播轨。每次构建 = 新时间戳草稿 (不覆盖旧版) + build_history.jsonl 追加留痕。

用法:
  python scripts/storyboard_draft.py sandbox/reports/hbo_ep1_storyboard.md
  可选:
    --assets  DIR        素材目录 (默认 outputs/storyboard/<md名去后缀>)
    --name    NAME       草稿名前缀 (默认 <md名去后缀>_拼图)
    --typing-text TXT    打字卡文案 (优先级: 参数 > roadmap_json.打字卡 > 稿内"打字卡："行)
    --book ID --ep N     从 pipeline.db 取打字卡/校时参考
    --no-opening         不加固定开场套件 (默认加: 0-2s 打字卡+2-3s 定格+3-5.5s 书揭示件)

约定 (全系统通用, 不绑定某一本书):
  - 分镜表 = markdown 表, 列: 镜|时间(A-Bs)|台词摘要|画面|景别/运镜|制作位|组
  - 制作位关键词: Char/截屏替换/生图/模板卡/剪映 (组合位取首个, 即主制作位)
  - assets/SXX.mp4 > SXX.png/jpg > 纯色占位卡; assets/voice.mp3 → 口播轨
  - 素材短于槽位: 原速播放后留黑 (时间轴不挤, 下一镜照常准点)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from PIL import Image, ImageDraw, ImageFont

import pyJianYingDraft as draft_mod
from pyJianYingDraft import ClipSettings, TextSegment, TextStyle, trange

_US = 1_000_000

# 制作位 → (背景色, 文字色, 分类)  动画系粉 / 剪辑系绿
SLOT_STYLE = {
    "Char":     ("#E91E8C", "#FFFFFF", "动画·出镜"),
    "截屏替换": ("#FF7BAC", "#1C1C1C", "动画·替换"),
    "生图":     ("#FFC9DD", "#1C1C1C", "动画·生图"),
    "模板卡":   ("#00A860", "#FFFFFF", "剪辑·模板卡"),
    "剪映":     ("#96E6B8", "#1C1C1C", "剪辑·后期"),
    "打字卡":   ("#00A860", "#FFFFFF", "剪辑·打字卡"),
    "书封墙":   ("#0E7A4A", "#FFFFFF", "剪辑·书揭示"),
}
_SLOT_ORDER = list(SLOT_STYLE)
_FONT_DIR = Path(r"C:\Windows\Fonts")


def _font(size: int, bold: bool = True):
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    fp = _FONT_DIR / name
    if not fp.exists():
        fp = _FONT_DIR / "msyh.ttc"
    return ImageFont.truetype(str(fp), size)


def parse_storyboard(md: Path) -> list[dict]:
    """分镜表 md → shots; 兼容 G0/组列缺失."""
    pat = re.compile(
        r"^\|\s*(S\d+)\s*\|\s*([\d.]+)-([\d.]+)s\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*[^|]*\|\s*([^|]+)\|\s*([^|]*)\|")
    rows = []
    for line in md.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if not m:
            continue
        sid, t0, t1, dial, vis, slot, grp = m.groups()
        slot_key = next((k for k in _SLOT_ORDER if k in slot), "剪映")
        rows.append({"id": sid.strip(), "t0": float(t0), "t1": float(t1),
                     "line": dial.strip(), "visual": vis.strip(),
                     "slot": slot_key, "slot_raw": slot.strip(), "group": grp.strip()})
    if not rows:
        raise SystemExit(f"分镜表解析 0 镜, 检查格式: {md}")
    return rows


def resolve_typing_text(args, shots: list[dict]) -> str:
    """打字卡文案: 参数 > roadmap.打字卡 > 稿内行 > 占位."""
    if args.typing_text:
        return args.typing_text.strip().strip("。")
    if args.book and args.ep:
        try:
            con = sqlite3.connect(PROJECT / "data" / "pipeline.db")
            r = con.execute(
                "select roadmap_json, script_text from book_episodes where book_id=? and ep_index=?",
                (args.book, args.ep)).fetchone()
            con.close()
            if r:
                rm = json.loads(r[0] or "{}")
                if rm.get("打字卡"):
                    return str(rm["打字卡"]).strip().strip("。")
                m = re.search(r"打字卡[:：]\s*(.+)", r[1] or "")
                if m:
                    return m.group(1).strip().strip("。")
        except Exception as exc:
            print(f"[warn] DB 取打字卡失败: {exc}")
    return "打字卡文案占位（--typing-text 或 roadmap.打字卡 提供）"


def opening_beats(text: str) -> list[dict]:
    """固定开场套件 (86万赞 A族): 打字卡 → 定格 → 书封墙扫描 → 主书封收敛 (~5.5s).

    书揭示件 (S00c/S00d): 素材来自 prep_book_wall (epub 封面自动备料),
    无素材时走色卡占位; 飞入/扫描/收敛动画与音效 = 剪映后期位 (标注在卡上)。
    """
    return [
        {"id": "S00", "t0": 0.0, "t1": 2.0, "line": "（静默·打字音效）",
         "visual": f"打字卡逐字: {text[:14]}…", "slot": "打字卡",
         "slot_raw": "打字卡(底图+打字机)", "group": "G0"},
        {"id": "S00b", "t0": 2.0, "t1": 3.0, "line": "（静默）",
         "visual": "定格半拍", "slot": "打字卡", "slot_raw": "打字卡·定格", "group": "G0"},
        {"id": "S00c", "t0": 3.0, "t1": 4.2, "line": "（口播:报书名）",
         "visual": "书封墙: 多书封错峰飞入→白色扫描选框掠过 (动画:向上滑动/放大; 音效:翻页+扫光)",
         "slot": "书封墙", "slot_raw": "书封墙·飞入+扫描", "group": "G0"},
        {"id": "S00d", "t0": 4.2, "t1": 5.5, "line": "（口播:硬核宣言）",
         "visual": "收敛主书封·放大定格 (动画:渐渐放大; 音效:whoosh+嘭盖章)", "slot": "书封墙",
         "slot_raw": "书封墙·收敛锁定", "group": "G0"},
    ]


# ── 书揭示件备料: epub 封面提取 (书库自给, 不依赖外网) ──────────────
def _epub_cover(epub_path: Path) -> bytes | None:
    """epub 内嵌封面: OPF cover 引用优先, 兜底=前6图取最大竖图 (首图常是站标)."""
    import zipfile, io
    try:
        z = zipfile.ZipFile(epub_path)
        imgs = sorted(n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png")))
        if not imgs:
            return None
        # OPF 引用: meta[name=cover] / manifest properties=cover
        opf = next((n for n in z.namelist() if n.endswith(".opf")), None)
        if opf:
            import re as _re
            txt = z.read(opf).decode("utf-8", "ignore")
            m = _re.search(r'name="cover"\s+content="([^"]+)"', txt) or \
                _re.search(r'properties="[^"]*cover[^"]*"\s+href="([^"]+)"', txt) or \
                _re.search(r'href="([^"]+)"[^>]*properties="[^"]*cover[^"]*"', txt)
            if m:
                href = m.group(1).split("/")[-1]
                hit = next((n for n in imgs if href in n), None)
                if hit:
                    return z.read(hit)
        # 兜底: 前6图按 最大面积+竖版优先
        best, best_key = None, (0, 0)
        for n in imgs[:6]:
            try:
                im = Image.open(io.BytesIO(z.read(n)))
                key = (im.width * im.height, int(im.height >= im.width))
            except Exception:
                continue
            if key > best_key:
                best, best_key = n, key
        return z.read(best) if best else None
    except Exception:
        return None


def prep_book_wall(assets_dir: Path, book_title: str, source_dir: Path, n_wall: int = 5) -> list[str]:
    """备料书揭示件素材: 主书封 → S00d.png, 书封墙 → S00c.png (写进 assets 即被扫描为真素材).

    封面链: 本书 epub → 书库其他书 epub (墙=账号拆书史); 提不出就留空 (色卡占位兜底)。
    返回备料说明 (进 build_history 留痕)。
    """
    import io
    notes: list[str] = []
    src = Path(source_dir)
    if not src.exists():
        return [f"书源目录不存在: {src}"]
    epubs = {p.stem.split("(")[0].strip(): p for p in src.glob("*.epub")}
    main = next((p for k, p in epubs.items() if book_title[:6] in k), None)

    def _card_img(cover_bytes: bytes | None, w: int, h: int, bg: str) -> Image.Image | None:
        if not cover_bytes:
            return None
        im = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
        im.thumbnail((w, h))
        canvas = Image.new("RGB", (w, h), bg)
        canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
        return canvas

    # 主书封 → S00d
    if main:
        cover = _epub_cover(main)
        img = _card_img(cover, 1080, 1543, "#111111")  # 竖版放大定格
        if img:
            img.save(assets_dir / "S00d.png")
            notes.append(f"主书封←{main.name}")
    # 书封墙 → S00c (其他书封面 横排拼贴)
    others = [p for k, p in sorted(epubs.items()) if p is not main][:n_wall]
    tiles = [t for t in (_card_img(_epub_cover(p), 340, 486, "#1A1A1A") for p in others) if t]
    if len(tiles) >= 3:
        gap, total_w = 16, len(tiles) * 356 + 16
        wall = Image.new("RGB", (total_w, 518), "#0A0A0A")
        for i, t in enumerate(tiles):
            wall.paste(t, (16 + i * 356, 16))
        wall.save(assets_dir / "S00c.png")
        notes.append(f"书封墙←{len(tiles)}本: {'、'.join(p.stem[:10] for p in others[:len(tiles)])}")
    if not notes:
        notes.append("书库封面提取 0 件 — S00c/S00d 走色卡占位")
    return notes


def scan_assets(assets: Path) -> tuple[dict[str, Path], Path | None]:
    """SXX.mp4/png/jpg → 替换素材; voice.mp3 → 口播."""
    mapping: dict[str, Path] = {}
    voice = None
    if assets.exists():
        for p in sorted(assets.iterdir()):
            stem, ext = p.stem, p.suffix.lower()
            if stem == "voice" and ext in (".mp3", ".wav", ".m4a"):
                voice = p
            elif re.fullmatch(r"S\d+[a-z]?", stem) and ext in (".mp4", ".png", ".jpg", ".jpeg", ".mov"):
                mapping[stem] = p
    return mapping, voice


def _wrap(text: str, n: int) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def make_card(shot: dict, out: Path, note: str = "") -> None:
    bg, fg, cat = SLOT_STYLE[shot["slot"]]
    img = Image.new("RGB", (1920, 1080), bg)
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_sml = _font(110), _font(64), _font(40, bold=False)
    d.text((70, 60), f"{shot['id']}  {shot['group']}", font=f_big, fill=fg)
    d.text((1850 - d.textlength(cat, font=f_sml), 80), cat, font=f_sml, fill=fg)
    d.text((960 - d.textlength(shot["slot"], font=f_mid) / 2, 300),
           shot["slot"], font=f_mid, fill=fg)
    ts = f"{shot['t0']:.0f}-{shot['t1']:.0f}s ({shot['t1'] - shot['t0']:.0f}s)"
    d.text((960 - d.textlength(ts, font=f_mid) / 2, 430), ts, font=f_mid, fill=fg)
    for i, ln in enumerate(_wrap(shot["visual"], 26)[:3]):
        d.text((960 - d.textlength(ln, font=f_sml) / 2, 620 + i * 62), ln, font=f_sml, fill=fg)
    if note:
        d.text((70, 990 - 50), note, font=f_sml, fill=fg)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="分镜拼图草稿 (占位→逐步替换真实素材)")
    ap.add_argument("storyboard", help="分镜表 md 路径")
    ap.add_argument("--assets", help="素材目录 (默认 outputs/storyboard/<md名>)")
    ap.add_argument("--name", help="草稿名前缀")
    ap.add_argument("--typing-text", help="打字卡文案 (最高优先级)")
    ap.add_argument("--book", help="book_id (从 DB 取打字卡)")
    ap.add_argument("--ep", type=int, help="集号 (配合 --book)")
    ap.add_argument("--no-opening", action="store_true", help="不加 0-3s 蓝图静默开场")
    args = ap.parse_args()

    md = PROJECT / args.storyboard if not Path(args.storyboard).is_absolute() else Path(args.storyboard)
    stem = md.stem
    assets_dir = (PROJECT / args.assets if args.assets else PROJECT / "outputs" / "storyboard" / stem)
    assets_dir.mkdir(parents=True, exist_ok=True)

    shots = parse_storyboard(md)
    typing_text = ""
    _wall_notes: list[str] = []
    if not args.no_opening:
        typing_text = resolve_typing_text(args, shots)
        # 固定开场套件占 0-5.5s (打字卡+定格+书揭示), 原表整体后移
        _open_dur = opening_beats("x")[-1]["t1"]
        for s in shots:
            s["t0"] += _open_dur
            s["t1"] += _open_dur
        # 书揭示件备料: --book 时按书名从书库 epub 提封面 (写 assets 即成真素材)
        if args.book:
            _title = ""
            try:
                con = sqlite3.connect(PROJECT / "data" / "pipeline.db")
                r = con.execute("select book_title from book_projects where id=?",
                                (args.book,)).fetchone()
                con.close()
                _title = r[0] if r else ""
            except Exception as exc:
                print(f"[warn] DB 取书名失败: {exc}")
            if _title:
                try:
                    from app.config import load_config, set_config
                    set_config(load_config())
                    _src = Path(load_config().defaults.book_source_dir)
                except Exception:
                    _src = Path("G:/Desktop/畅销书")
                _wall_notes = prep_book_wall(assets_dir, _title, _src)
                for n in _wall_notes:
                    print(f"[wall] {n}")
        shots = opening_beats(typing_text) + shots
    for a, b in zip(shots, shots[1:]):
        if abs(a["t1"] - b["t0"]) > 1e-6:
            raise SystemExit(f"{a['id']}→{b['id']} 时间不连续 ({a['t1']} vs {b['t0']})")
    total = shots[-1]["t1"] - shots[0]["t0"]
    asset_map, voice = scan_assets(assets_dir)

    import os
    drafts_dir = Path(os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "JianyingPro", "User Data", "Projects", "com.lveditor.draft"))
    prefix = args.name or f"{stem}_拼图"
    name = f"{prefix}_{datetime.now().strftime('%m%d_%H%M')}"
    folder = draft_mod.DraftFolder(str(drafts_dir))
    script = folder.create_draft(name, 1920, 1080, allow_replace=True)
    script.append_tracks([
        draft_mod.TrackSpec(draft_mod.TrackType.video, "main"),
        draft_mod.TrackSpec(draft_mod.TrackType.text, "caption"),
        draft_mod.TrackSpec(draft_mod.TrackType.text, "typing"),
        draft_mod.TrackSpec(draft_mod.TrackType.audio, "voice"),
    ])

    cards_dir = drafts_dir / name / "cards"
    kinds: dict[str, str] = {}
    for s in shots:
        dur_us = int(round((s["t1"] - s["t0"]) * _US))
        start_us = int(round(s["t0"] * _US))
        real = asset_map.get(s["id"])
        if real is not None:
            mat = draft_mod.VideoMaterial(str(real))
            use_us = min(dur_us, int(mat.duration)) if mat.duration else dur_us
            if use_us < dur_us:
                # 短素材: 原速播放 → 槽位差额补警示卡 (时间轴不挤, 下一镜照常准点)
                rem = dur_us - use_us
                warn = cards_dir / f"{s['id']}_short.png"
                make_card(s, warn, note=f"⚠ 素材 {use_us / _US:.1f}s < 槽位 {dur_us / _US:.0f}s, 差 {rem / _US:.0f}s 补占位")
                script.add_segment(draft_mod.VideoSegment(mat, trange(start_us, use_us), volume=0), "main")
                script.add_segment(draft_mod.VideoSegment(
                    draft_mod.VideoMaterial(str(warn)),
                    trange(start_us + use_us, rem), volume=0), "main")
                kinds[s["id"]] = f"real:{real.suffix.strip('.')}(短{rem / _US:.0f}s)"
            else:
                script.add_segment(draft_mod.VideoSegment(mat, trange(start_us, use_us), volume=0), "main")
                kinds[s["id"]] = f"real:{real.suffix.strip('.')}"
        else:
            make_card(s, cards_dir / f"{s['id']}.png")
            script.add_segment(draft_mod.VideoSegment(
                draft_mod.VideoMaterial(str(cards_dir / f"{s['id']}.png")),
                trange(start_us, max(dur_us, 100_000)), volume=0), "main")
            kinds[s["id"]] = "card"
        if s["slot"] == "打字卡":
            continue  # 静默段无口播字幕
        line = s["line"] or "—"
        chunks = _wrap(line, 26)
        alloc = [dur_us * len(c) // max(len(line), 1) for c in chunks]
        alloc[-1] = dur_us - sum(alloc[:-1])
        seg_start = start_us
        for chunk, chunk_us in zip(chunks, alloc):
            try:
                script.add_segment(TextSegment(
                    chunk, trange(seg_start, max(chunk_us, 1000)),
                    clip_settings=ClipSettings(transform_y=-0.75)), "caption")
            except Exception as exc:
                print(f"[warn] caption 失败 {s['id']}: {exc}")
            seg_start += chunk_us

    # 打字卡大字: 0-2s 逐字前缀 (真打字机感) → 2-8s 定格 (蓝图 3-8s 断言大字定格)
    if typing_text:
        _ts = TextStyle(size=12, bold=True, align=0, max_line_width=0.82)
        _cs = ClipSettings(transform_y=0.2)
        chars = list(typing_text)
        step_us = int(2.0 * _US / len(chars))
        for i in range(1, len(chars) + 1):
            try:
                script.add_segment(TextSegment(
                    "".join(chars[:i]), trange((i - 1) * step_us, step_us),
                    style=_ts, clip_settings=_cs), "typing")
            except Exception as exc:
                print(f"[warn] typing 段失败 @{i}: {exc}")
        try:
            script.add_segment(TextSegment(
                typing_text, trange(2 * _US, 6 * _US), style=_ts, clip_settings=_cs), "typing")
        except Exception as exc:
            print(f"[warn] typing 定格段失败: {exc}")

    if voice is not None:
        amat = draft_mod.AudioMaterial(str(voice))
        vdur = int(amat.duration) if amat.duration else int(total * _US)
        script.add_segment(draft_mod.AudioSegment(str(voice), trange(0, vdur)), "voice")
    script.save()

    # 留痕: 每次构建追加一版 (迭代记录 → 稳定版本)
    n_real = sum(1 for v in kinds.values() if v.startswith("real"))
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "draft": name,
             "storyboard": str(md), "assets_dir": str(assets_dir),
             "shots": len(shots), "total_s": total, "real": n_real,
             "placeholder": len(shots) - n_real, "voice": bool(voice),
             "typing": bool(typing_text), "kinds": kinds,
             "book_wall": _wall_notes}
    hist = assets_dir / "build_history.jsonl"
    with hist.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[draft] {name}  →  {drafts_dir / name}")
    print(f"  {len(shots)}镜 {total:.0f}s | 真实素材 {n_real} / 占位 {len(shots) - n_real} | 口播 {'✓' if voice else '—'} | 打字卡 {'✓' if typing_text else '—'}")
    print(f"  素材目录: {assets_dir}  (放 SXX.mp4/png 即换, voice.mp3 挂口播)")
    print(f"  迭代留痕: {hist} (第 {sum(1 for _ in hist.open(encoding='utf-8'))} 版)")


if __name__ == "__main__":
    main()
