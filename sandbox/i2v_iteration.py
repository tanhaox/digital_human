# -*- coding: utf-8 -*-
"""i2v_iteration — 抽卡→迭代纪律工具 (2026-08-27, B-roll 沙盒层).

来源: docs/refs/hell-grind-aigc-skill-zh 失败诊断体系, 按 Wan2.2 意象 B-roll
形态裁剪 (无角色口播线: 身份/表演类裁掉, 加 Wan2.2 特有熔化/漂移类)。

三个纪律动作 (Hell Grind 原则: "结果不好"不是错误码; 先叫出症状; 两批同错
无改善就回责任层, 不换 seed 盲抽):

1. log   记一条生成结果 + tools/vision 自动 QC 失败分类 → JSONL
2. verdict 查看任务迭代状态 → 下一步建议 (继续/回责任层)
3. codes 失败码速查表

用法 (配合任意 ComfyUI 生成产物):
  python sandbox/i2v_iteration.py log <task_id> <video.mp4> --batch 1 --seed 42 --prompt-ver v1
  python sandbox/i2v_iteration.py verdict <task_id>

产线化时: 本模块迁 app/services, JSONL 换 DB 表 (DirectorSlot 已有
error_code/retry_count 字段, 码表直接复用)。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "sandbox" / "gen_iterations.jsonl"

# ── 失败码表 (Wan2.2 意象子集): code → (症状, 责任层, 最小修复) ──────────
# 责任层排序 = 诊断顺序: 首帧(资产) → 提示词 → 生成随机性 → 后期
FAILURE_CODES: dict[str, dict[str, str]] = {
    "F-FIRSTFRAME-DARK": {"symptom": "首帧太暗/主体不清晰, 运动无从展开",
                          "layer": "首帧", "fix": "Z-Image 重出首帧: 想动的光必须先显著存在 (兵器谱定稿)"},
    "F-FIRSTFRAME-CLUTTER": {"symptom": "首帧元素过多/主体占比过小",
                             "layer": "首帧", "fix": "首帧减元素, 主体占比加大 (视角收窄)"},
    "F-PROMPT-ADJ": {"symptom": "运动无因果, 画面随机飘 (提示词只有形容词)",
                     "layer": "提示词", "fix": "重写 motion 为物理行为: 重量/惯性/重力因果链"},
    "F-PROMPT-CONFLICT": {"symptom": "多个主运动互相争夺, 运动混乱",
                          "layer": "提示词", "fix": "只保留一个主运动, 其余写静止"},
    "F-PROMPT-JARGON": {"symptom": "镜头随机 (写了 mm 数/摄影术语没生效)",
                        "layer": "提示词", "fix": "FOV 结果化: 写画面里能看到什么"},
    "F-PROMPT-OVERDO": {"symptom": "特效/氛围过度强调致失真 (如冷气浓成雾霾) — 人审常用, llama 难辨",
                        "layer": "提示词", "fix": "该效果全稿只描述一次, 其余句删; 加针对性负向 (浓烟雾等)"},
    "F-MELT": {"symptom": "主体融化/边界粘连/纹理流失",
               "layer": "生成随机性", "fix": "同 batch 换 seed 复测; 仍熔 → 降运动幅度或回提示词"},
    "F-DRIFT": {"symptom": "主体漂移出画面/布局重构",
                "layer": "生成随机性", "fix": "换 seed; 仍漂 → motion 加'位置保持'锚"},
    "F-ARTIFACT": {"symptom": "多余肢体/伪影/物体穿透 (slop)",
                   "layer": "生成随机性", "fix": "换 seed + 提示词去 8K/masterpiece 类堆词"},
    "F-MOTION-NONE": {"symptom": "画面几乎静止, 呼吸感缺失",
                      "layer": "提示词", "fix": "加微运动指令 (光源缓慢移动/粒子飘落), 或接受走 zoompan 后期"},
    "F-STYLE-SLOP": {"symptom": "AI 味塑料感/过度锐化/色彩过饱和",
                     "layer": "后期", "fix": "route_post: ffmpeg 调色压饱和; 提示词删堆砌词"},
}

QC_PROMPT = """你是 AI 视频质检员。看这视频的首/中/尾帧，判断生成质量。只输出 JSON:
{"pass": true/false,
 "codes": ["从下列码中选0-2个, 无问题给空数组"],
 "note": "一句话证据"}
