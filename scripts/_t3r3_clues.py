# -*- coding: utf-8 -*-
"""T3R3CFtYUww 口播转写 + 来源线索扫描 (CPU, VPN 无关)."""
import re

import faster_whisper

model = faster_whisper.WhisperModel("medium", device="cpu", compute_type="int8")
segs, info = model.transcribe("data/materials/youtube/yt_T3R3CFtYUww.mp4",
                              language="zh", vad_filter=True, beam_size=5)
lines = [f"[{s.start:.0f}-{s.end:.0f}] {s.text}" for s in segs]
open("_t3r3_asr.txt", "w", encoding="utf-8").write("\n".join(lines))
print(f"{len(lines)} 段, 全文 {sum(len(l) for l in lines)} 字")

# 找来源线索: 提及厂商/来源用语/授权
clue_pat = re.compile(
    r"来自|根据|官方|授权|素材|来源|台积电|三星|英特尔|英伟达|AMD|日月光|安靠|"
    r"amkor|ASE|semiengineering|SEMICON|引用|改编|动画由|制作")
for line in lines:
    if clue_pat.search(line):
        print("线索:", line)
