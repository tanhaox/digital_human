"""H3 视频产物机检 (0927 反哺实施计划 v2 P1-B · 观察模式).

补全产线最贵环节的零检查盲区: H3 出片 (~102s+/镜) 后无任何画面级验证 —
尤其 chain_h3 一条龙链 (单镜重规划→自动过审→直达视频) 上, K2 人检被机器
代过审 (anim_service.py:501-516), 视频层是"确认→装配"之间唯一没人看过的
环节。本闸给人多一个信号, 不替人决定 — 与 text_suspect 同构的标记交人检。

三类检查 (每镜抽 3 帧: 首部避淡入 / 中点 / 尾部避截断):
  ① 崩坏: 大面积黑屏/花屏噪点/撕裂/角色肢体严重畸变
  ② 文字残留: K2 灭字铁律的 H3 层兜底 — H3 动画层可能自造文字;
     本镜 text_layer 合法花字 (H3 渲染的设计层) 传白名单, 不算违规
  ③ 首帧漂移: 首抽帧 vs K2 原图水平拼图一次 VLM 对比 (老李"两张脸"同类病灶)

执法口径: 观察模式 — shot["video_suspect"] = {"kinds": [...], "what": "..."}
+ log, 不阻断 anim_done, 装配照常用真片; 人看标记决定重roll。VLM/ffmpeg
异常一律 fail-open (观察闸漏标只损失信号, 不影响产线)。

升级判据 (计划 v2 §P1-B): 连续 2 个新集真阳性 ≥3 镜且假阳性 <10% 才议
软阻断 (video_suspect 镜默认不 chain 进装配, 人一键放行); 永不自动重掷。

回测: python -m app.services.anim_pipeline.video_qc --backtest [--sample N]
"""
from __future__ import annotations

import json
import logging
import subprocess as _sp
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_FRAMES = 3  # 每镜抽帧数 (首部/中点/尾部)


def _duration(mp4: Path) -> float:
    out = _sp.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                   "-of", "csv=p=0", str(mp4)],
                  capture_output=True, text=True, timeout=15).stdout.strip()
    return float(out) if out else 0.0


def _extract_frames(mp4: Path, workdir: Path) -> list[Path]:
    """抽 3 帧: t≈0.2s(避淡入) / 中点 / t≈end-0.3s(避截断), 过短镜自动退位."""
    dur = _duration(mp4)
    if dur <= 0:
        return []
    ts = sorted({round(min(0.2, dur * 0.1), 2),
                 round(dur / 2, 2),
                 round(max(dur - 0.3, dur * 0.85), 2)})
    outs: list[Path] = []
    for i, t in enumerate(ts):
        out = workdir / f"f{i}_{t}s.png"
        r = _sp.run(["ffmpeg", "-v", "quiet", "-ss", str(t), "-i", str(mp4),
                     "-frames:v", "1", "-q:v", "3", "-y", str(out)],
                    capture_output=True, timeout=30)
        if r.returncode == 0 and out.exists():
            outs.append(out)
    return outs


def _vlm(image: Path, prompt: str, max_tokens: int = 96) -> str:
    import base64
    import io as _io

    from PIL import Image

    from app.services.reverse_prompt_service import _chat as _vlm_chat
    im = Image.open(image).convert("RGB")
    if im.width > 960:
        im = im.resize((960, int(im.height * 960 / im.width)))
    buf = _io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    # 注意: _chat 第三个位置参数是 system — max_tokens 必须关键字传
    # (实锤: 位置传参把 96 落进 system, API 400 invalid input)
    return _vlm_chat("data:image/jpeg;base64,"
                     + base64.b64encode(buf.getvalue()).decode(), prompt,
                     max_tokens=max_tokens)


def _text_whitelist(shot: dict) -> str:
    """本镜合法花字 (H3 渲染的设计文字层) — 已不参与判读 (口径两轮眼验后
    只查乱码), 留作日志/升级判读时的上下文源."""
    words = [str(t.get("text") or "").strip()
             for t in (shot.get("text_layer") or []) if t.get("text")]
    return "、".join(w for w in words if w)[:60]


