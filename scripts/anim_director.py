# -*- coding: utf-8 -*-
"""动画导演引擎 (0914 六原则架构) — 稿+TTS manifest → 叙事弧+运动设计+分镜规划.

垂直切片: 用 ep1 跑通全链, 产出 shots.json + QC 面板。
体外运行, 不接主产线; 跑通后接入。

六原则:
  1 音频先行    — manifest 是时间骨架, 镜头时长 = 音频实际时长
  2 运动驱动    — 先设计"什么运动在讲故事", 按运动节拍切镜
  3 双向增益    — 画面补充/放大/对比口播, 禁复述口播
  4 时间预算    — 镜数和运动复杂度装进音频时长的盒子
  5 序列内连贯  — 同一弧内色彩/风格/人物/时代感一致
  6 全片统一    — 一部片子一份美术圣经, 从头到尾一个视觉身份

用法:
  python scripts/anim_director.py --book 97ac3fd5... --ep 1
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

DB = PROJECT / "data" / "pipeline.db"
OUT_ROOT = PROJECT / "outputs" / "动画导演"


# ── 数据获取 ──────────────────────────────────────────────────

def load_script_and_manifest(book_id: str, ep: int) -> tuple[str, dict, str]:
    """从 DB 取确认稿 + 最新 audio job 的 manifest."""
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    # 稿
    r = con.execute(
        "select script_text, title from book_episodes where book_id=? and ep_index=?",
        (book_id, ep)).fetchone()
    if not r or not r["script_text"]:
        raise SystemExit(f"ep{ep} 无稿")
    script = r["script_text"]
    title = r["title"] or ""
    # manifest: 最新完成的 audio job
    j = con.execute(
        """select j.output_dir from audio_jobs j
        join scripts s on j.script_id = s.id
        join book_episodes be on s.id = be.script_id
        where be.book_id=? and be.ep_index=? and j.status in ('completed', 'stale')
        order by j.created_at desc limit 1""",
        (book_id, ep)).fetchone()
    con.close()
    if not j:
        raise SystemExit(f"ep{ep} 无 audio manifest (先跑 TTS)")
    mf_path = Path(j["output_dir"]) / "manifest.json"
    if not mf_path.exists():
        raise SystemExit(f"manifest 不存在: {mf_path}")
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    return script, manifest, title


# ── 模块 A: 美术圣经 ──────────────────────────────────────────

def derive_style_bible(book_title: str, soul: dict, audience: dict) -> dict:
    """书级资产: 一次生成全系列锁定."""
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.book_service.creation_common import _llm, _parse_json

    sys_p = (
        "你是动画短片的美术指导 (Art Director)。为一系列拆书动画短片制定美术圣经 — "
        "全系列从头到尾一个视觉身份, 像迪士尼动画一样统一。\n\n"
        "产出以下字段:\n"
        "- style_prefix: ComfyUI 提示词前缀 (英文, 逗号分隔, 定义整体风格; 每镜必带)\n"
        "- color_palette: 色彩体系 (主色/辅色/情绪色/禁用色, 中文描述)\n"
        "- texture: 线条与质感 (扁平/厚涂/水彩/3D 选一, 加描述)\n"
        "- character_policy: 角色出场策略 (如'少量出现, 只作配角点缀'或'主角贯穿')\n"
        "- text_graphic: 大字卡/数字卡与动画的融合方式 (底色/入场动画风格)\n"
        "- mood_keywords: 整体氛围关键词 (英文, 3-5 个)\n\n"
        "铁律: 风格选一种并锁死, 禁多风格混用; 色板贯穿全片; 质感统一。\n"
        '输出严格 JSON: {"style_prefix": "...", "color_palette": "...", '
        '"texture": "...", "character_policy": "...", "text_graphic": "...", '
        '"mood_keywords": "..."}'
    )
    l1 = ((audience or {}).get("L1") or {}).get("reader", "")
    user = (f"书名：《{book_title}》\n"
            f"核心：{soul.get('core', '')}\n"
            f"收益：{str(soul.get('gain', ''))[:150]}\n"
            f"目标观众：{l1[:80]}\n"
            f"系列集数：约 6-9 集, 每集 5 分钟
风格要求: 吉普力手绘水彩风 (Studio Ghibli hand-painted watercolor)")
    data = _parse_json(_llm().chat(sys_p, user, model="pro", temperature=0.4))
    if not data or not data.get("style_prefix"):
        return {}
    return data


# ── 模块 B: 叙事弧 + 运动设计 + 分镜规划 (核心引擎) ────────────

DIRECTOR_SYS = """你是顶级动画短片的导演 (Motion Director)。你的任务不是给台词"配插图"，
而是用运动和视觉变化来讲故事 — 画面是口播的增益搭档，不是复读机。

