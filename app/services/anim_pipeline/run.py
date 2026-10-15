# -*- coding: utf-8 -*-
"""动画管线 v1 CLI — 模块入口 (0914 系统化平移至 app/services/anim_pipeline).

流程:
  1. plan    LLM 规划 → shots.json                     (零 GPU)
  2. k2      K2 批生图 (2ST 配方 ~15s/镜)               → img/*.png
  3. qc      生成 qc_sheet.html 人检接触表
  4. approve 人检回填 (--ok 过审 / --redo 打回换seed)
  5. h3      H3 批生视频 (节拍式 ~102s/10s镜)            → anim/*.mp4
  6. status  看板

用法 (系统内已有 web 工坊页 /api/anim, CLI 保留给命令行场景):
  python -m app.services.anim_pipeline.run plan --book f41d994d --ep 1
  python -m app.services.anim_pipeline.run k2 --book f41d994d --ep 1
  python -m app.services.anim_pipeline.run approve --book f41d994d --ep 1 --ok s01,s02 --redo s03
  python -m app.services.anim_pipeline.run h3 --book f41d994d --ep 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # digital_human 根 (包内绝对导入)

from app.services.anim_pipeline import h3 as h3_mod  # noqa: E402
from app.services.anim_pipeline import k2 as k2_mod  # noqa: E402
from app.services.anim_pipeline import planner, qc, tts as tts_mod  # noqa: E402
from app.services.anim_pipeline import shots as shots_mod  # noqa: E402


def _id_list(s: str | None) -> set[str] | None:
    if not s:
        return None
    return {x.strip() for x in s.split(",") if x.strip()}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 轮询噪音
    ap = argparse.ArgumentParser(description="动画管线 v1 (独立模块)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_book(p: argparse.ArgumentParser) -> None:
        p.add_argument("--book", required=True, help="书 UUID 前缀或书名子串")
        p.add_argument("--ep", required=True, type=int, help="集号")

    p_plan = sub.add_parser("plan", help="LLM 规划 → shots.json")
    add_book(p_plan)
    p_plan.add_argument("--teardown", default=None, help="拆解报告 md 追加给规划器 (可选)")
    p_plan.add_argument("--force", action="store_true", help="覆盖已有 shots.json")

    p_k2 = sub.add_parser("k2", help="K2 批生图 (planned 镜)")
    add_book(p_k2)
    p_k2.add_argument("--only", default=None, help="只跑指定镜 s01,s03")

    p_ext = sub.add_parser("extend", help="为段落扩镜 (不动已有镜头)")
    add_book(p_ext)
    p_ext.add_argument("--segment", required=True, help="段落标识: '44-111' 或标签子串")
    p_ext.add_argument("--count", type=int, default=2, help="补几个镜 (默认 2)")

    p_qc = sub.add_parser("qc", help="生成人检接触表 qc_sheet.html")
    add_book(p_qc)

    p_ap = sub.add_parser("approve", help="人检回填: --ok 过审 / --redo 打回(换seed)")
    add_book(p_ap)
    p_ap.add_argument("--ok", default=None, help="过审镜列表 s01,s02")
    p_ap.add_argument("--redo", default=None, help="打回镜列表 s03 (换 seed 重跑 k2)")
    p_ap.add_argument("--note", default="", help="打回理由")

    p_h3 = sub.add_parser("h3", help="H3 批生视频 (approved 镜)")
    add_book(p_h3)
    p_h3.add_argument("--only", default=None, help="只跑指定镜")
    p_h3.add_argument("--retry-failed", action="store_true", help="连 anim_fail 镜一起重试 (换 h3_seed)")
    p_h3.add_argument("--reroll", default=None, help="已 anim_done 镜重生成视频 (换 h3_seed), 如 s02,s05")

    p_tts = sub.add_parser("tts", help="TTS 先行: 2.5 逐镜合成 + 时间轴归真 + 漂移审计")
    add_book(p_tts)
    p_tts.add_argument("--only", default=None, help="只跑指定镜 s01,s03")
    p_tts.add_argument("--force", action="store_true", help="已有 wav 也重合成")
    p_tts.add_argument("--gap", type=float, default=None, help="镜间气口秒数 (默认 0.2)")
    p_tts.add_argument("--title-card", type=float, default=None, help="头部标题卡预留秒数 (默认 2.0)")

    p_st = sub.add_parser("status", help="看板")
    add_book(p_st)

    args = ap.parse_args()
    from app.services.anim_pipeline.config import load

    cfg = load()

    if args.cmd == "plan":
        planner.plan(args.book, args.ep, teardown_file=args.teardown, force=args.force)
        return
    if args.cmd == "extend":
        planner.extend_segment(args.book, args.ep, args.segment, args.count)
        return

    # 其余命令都要先定位 shots.json: --book 解析成书名
    book_id, book_title = planner.resolve_book(cfg.db_path, args.book)
    doc = shots_mod.load_doc(book_title, args.ep)

    if args.cmd == "k2":
        r = k2_mod.run_batch(doc, only=_id_list(args.only))
        print(f"k2 完成: ok={r['ok']} fail={r['fail']} → 下一步 qc 人检")
    elif args.cmd == "qc":
        out = qc.render(doc)
        print(f"接触表: {out}")
        print("浏览器逐镜检查 (文字/穿模/构图) 后跑 approve")
    elif args.cmd == "approve":
        ok_list = _id_list(args.ok)
        if args.ok and args.ok.strip().lower() == "all":
            ok_list = {s["shot_id"] for s in doc["shots"] if s["status"] == "img_done"}
        redo_list = _id_list(args.redo) or set()
        if not ok_list and not redo_list:
            raise SystemExit("--ok / --redo 至少给一个")
        for sid in ok_list | redo_list:
            shots_mod.find_shot(doc, sid)  # 先全量校验, 防半改
        for sid in ok_list:
            s = shots_mod.find_shot(doc, sid)
            if s["status"] != "img_done":
                print(f"跳过 {sid}: 状态 {s['status']} (只有 img_done 可过审)")
                continue
            shots_mod.transition(s, "approved")
        for sid in redo_list:
            s = shots_mod.find_shot(doc, sid)
            shots_mod.reject_for_reroll(s, args.note)
        shots_mod.save(doc)
        print(shots_mod.summary(doc))
    elif args.cmd == "h3":
        if args.reroll:
            for sid in _id_list(args.reroll) or set():
                s = shots_mod.find_shot(doc, sid)
                shots_mod.transition(s, "approved")  # 回到可跑态 (video_file 留旧值, 跑完覆盖)
                shots_mod.retry_anim(s)
            shots_mod.save(doc)
            print(f"reroll 就绪: {args.reroll}")
        r = h3_mod.run_batch(doc, only=_id_list(args.only), retry_failed=args.retry_failed)
        print(f"h3 完成: ok={r['ok']} fail={r['fail']} (fail 镜降级用静态图)")
    elif args.cmd == "tts":
        r = tts_mod.run_tts(doc, only=_id_list(args.only), force=args.force,
                            gap=args.gap, title_card=args.title_card)
        print(f"tts 完成: {r['synth']}+{r['skipped']} 镜音频, 全集成片 ≈ {r['ep_len_s']}s")
    elif args.cmd == "status":
        audio_n = sum(1 for s in doc["shots"] if s.get("audio_file"))
        print(shots_mod.summary(doc))
        print(f"audio: {audio_n}/{len(doc['shots'])} 镜已合成")
        print(f"目录: {shots_mod.ep_dir(book_title, args.ep)}")
        for s in doc["shots"]:
            err = f" | {s['error'][:80]}" if s.get("error") else ""
            aud = f" a={s['audio_dur_s']}s" if s.get("audio_dur_s") else ""
            print(f"  {s['shot_id']} [{s['page_type']}] {s['status']:10s} "
                  f"k2×{s['attempts']['k2']} h3×{s['attempts']['h3']}{aud}{err}")


if __name__ == "__main__":
    main()
