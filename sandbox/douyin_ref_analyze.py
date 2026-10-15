# -*- coding: utf-8 -*-
"""抖音参考片拆解试点 — 口播×画面对齐分析 (沙盒, 产线外)

用法: python douyin_ref_analyze.py <视频路径>

链路(全本地):
  1. ffmpeg 抽音频 → faster-whisper medium → 口播分段+时间戳
  2. ffmpeg 场景切换检测 → 切点帧抽取(带时间戳); 兜底补均匀帧
  3. llama-server(8080, Qwythos-9B视觉) 单帧精描 → 画面/字幕/特效 JSON
  4. 时间轴对齐 → [t0-t1] 口播×画面 表格 + LLM 综合配合模式分析
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
LLAMA = "http://127.0.0.1:8080"

SYS_FRAME = """你是短视频单帧画面分析师。基于给出的一帧截图, 精确提取画面与后期信息。只输出JSON, 不要其他文字:
{
 "scene_type": "口播真人|真人画中画+Broll|Broll实拍|AI生成意象|图表数据|字幕卡|产品界面|其他",
 "subject": "画面主体(20字内), 有人注明出镜方式",
 "setting": "场景环境(15字内)",
 "text_on_screen": "画面上所有可见文字, 原样引用, 含样式注记(颜色/位置), 无字写无",
 "effects": "后期特效观察(25字内): 贴纸/光效/蒙版/转场痕迹/滤镜/包装元素",
 "visual_style": "色调质感(15字内): 纪录片/电影感/新闻/MG动画/生活流等"
}"""


def ensure_llama() -> None:
    try:
        urllib.request.urlopen(f"{LLAMA}/health", timeout=3)
        print("[llama] 已在运行")
    except Exception:
        print("[llama] 未运行 — 请先启动打标服务或 llama-server (E:/Llama-cpp-12)")
        raise SystemExit(1)


def transcribe(video: str, td: str) -> list[dict]:
    import faster_whisper
    audio = os.path.join(td, "a.wav")
    subprocess.run([FF, "-y", "-i", video, "-vn", "-ar", "16000", "-ac", "1", audio],
                   capture_output=True)
    model = faster_whisper.WhisperModel("medium", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, language="zh", vad_filter=True, beam_size=5)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
           for s in segs if s.text.strip()]
    print(f"[asr] {len(out)} 段")
    for s in out:
        print(f"  [{s['start']:6.2f}-{s['end']:6.2f}] {s['text']}")
    return out


def scene_frames(video: str, td: str, min_frames: int = 8) -> list[dict]:
    """场景切换检测抽帧(1起始编号) + 均匀网格兜底(近邻去重), 保证全片覆盖. 返回 [{ts, path}]"""
    r = subprocess.run(
        [FF, "-i", video, "-vf", "select='gt(scene,0.25)',scale=1024:-2,showinfo", "-vsync", "vfr",
         "-frames:v", "30", os.path.join(td, "cut_%03d.png")],
        capture_output=True, text=True)
    ts_list = [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    frames = [{"ts": round(t, 2), "path": os.path.join(td, f"cut_{i + 1:03d}.png")}
              for i, t in enumerate(ts_list)]
    print(f"[帧] 场景切换 {len(frames)} 处: {[f['ts'] for f in frames]}")

    # 均匀网格兜底 — 保证时间轴全覆盖(长镜头无切点段)
    dur = float(subprocess.run(
        [r"C:\Programs\ffmpeg\bin\ffprobe.exe", "-v", "quiet", "-show_entries",
         "format=duration", "-of", "csv=p=0", video],
        capture_output=True, text=True).stdout.strip())
    n = max(min_frames, 6)
    for i in range(n):
        t = round(dur * (i + 0.5) / n, 2)
        if any(abs(t - f["ts"]) < 0.4 for f in frames):
            continue  # 已有近邻切点帧
        p = os.path.join(td, f"uni_{i:02d}.jpg")
        subprocess.run([FF, "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                        "-vf", "scale=1024:-2", p], capture_output=True)
        frames.append({"ts": t, "path": p})
    frames.sort(key=lambda x: x["ts"])
    print(f"[帧] 兜底后共 {len(frames)} 帧, 覆盖 0-{dur:.1f}s")
    return frames


def llama_vision(img_path: str, system: str, user: str) -> str:
    b64 = base64.b64encode(open(img_path, "rb").read()).decode()
    payload = {
        "model": "qwythos-9b", "stream": False, "temperature": 0.2, "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": user}]}]}
    req = urllib.request.Request(
        f"{LLAMA}/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"]


def parse_json_sloppy(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {"raw": s[:200]}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"raw": s[:200]}


def main():
    video = sys.argv[1]
    ensure_llama()
    with tempfile.TemporaryDirectory() as td:
        segs = transcribe(video, td)
        frames = scene_frames(video, td)

        print("\n[视觉] 逐帧精描…")
        for f in frames:
            raw = llama_vision(f["path"], SYS_FRAME, "描述这一帧。只输出JSON。")
            f["desc"] = parse_json_sloppy(raw)
            d = f["desc"]
            print(f"  [{f['ts']:6.2f}] {d.get('scene_type', '?')} | {d.get('subject', '')}"
                  f" | 字幕:{str(d.get('text_on_screen', ''))[:20]}")

        # 时间轴对齐
        def seg_at(t):
            for s in segs:
                if s["start"] - 0.3 <= t <= s["end"] + 0.3:
                    return s["text"]
            best, bd = None, 9e9
            for s in segs:
                dist = min(abs(t - s["start"]), abs(t - s["end"]))
                if dist < bd:
                    best, bd = s, dist
            return best["text"] if bd < 2.0 else "(静默/音乐)"

        lines = ["| 时间 | 口播 | 画面 | 字幕/特效 |", "|---|---|---|---|"]
        for f in frames:
            d = f["desc"]
            lines.append(f"| {f['ts']:.1f}s | {seg_at(f['ts'])} "
                         f"| {d.get('scene_type','?')}: {d.get('subject','')} "
                         f"| {str(d.get('text_on_screen',''))[:30]} / {d.get('effects','')}")
        table = "\n".join(lines)

        # 综合分析(纯文本, 同一本地模型)
        payload = {
            "model": "qwythos-9b", "stream": False, "temperature": 0.4,
            "messages": [{"role": "user", "content":
                f"""你是短视频编导。以下是一条15秒级抖音视频的完整拆解数据。