## 输入
- 稿件文本（带段落标签【A-B秒｜段名】）
- TTS manifest（每个音频段落的精确时长和文本, 含气口停顿）

## 你的工作流程（严格按序）

### 第一步: 读 manifest 建时间骨架
每个 manifest segment = 一个时间块。把时间块映射到稿件段落，
得到每个段落的精确起止时间。

### 第二步: 切叙事弧 (Narrative Arc)
一个叙事弧 = 一段完整的叙事意图（不是一句台词）。
例: "HBO 从 365 户起步到 90% 市场" 是一个弧（含 2-3 句台词）。
弧的边界 = 叙事意图的边界，不是句号。

### 第三步: 对每个弧设计运动叙事 (Motion Narrative)
用一句中文描述"什么运动在讲故事"：
- ✅ "HBO 标志从灰暗小店快速扩张铺满北美地图，40 年时间飞速流逝"
- ✅ "电视屏幕从烟雾信号逐代进化到卫星，每一次技术跳变画面闪切一次"
- ✅ "尼克松在电视上满头汗 vs 广播里的自信声音，同一人两种媒介的分裂"
- ❌ "一个男人站在电视台门口"（这是静态配图，不是运动叙事）

### 第四步: 在弧的时间预算内按运动节拍切镜
- 每镜 ≥2 秒（低于 2 秒观众看不清）
- 镜数 = floor(弧时长 / 3) 向上取整，封顶 4 镜
- 弧 ≤6 秒 → 1 镜（画面内有运动变化）
- 弧 6-12 秒 → 2 镜（前后对比或递进）
- 弧 12-20 秒 → 3 镜（起承转）
- 弧 >20 秒 → 4 镜（可分段叙事）

### 第五步: 每镜输出 keyframe + 运动指令
- keyframe_zh: 中文描述这一镜的"定格瞬间"画面
- motion_zh: 中文描述这一镜内的运动（相机怎么动、画面怎么变、什么在生长/消逝）
- 运镜: 推/拉/摇/移/跟/固定 选一

## 六条铁律（违反任何一条=废稿）

1. **音频先行**: 每镜的 t_start/t_end 必须精确对齐 manifest 时间块边界（可跨块组合，不可自造时间点）
2. **运动驱动**: keyframe 描述的是"运动中的定格帧"，不是"一个静态场景"— 每镜必须有 motion_zh
3. **双向增益**: 画面必须补充/放大/对比口播，禁复述口播（口播说"HBO很小"→画面给"小到地图上一个点都找不到"，不是画一个小房子）
4. **时间预算**: 弧内总镜时长 = 弧时长，不多不少
5. **弧内连贯**: 同一弧内所有镜头共享同一场景/主体/色调，只变角度/距离/时间，不换场景
6. **全片统一**: 所有镜头风格一致（美术圣经约束）

## 生图/生视频技术禁忌 (K2+H3 实测)

### K2 文字禁令 (生图模型画不了文字)
- keyframe_zh 里**禁止出现任何可读文字/数字/字母** — 屏幕留空或画抽象光点/线条
- 需要"365""90%"等数字？→ 写进 text_layer (后期叠加)，不画进图里
- 需要"HBO"字样？→ keyframe 画一个几何方块标志占位，真字由 text_layer 叠加
- 屏幕/海报/封面/标牌一律: "不可出现可读内容"

### H3 运镜约束 (快推快拉容易糊帧)
- 禁急推/急拉 ("猛然后拉"✗ → "缓慢拉远至..."✓)
- 运镜速度: 缓推/缓拉/缓慢平移/缓慢摇 — 每镜运动变化用"缓慢/逐渐/匀速"描述
- 快速变化靠**切镜**实现 (两镜之间快切), 不靠单镜内的快推拉