def _frame_issues(png: Path) -> list[str]:
    """单帧查 ①崩坏 ②乱码伪文字 ③违禁画面 (1008 规层 R8).

    文字口径两轮眼验定案 (0927): ①白名单匹配 → H3 花字逐拍轮播片段对不上
    全句, 假阳性; ②载体判断 (悬浮艺术字=合法/贴物体=违规) → glm-4v-flash
    执行不稳, 66 镜抽测 9 命中眼验 9/9 全为合法花字, 假阳性 100%。
    终版口径: 只查「乱码伪文字」——似字非字、无法读通的模型自造假字
    (H3 真实风险通道); 一切可读汉字 (花字/招牌) 一律放行, 不在此闸判。
    ③违禁画面 (1008 规层 R8): K2 层只拦了首帧, H3 动画过程可能自造
    (烟/酒/赌/血) — 抽帧兜底查平台红线。"""
    prompt = ("仔细看这帧动画视频画面, 逐项检查: "
              "①画面崩坏: 大面积黑屏/花屏噪点/画面撕裂/角色肢体严重畸变 "
              "②乱码伪文字: 画面中出现似字非字、笔画错乱、无法读通的乱码伪字。"
              "注意: 凡是能读通顺的汉字 (任何大字/花字/招牌字) 都不是乱码, 一律不算。"
              "③违禁画面: 任何国旗国徽/地图/政治人物形象; 吸烟饮酒; 赌博 (扑克骰子"
              "老虎机); 暴力血腥 (血/伤口/断肢); 色情暴露; 毒品/注射器。"
              '只输出 JSON: {"issues": ["崩坏"/"乱码"/"违禁"], "what": "简述位置"} '
              '— 全无则 {"issues": [], "what": ""}')
    out = _vlm(png, prompt)
    issues = []
    for kw in ("崩坏", "乱码", "违禁"):
        if kw in (out or ""):
            issues.append(kw)
    return issues


def _drift(frame_png: Path, k2_png: Path) -> str | None:
    """首抽帧 vs K2 原图 拼图对比: 主体/场景是否自然延续 (1012 补衣色条款)."""
    import base64
    import io as _io

    from PIL import Image

    from app.services.reverse_prompt_service import _chat as _vlm_chat
    a = Image.open(k2_png).convert("RGB").resize((480, 270))
    b = Image.open(frame_png).convert("RGB").resize((480, 270))
    canvas = Image.new("RGB", (960, 270), (24, 24, 24))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (480, 0))
    buf = _io.BytesIO()
    canvas.save(buf, "JPEG", quality=80)
    out = _vlm_chat("data:image/jpeg;base64,"
                    + base64.b64encode(buf.getvalue()).decode(),
                    "左图=本镜首帧参考图, 右图=视频实际开头帧。判断右图是否左图"
                    "同一场景同一主体的自然延续: 允许轻微运动/镜头微移/光影变化; "
                    "不允许: 主体换成别人或别物/场景完全不同/画风突变/"
                    "主角服装颜色改变 (1012 衣色漂移条款)。"
                    '只输出 JSON: {"drift": true/false, "what": "简述差异"}',
                    max_tokens=96)
    if '"drift": true' in (out or "").replace(" ", ""):
        return "首帧漂移"
    return None