可选码 (症状定义):
""" + "\n".join(f"- {c}: {v['symptom']}" for c, v in FAILURE_CODES.items())


def _qc_video(video: str) -> dict:
    sys.path.insert(0, str(ROOT))
    from tools.vision import analyze_video_at
    dur = _duration(video)
    times = [0.2, dur / 2, max(dur - 0.3, dur / 2)]
    raw = analyze_video_at(video, times=times, prompt=QC_PROMPT)
    text = raw if isinstance(raw, str) else "".join(str(x) for x in raw)
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1])
        codes = [c for c in data.get("codes", []) if c in FAILURE_CODES]
        return {"pass": bool(data.get("pass")) and not codes, "codes": codes,
                "note": (data.get("note") or "")[:120]}
    except Exception:
        return {"pass": None, "codes": [], "note": f"QC 解析失败: {text[:100]}"}


def _duration(video: str) -> float:
    import subprocess
    out = subprocess.run(
        [r"C:\Programs\ffmpeg\bin\ffprobe.exe", "-v", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 5.0


def append_log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_task(task_id: str) -> list[dict]:
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("task_id") == task_id:
            rows.append(e)
    return rows


def verdict(task_id: str) -> dict:
    """纪律判定: 按 batch 聚合 → 同码连续两批无改善 = 触发回责任层."""
    rows = sorted(read_task(task_id), key=lambda r: r.get("batch", 0))
    batches: dict[int, list[dict]] = {}
    for r in rows:
        batches.setdefault(r["batch"], []).append(r)
    # 每个 batch 的"改善后残留码" = 该批所有 pass=false 条目的码并集
    residual: dict[int, set[str]] = {}
    for b, items in sorted(batches.items()):
        codes: set[str] = set()
        for it in items:
            qc = it.get("qc") or {}
            if qc.get("pass") is False:
                codes.update(qc.get("codes") or [])
        residual[b] = codes

    stop_advice: list[dict] = []
    bs = sorted(residual)
    for i in range(1, len(bs)):
        prev, cur = residual[bs[i - 1]], residual[bs[i]]
        stuck = prev & cur  # 连续两批同码
        for code in stuck:
            stop_advice.append({
                "code": code, **FAILURE_CODES[code],
                "evidence": f"批{bs[i-1]}与批{bs[i]}均出现",
                "action": f"停止抽卡 — 回[{FAILURE_CODES[code]['layer']}]层: {FAILURE_CODES[code]['fix']}",
            })
    return {"task_id": task_id, "batches": bs,
            "residual": {b: sorted(c) for b, c in residual.items()},
            "pass_batches": [b for b, c in residual.items() if not c],
            "stop_advice": stop_advice}


def main() -> None:
    ap = argparse.ArgumentParser(description="i2v 抽卡迭代纪律")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_log = sub.add_parser("log", help="记录一条生成结果 + 自动 QC")
    p_log.add_argument("task_id")
    p_log.add_argument("video")
    p_log.add_argument("--batch", type=int, required=True)
    p_log.add_argument("--seed")
    p_log.add_argument("--prompt-ver", default="v1")
    p_log.add_argument("--note", default="")
    p_ver = sub.add_parser("verdict", help="迭代状态与回责任层建议")
    p_ver.add_argument("task_id")
    sub.add_parser("codes", help="失败码速查")
    args = ap.parse_args()

    if args.cmd == "log":
        qc = _qc_video(args.video)
        entry = {"task_id": args.task_id, "batch": args.batch, "seed": args.seed,
                 "prompt_ver": args.prompt_ver, "video": args.video,
                 "qc": qc, "note": args.note, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        append_log(entry)
        print(json.dumps(entry, ensure_ascii=False, indent=1))
        v = verdict(args.task_id)
        if v["stop_advice"]:
            print("\n⛔ 迭代纪律触发 (两批同码无改善):")
            for a in v["stop_advice"]:
                print(f"  {a['code']} → {a['action']}")
    elif args.cmd == "verdict":
        print(json.dumps(verdict(args.task_id), ensure_ascii=False, indent=1))
    else:
        for c, v in FAILURE_CODES.items():
            print(f"{c:22s} [{v['layer']}] {v['symptom']} → {v['fix']}")


if __name__ == "__main__":
    main()