### text_layer (文字后期叠加)
每镜可附 text_layer 数组, 文字/数字/标题全部走这里:
  [{"kind": "title|caption|counter|typewriter", "text": "...", "t_start": 0.5, "t_end": 3.0}]
  t_start/t_end 是镜内相对时间 (0 = 镜头开始)
  kind: title=大字标题 / caption=字幕条 / counter=数字计数器 / typewriter=打字机

## 输出格式
严格 JSON:
{
  "arcs": [
    {
      "arc_id": "A1",
      "narrative": "一句话描述这个弧讲什么故事",
      "motion": "一句话描述什么运动在讲故事",
      "t_start": 0.0, "t_end": 4.9,
      "shot_count": 1
    }
  ],
  "shots": [
    {
      "shot_id": "S01",
      "arc_id": "A1",
      "t_start": 0.0, "t_end": 4.9,
      "narration": "这段音频的文本",
      "keyframe_zh": "定格瞬间的画面描述（中文，含光线/构图/主体/氛围；禁可读文字；色彩用名称不用色号）",
      "motion_zh": "这一镜内的运动描述（缓推/缓拉/平移/摇；禁急推急拉）",
      "camera": "缓推|缓拉|摇|移|跟|固定",
      "text_layer": [{"kind":"counter","text":"365","t_start":0.5,"t_end":2.0}]
    }
  ]
}
"""


def plan_shots(script: str, manifest: dict, bible: dict) -> dict:
    """模块 B: 叙事弧 + 运动 + 分镜规划."""
    from app.config import load_config, set_config
    set_config(load_config())
    from app.services.book_service.creation_common import _llm, _parse_json

    # 构造 manifest 摘要 (时间块 → 文本 → 时长)
    segs = []
    t_cursor = 0.0
    for s in manifest.get("segments") or []:
        dur = float(s.get("duration") or 0)
        txt = str(s.get("text") or "").strip()
        pause = float(s.get("breath_pause") or 0)
        segs.append({"t_start": round(t_cursor, 2), "t_end": round(t_cursor + dur, 2),
                     "duration": round(dur, 2), "text": txt[:120],
                     "breath_pause": pause})
        t_cursor += dur

    bible_block = ""
    if bible:
        bible_block = (f"\n【美术圣经 (全片统一, 每镜必须继承)】\n"
                       f"- 风格前缀: {bible.get('style_prefix', '')}\n"
                       f"- 色彩: {bible.get('color_palette', '')}\n"
                       f"- 质感: {bible.get('texture', '')}\n"
                       f"- 角色策略: {bible.get('character_policy', '')}\n"
                       f"- 氛围: {bible.get('mood_keywords', '')}\n")

    user = (f"【稿件】\n{script[:3000]}\n\n"
            f"【TTS manifest (时间骨架, 精确到 0.1s)】\n"
            + "\n".join(json.dumps(s, ensure_ascii=False) for s in segs)
            + bible_block)

    raw = _llm().chat(DIRECTOR_SYS, user, model="pro", temperature=0.5)
    data = _parse_json(raw) or {}
    if not data.get("shots"):
        raise SystemExit("导演产出不可解析 (无 shots)")
    return data


# ── 模块 D: 确定性校验 ─────────────────────────────────────────

def validate_shots(shots_data: dict, manifest: dict) -> list[str]:
    issues = []
    shots = shots_data.get("shots") or []
    arcs = shots_data.get("arcs") or []
    segs = manifest.get("segments") or []

    # 时间轴总长
    total_audio = sum(float(s.get("duration") or 0) for s in segs)
    if shots:
        shot_end = max(float(s.get("t_end") or 0) for s in shots)
        if abs(shot_end - total_audio) > 2.0:
            issues.append(f"末镜终点 {shot_end:.1f}s ≠ 音频总长 {total_audio:.1f}s (偏差 {abs(shot_end-total_audio):.1f}s)")

    # 连续性
    for i in range(1, len(shots)):
        prev_end = float(shots[i-1].get("t_end") or 0)
        cur_start = float(shots[i].get("t_start") or 0)
        if abs(prev_end - cur_start) > 0.5:
            issues.append(f"时间轴断裂: S{i}({prev_end:.1f}s) → S{i+1}({cur_start:.1f}s) 间隙 {abs(prev_end-cur_start):.1f}s")

    # 每镜时长
    for s in shots:
        dur = float(s.get("t_end") or 0) - float(s.get("t_start") or 0)
        if dur < 1.5:
            issues.append(f"{s.get('shot_id','?')} 时长 {dur:.1f}s < 1.5s (太短)")
        if dur > 25:
            issues.append(f"{s.get('shot_id','?')} 时长 {dur:.1f}s > 25s (太长, 应拆镜)")

    # 运动描述必填
    for s in shots:
        if not str(s.get("motion_zh") or "").strip():
            issues.append(f"{s.get('shot_id','?')} 缺运动描述 (motion_zh) — 违反运动驱动原则")

    # K2 文字禁令: keyframe 里禁可读文字/数字 (排除色号 #0E1620)
    _hex_strip = re.compile(r"#[0-9a-fA-F]{3,8}")
    _text_pat = re.compile(r"[a-zA-Z]{3,}|\d{3,}")
    for s in shots:
        kf = _hex_strip.sub("", str(s.get("keyframe_zh") or ""))
        hits = _text_pat.findall(kf)
        if hits:
            issues.append(f"{s.get('shot_id','?')} keyframe 含可读文字/数字 {'/'.join(hits[:3])} — K2 画不了文字, 走 text_layer")

    # H3 运镜: 禁急推急拉
    _fast_pat = re.compile(r"猛然|急速|快速|飞速|瞬间|突[然然]|闪电")
    for s in shots:
        mo = str(s.get("motion_zh") or "") + str(s.get("camera") or "")
        if _fast_pat.search(mo):
            issues.append(f"{s.get('shot_id','?')} 运动描述含急推/急拉关键词 — H3 快推快拉糊帧, 改缓推/缓拉/切镜")

    # 弧内连贯 (简单 bigram 检查: 同弧 keyframe 应共享关键词)
    arc_groups = {}
    for s in shots:
        arc_groups.setdefault(s.get("arc_id"), []).append(s)
    for arc_id, group in arc_groups.items():
        if len(group) < 2:
            continue
        kfs = [str(s.get("keyframe_zh") or "") for s in group]
        all_bigrams = [set(k[i:i+2] for i in range(len(k)-1)) for k in kfs]
        for i in range(1, len(all_bigrams)):
            overlap = len(all_bigrams[0] & all_bigrams[i]) / max(len(all_bigrams[0]), 1)
            if overlap < 0.05:
                issues.append(f"弧 {arc_id} 内 S{i} 与 S{i+1} 画面关键词重叠 {overlap:.0%} < 5% (可能换了场景)")

    return issues


# ── 模块 E: QC 面板 ────────────────────────────────────────────

def render_qc(shots_data: dict, issues: list, bible: dict, title: str, out: Path):
    arcs = shots_data.get("arcs") or []
    shots = shots_data.get("shots") or []
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>动画导演 QC — {title}</title><style>
body{{margin:0;padding:16px;background:#1a1a2e;color:#e2e8f0;font:14px/1.6 system-ui,"Microsoft YaHei"}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#64748b;margin-bottom:12px}}
.issue{{background:#7f1d1d;color:#fecaca;padding:6px 12px;border-radius:6px;margin:4px 0}}
.arc{{background:#16213e;border:1px solid #334155;border-radius:10px;padding:12px;margin:10px 0}}
.arc h3{{margin:0 0 4px;font-size:15px;color:#C9A25E}}
.arc .motion{{color:#93c5fd;font-size:.85rem;margin-bottom:8px}}
.shot{{background:#0f172a;border:1px solid #1e3a5f;border-radius:8px;padding:10px;margin:6px 0;display:flex;gap:12px}}
.shot .time{{min-width:70px;color:#64748b;font-size:.8rem}}
.shot .kf{{flex:1}} .shot .kf b{{color:#e2e8f0}}
.shot .mo{{color:#fbbf24;font-size:.82rem;margin-top:4px}}
.bible{{background:#1e1e3f;border:1px solid #4c1d95;border-radius:8px;padding:10px;margin:8px 0;font-size:.85rem}}
</style></head><body>
<h1>🎬 动画导演 QC — {title}</h1>
<div class="sub">{len(arcs)} 叙事弧 · {len(shots)} 镜 · {sum(float(s.get('t_end',0)-s.get('t_start',0)) for s in shots):.0f}s</div>
{''.join(f'<div class="issue">⚠ {i}</div>' for i in issues) if issues else '<div style="color:#22c55e;margin:6px 0">✅ 校验全过</div>'}
<div class="bible"><b>🎨 美术圣经</b><br>
风格: {bible.get('style_prefix','—')}<br>色彩: {bible.get('color_palette','—')}<br>
质感: {bible.get('texture','—')}<br>角色: {bible.get('character_policy','—')}<br>
氛围: {bible.get('mood_keywords','—')}</div>
"""
    for arc in arcs:
        html += f"""<div class="arc"><h3>{arc.get('arc_id','?')} · {arc.get('narrative','')}</h3>
<div class="motion">🏃 {arc.get('motion','')}</div>
<div class="sub">{arc.get('t_start',0):.1f}s – {arc.get('t_end',0):.1f}s · {arc.get('shot_count',0)} 镜</div>"""
        for s in shots:
            if s.get("arc_id") != arc.get("arc_id"):
                continue
            html += f"""<div class="shot">
<div class="time">{float(s.get('t_start',0)):.1f}–{float(s.get('t_end',0)):.1f}s<br>{float(s.get('t_end',0))-float(s.get('t_start',0)):.1f}s</div>
<div class="kf"><b>{s.get('shot_id','?')} [{s.get('camera','?')}]</b> {s.get('narration','')[:50]}<br>
<span style="color:#94a3b3">{s.get('keyframe_zh','')[:150]}</span>
<div class="mo">🏃 {s.get('motion_zh','')[:100]}</div></div></div>"""
        html += "</div>"
    html += "</body></html>"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