def _torso_hue_hist(png: Path) -> list[float]:
    """躯干中心区 12 桶色相直方图 (确定性, 供衣色漂移对比).
    框收紧到衬衫中心 (0.30-0.70 宽 / 0.55-0.92 高) — 框太宽红椅常量会稀释
    衬衫交换信号 (实锤: 宽框 Δmax=0.11 漏检)。"""
    import colorsys

    from PIL import Image
    im = Image.open(png).convert("RGB").resize((320, 180))
    px = im.load()
    hist = [0.0] * 12
    for y in range(99, 166, 2):
        for x in range(96, 224, 2):
            r, g, b = px[x, y]
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s > 0.3 and v > 0.25:
                hist[min(11, int(h * 360) // 30)] += 1
    n = sum(hist) or 1.0
    return [c / n for c in hist]


def _costume_drift(frame_png: Path, k2_png: Path) -> str | None:
    """衣色漂移确定性检测 (1012, 蓝衬衫→红实锤): 视频帧 vs K2 首帧躯干区
    色相直方图**逐桶差值** — 任一桶占比变化 ≥0.2 = 漂移。零 VLM (便宜模型
    对未放大小图不稳)。不用余弦: 常量背景 (红椅) 会撑高相似度掩盖对冲
    (实锤: 蓝红互换余弦仍 0.83)。"""
    a = _torso_hue_hist(k2_png)
    b = _torso_hue_hist(frame_png)
    if max(abs(x - y) for x, y in zip(a, b)) >= 0.2:
        return "衣色漂移"
    return None


def _flag_banner(frame_png: Path) -> str | None:
    """红旗/红横幅混合检测 (1012, 治 H3 自长红旗): 像素预筛上半屏高饱和红旗块
    (确定性, 便宜) → 命中才把**上半屏整块放大**问 VLM。
    注意别逐格放大: 红椅/红台灯等合法红元素会把"最浓格"带偏 (卓有实锤),
    上半屏整块放大是唯一稳定看到远处小旗的视角。"""
    import colorsys

    from PIL import Image
    im = Image.open(frame_png).convert("RGB").resize((640, 360))
    px = im.load()
    cells: dict[tuple[int, int], int] = {}
    for y in range(0, 200, 2):  # 上半屏 (天际线/窗外)
        for x in range(0, 640, 2):
            r, g, b = px[x, y]
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if (h < 0.035 or h > 0.965) and s > 0.62 and v > 0.38:
                cells.setdefault((x // 32, y // 32), 0)
                cells[(x // 32, y // 32)] += 1
    if not any(n >= 5 for n in cells.values()) and sum(cells.values()) < 100:
        return None
    crop = im.crop((0, 0, 640, 200)).resize((1280, 400))
    import io as _io2
    buf = _io2.BytesIO()
    crop.save(buf, "JPEG", quality=85)
    import base64 as _b64

    from app.services.reverse_prompt_service import _chat as _vlm_chat
    out = _vlm_chat("data:image/jpeg;base64,"
                    + _b64.b64encode(buf.getvalue()).decode(),
                    "这是画面上半屏的放大图 (城市天际线/窗外/室内上部)。里面有没有"
                    "红旗、红色旗帜、红色横幅或条幅? (橙黄窗灯/晚霞/红色台灯/"
                    "红色室内装饰/红椅不算)"
                    '只输出 JSON: {"flag": true/false, "what": "是什么"}',
                    max_tokens=64)
    if '"flag": true' in (out or "").replace(" ", ""):
        return "红旗"
    return None


def check(mp4: Path, shot: dict, doc: dict | None = None) -> dict:
    """检单镜 H3 产物. 返回 {"kinds": [...], "what": "..."}; 干净返回 {}.

    fail-open: 任何异常返回 {} (观察闸漏标只损失信号)。品牌镜/无图镜直接跳过。
    """
    try:
        if shot.get("brand_card"):
            return {}
        ep_dir = mp4.parent.parent
        k2_png = ep_dir / str(shot.get("image_file") or "")
        if not mp4.exists():
            return {}
        kinds: list[str] = []
        notes: list[str] = []
        with tempfile.TemporaryDirectory(prefix="video_qc_") as td:
            frames = _extract_frames(mp4, Path(td))
            if not frames:
                return {}
            for f in frames:
                for iss in _frame_issues(f):
                    if iss not in kinds:
                        kinds.append(iss)
                        notes.append(f"{iss}@{f.name}")
                fb = _flag_banner(f)  # 1012: 红旗混合检测 (像素预筛+放大VLM确认)
                if fb and fb not in kinds:
                    kinds.append(fb)
                    notes.append(f"{fb}@{f.name}")
            if k2_png.exists():
                # 1012: 首帧+中段各比一次 — 衣色漂移常在首拍后发生 (蓝衬衫0.5s变红实锤)
                _seen: set[int] = set()
                for idx in (0, len(frames) // 2):
                    if idx in _seen or idx >= len(frames):
                        continue
                    _seen.add(idx)
                    d = _drift(frames[idx], k2_png)
                    if d and d not in kinds:
                        kinds.append(d)
                        notes.append(f"{d}@{frames[idx].name}")
                cd = _costume_drift(frames[len(frames) // 2], k2_png)
                if cd and cd not in kinds:
                    kinds.append(cd)
                    notes.append(cd)
        if not kinds:
            return {}
        return {"kinds": kinds, "what": "; ".join(notes)[:200]}
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[video_qc] %s 检查异常 (放行): %s",
                       shot.get("shot_id"), exc)
        return {}


def writer_audit(doc: dict, ss: dict | None = None, bible: dict | None = None) -> list[str]:
    """单一写者执法 (1009 P4 收尾件, 设计 §6.1) — 可重放断言.

    非品牌、有创意槽、有法层对位的镜, keyframe_zh 必须满足其一:
      ① == assemble_k2_prompt 重放结果 (纯器层产物);
      ② 器层锚全在 (enhance 抛光过的合法变体 — 结构头 + look 括段存活).
    都不满足 = 有旁路写者动过 keyframe (巡检直写/手改/旧链残留) → 报告。
    fail-open: 无 ss/bible 或异常时返回 [] (观察闸)。
    """
    issues: list[str] = []
    try:
        if not ss:
            from .shooting_script import load as _load_ss
            ss = _load_ss(str(doc.get("book_title") or ""), int(doc.get("ep") or 0))
        if not ss:
            return []
        if bible is None:
            bible = doc.get("bible") or {}
        from .assembler import assemble_k2_prompt, fa_index
        from .director2 import _anchor_lost
        from .registers import author_of, world_visual
        by_sid = fa_index(doc, ss)
        arc_world = {str(a.get("arc_id") or ""): world_visual(doc, str(a.get("visual_world") or ""))
                     for a in doc.get("arcs") or []}
        for shot in doc.get("shots") or []:
            if shot.get("brand_card") or not shot.get("creative_slots"):
                continue
            # 1013 P0 双分支: 单写者镜断言 = 五件套齐 + 锚定链 (opening==keyframe_en)
            # + 作者围栏 (作者名只准引用镜) — 不做器层重放比对 (writer 本就不是器层产物)。
            if str(shot.get("writer_source") or "") == "writer":
                cur_zh = str(shot.get("keyframe_zh") or "").strip()
                cur_en = str(shot.get("keyframe_en") or "").strip()
                if not cur_zh or not cur_en:
                    issues.append(f"{shot.get('shot_id')} writer 镜五件套残缺 (keyframe_zh/en 空)")
                    continue
                od = str(((shot.get("anim") or {}).get("opening_desc")) or "").strip()
                if od != cur_en:
                    issues.append(f"{shot.get('shot_id')} writer 镜锚定链断 (opening_desc ≠ keyframe_en)")
                reg = str(shot.get("register") or "")
                _author = author_of(doc, bible)
                if (_author and reg != "author_cite" and _author in cur_zh
                        and not shot.get("political_flag")):
                    issues.append(f"{shot.get('shot_id')} writer 镜围栏违例 (作者名出现在非引用镜)")
                continue
            pair = by_sid.get(str(shot.get("shot_id")))
            if not pair:
                continue
            fa_shot, fa_scene = pair
            replay = assemble_k2_prompt(
                shot, fa_shot, fa_scene, bible,
                world_phrase=arc_world.get(str(shot.get("arc_id") or ""), ""))
            cur = str(shot.get("keyframe_zh") or "")
            if cur == replay:
                continue
            if not _anchor_lost(replay, cur):
                continue  # 抛光变体, 锚全在
            issues.append(
                f"{shot.get('shot_id')} 旁路写者: keyframe 非器层重放且丢锚 "
                f"(重放头: {replay[:30]}… ≠ 现值头: {cur[:30]}…)")
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[writer_audit] 审计异常 (放行): %s", exc)
    return issues


def _backtest(sample: int = 0) -> None:
    """存量回测: 全部 doc 的 anim_done 镜, --sample N 每集抽 N 镜."""
    from . import shots as shots_mod
    root = Path(shots_mod.anim_output_root())
    hit = total = 0
    kinds_all: list[str] = []
    for jp in sorted(root.glob("*/ep*/shots.json")):
        doc = json.loads(jp.read_text(encoding="utf-8"))
        base = shots_mod.ep_dir(doc["book_title"], doc["ep"])
        cands = [s for s in doc.get("shots") or []
                 if s.get("video_file") and not s.get("brand_card")]
        if sample:
            cands = cands[::max(1, len(cands) // sample)][:sample]
        for s in cands:
            mp4 = base / str(s["video_file"])
            if not mp4.exists():
                continue
            r = check(mp4, s, doc)
            total += 1
            if r:
                hit += 1
                kinds_all.extend(r["kinds"])
                print(f"  HIT {doc['book_title']} ep{doc['ep']} {s['shot_id']}: {r}")
        print(f"[backtest] {doc['book_title']} ep{doc['ep']} 累计 {total} 镜 / 命中 {hit}")
    print(f"\n== 回测汇总: {total} 镜, 命中 {hit} ({hit * 100 // max(total, 1)}%) ==")
    from collections import Counter
    print("== kinds 分布:", dict(Counter(kinds_all)))


if __name__ == "__main__":
    import sys
    _samp = 0
    if "--backtest" in sys.argv:
        if "--sample" in sys.argv:
            _samp = int(sys.argv[sys.argv.index("--sample") + 1])
        # 独立进程无 FastAPI lifespan — 手动装载配置 (VLM 通道依赖)
        from app.config import load_config, set_config
        set_config(load_config())
        logging.basicConfig(level=logging.WARNING)
        _backtest(_samp)
