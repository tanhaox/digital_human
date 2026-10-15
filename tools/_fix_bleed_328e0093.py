# -*- coding: utf-8 -*-
"""离线修复 328e0093 (job 9193c63b) 的双向 bleed 受染行 — 产线同款逻辑。

复刻 tts_service.generate 的 _resynth_line (_synthesize_single + loudnorm),
调用升级后的 verify_pronunciation 双向扫描, 修复后回写 manifest.json 时长。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.config import load_config, set_config

set_config(load_config())

PROJ = Path(r"F:/AI-Agent-Local/digital_human/projects/20260907_012434_135834/audio")
VOICE = {
    "backend": "indextts",
    "voice_id": "大学教授·地缘版",
    "base_url_indextts": "http://127.0.0.1:7862",
    "ref_audio": Path(r"F:/AI-Agent-Local/digital_human/references/2267aeae-1acc-4738-8130-abfa4140caf7/anchor.wav"),
    "ref_text": "在当今这个信息爆炸的时代，财经资讯每天如潮水般涌来。很多朋友问我，老陈，这么多消息，哪些才是真正值得我们关注的？今天我就来给大家梳理一下最近的重要经济动态。首先，我们要要看的是宏观政策的走向。货币政策方面，央行近期释放出明确信号，要精准发力，支持实体经济发展。这意味着什么呢？简单来说，就是市场上不会缺钱，但也不会大水漫灌。对于普通投资者来说，这是一个重要的参考信号。",
    "master_audio": Path(r"F:/AI-Agent-Local/digital_human/references/2267aeae-1acc-4738-8130-abfa4140caf7/master_1785001934912.wav"),
    "master_text": "由此看，马云相关观点的重点也不应被理解成“先亏钱再挣钱”。那仍是旧互联网时代的算盘。AI时代的新问题是，平台能否让消费者把判断过程交给机器，一旦消费者形成这种习惯，企业获得的就不是传统忠诚度，而是替用户处理生活事务的授权，这种授权将成为比流量更稀缺的资源。",
    "params": {"speed": 1.0, "pitch": 0.0, "volume": 1.0, "master_style": "calm"},
}


def main() -> None:
    from scripts.tts_lib.orchestrator import _synthesize_single
    from scripts.tts_lib.text import _tts_text
    from app.services.tts_verify import verify_pronunciation

    manifest = json.loads((PROJ / "manifest.json").read_text(encoding="utf-8"))

    def resynth_line(idx: int, clean_line: str, marked_line: str) -> None:
        from scripts.tts_lib.lines import DEFAULT_FISH_URL, DEFAULT_F5_URL
        wav = PROJ / f"{idx:03d}.wav"
        tmp = PROJ / f"_verify_{idx:03d}.wav"
        _synthesize_single(
            text=_tts_text(marked_line, keep_breaks=True),
            output_path=tmp,
            backend=VOICE["backend"],
            voice_id=VOICE["voice_id"],
            reference_audio=VOICE["ref_audio"],
            reference_text=VOICE["ref_text"],
            base_url_fish=DEFAULT_FISH_URL,
            base_url_f5=DEFAULT_F5_URL,
            base_url_indextts=VOICE["base_url_indextts"],
            master_audio=VOICE["master_audio"],
            master_text=VOICE["master_text"],
            params=VOICE["params"],
        )
        from app.infrastructure.ffmpeg import normalize_audio
        try:
            normalize_audio(tmp, wav)
            tmp.unlink(missing_ok=True)
        except Exception:
            tmp.replace(wav)

    report = None
    # 走产线 GPU 服务托管 (按需拉起 7862 + 空闲自动关停), 不裸连 —
    # 离线直连在服务空闲关停后 WinError 10061 (run2 实锤)
    from app.services.gpu_service_manager import get_gpu_service_manager
    manager = get_gpu_service_manager()
    with manager.session(VOICE["backend"], status_callback=lambda m: print(f"[svc] {m}")):
        report = verify_pronunciation(
            output_dir=PROJ,
            manifest=manifest,
            resynth_line=resynth_line,
            on_event=lambda m, lv: print(f"[{lv}] {m}"),
        )
    print(f"\nchecked={report['checked']} fixed={len(report['fixed'])} "
          f"unresolved={len(report['unresolved'])}")

    # 回写 manifest 时长 (时间轴真理源)
    by_file = {f["file"]: f["duration"] for f in report.get("fixed", []) if f.get("duration")}
    touched = False
    for seg in manifest.get("segments", []):
        if seg.get("file") in by_file:
            seg["duration"] = by_file[seg["file"]]
            touched = True
    if touched:
        (PROJ / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print("manifest.json 时长已回写")


if __name__ == "__main__":
    main()