# ── 主流程 ─────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="动画导演引擎 (垂直切片)")
    ap.add_argument("--book", required=True, help="book_id")
    ap.add_argument("--ep", type=int, required=True, help="ep_index")
    ap.add_argument("--bible-only", action="store_true", help="只产美术圣经")
    args = ap.parse_args()

    from app.config import load_config, set_config
    set_config(load_config())

    script, manifest, title = load_script_and_manifest(args.book, args.ep)
    print(f"稿: {len(script)} 字 | manifest: {len(manifest.get('segments') or [])} 段")

    # 美术圣经
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    ij = json.loads(con.execute("select input_json from book_projects where id=?",
                                (args.book,)).fetchone()[0])
    con.close()
    soul = ij.get("灵魂三问") or {}
    audience = (ij.get("受众地图") or {}).get("value") or {}

    bible = derive_style_bible(
        ij.get("book_title") or title, soul, audience)
    if bible:
        print(f"圣经: {bible.get('style_prefix', '')[:60]}...")
    else:
        print("圣经生成失败, 跳过 (无前缀)")
        bible = {}

    if args.bible_only:
        return

    # 分镜规划
    print("导演规划中... (LLM)")
    shots_data = plan_shots(script, manifest, bible)
    arcs = shots_data.get("arcs") or []
    shots = shots_data.get("shots") or []
    print(f"产出: {len(arcs)} 弧 · {len(shots)} 镜")

    # 校验
    issues = validate_shots(shots_data, manifest)
    if issues:
        print(f"校验: {len(issues)} 问题")
        for i in issues[:5]:
            print(f"  ⚠ {i}")
    else:
        print("校验: 全过 ✓")

    # 输出
    bdir = re.sub(r'[\\/:*?"<>|\s]+', "_", ij.get("book_title") or title).strip("_")
    out_dir = OUT_ROOT / bdir / f"ep{args.ep}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "shots.json").write_text(
        json.dumps({"bible": bible, **shots_data}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    render_qc(shots_data, issues, bible, title, out_dir / "qc_plan.html")
    print(f"输出: {out_dir}")
    print(f"QC 面板: {out_dir / 'qc_plan.html'}")


if __name__ == "__main__":
    main()