口播转录(带时间):
{json.dumps(segs, ensure_ascii=False)}

关键帧画面描述(带时间):
{json.dumps([{k: f[k] for k in ('ts', 'desc')} for f in frames], ensure_ascii=False, default=str)}

请分析口播与画面的配合模式, 输出:
1. 剪辑节奏: 切换频率/段落结构
2. 画面类型分布: 口播/Broll/图表各占多少, 在什么口播内容时用什么画面
3. 配合规律: 口播的什么词性/内容触发画面切换(名词?数字?转折?)
4. 后期包装: 字幕/贴纸/特效的使用规律
5. 复刻要点: 如果要用AI生成+剪辑复刻这条视频的画面, 需要哪些素材和能力"""}]}
        req = urllib.request.Request(
            f"{LLAMA}/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        synthesis = json.loads(urllib.request.urlopen(req, timeout=300).read())["choices"][0]["message"]["content"]

    print("\n===== 时间轴 =====\n" + table)
    print("\n===== 配合模式分析 =====\n" + synthesis)

    out_md = Path("sandbox/reports") / (Path(video).stem + "_analysis.md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(f"# {Path(video).name} 拆解\n\n{table}\n\n## 配合模式分析\n\n{synthesis}\n",
                      encoding="utf-8")
    print(f"\n📄 已保存: {out_md}")


if __name__ == "__main__":
    main()
