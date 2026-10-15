# -*- coding: utf-8 -*-
"""定点抽帧分析: 指定视频某秒前后多帧 → llama视觉精描 (复刻可行性评估用)"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

FF = r"C:\Programs\ffmpeg\bin\ffmpeg.exe"
LLAMA = "http://127.0.0.1:8080"

SYS = """你是短视频画面分析师。精确描述这一帧, 只输出JSON:
{"subject":"主体20字内","detail":"结构/材质/光影细节40字内","text_on_screen":"画面文字原样","effects":"后期/特效观察20字内","source_guess":"实拍/3D渲染/CGI混合/AI生成, 哪种+依据"}"""


def main():
    vid, t0 = sys.argv[1], float(sys.argv[2])
    offsets = [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [0, 0.5, 1.0]
    td = tempfile.mkdtemp()
    for off in offsets:
        t = t0 + off
        p = os.path.join(td, f"f{t}.jpg")
        subprocess.run([FF, "-y", "-ss", str(t), "-i", vid, "-frames:v", "1",
                        "-vf", "scale=1024:-2", p], capture_output=True)
        b64 = base64.b64encode(open(p, "rb").read()).decode()
        payload = {
            "model": "qwythos-9b", "stream": False, "temperature": 0.2, "max_tokens": 400,
            "messages": [
                {"role": "system", "content": SYS},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "描述这一帧,只输出JSON"},
                ]},
            ],
        }
        req = urllib.request.Request(
            f"{LLAMA}/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        raw = json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", raw, re.S)
        print(f"--- {t}s ---")
        print(m.group(0) if m else raw[:250])


if __name__ == "__main__":
    main()
