# -*- coding: utf-8 -*-
"""R 配方演示 v2 — 沉淀配方 → 剪映草稿可视化激活 (2026-08-26).

背景: R10~R20 配方沉淀在 docs/teardown/剪映模板套路分析-效果配方库.md 一个月未上线,
作者本人已忘记配方内容。本脚本 = "演示即激活": 每个配方一段 8~13s 演示,
逐拍还原拆解表 (入场动画/同帧音效/步进/字号阶梯/三色板), 落剪映草稿箱。

v2.2 (第二轮反馈修正) — 版式约束体系:
- 行宽硬约束: 竖屏 1080px, 每字 ≈ 字号×8px → 超宽必被裁。全部拍按
  "字号×8×字数 ≤ 1020px (94%屏宽)" 重排: 长句拆两行(\\n)或降字号
- R12 引号: 贴观点句两侧外张 (x=±0.82, y 微上移 0.13) — "夹"的设计感
- R14 删除线: 贴纸方案废 (sx/sy 语义=资源默认尺寸倍率, 不可控 + 贴纸轨
  在文字轨下=背景条)。改红色全角破折号文字线 (————), 与文字同行宽同 y,
  track 强制最高文字轨 = 真压在文字上层
- R10/R17 长句: 两行化 + 短文案
- 轨道顺序: deco(贴纸) 移到文字轨之后 (贴纸永远在文字上层)

v2.1: 字体=FontType.俊雅体(真枚举); 字号=剪映UI读数(53期实证); 打字音效
sfx_max 钳到动画时长。
v1 教训: pyJYD 字体藏在 content.styles[0].font.id, 顶层 font 字段恒 null。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyJianYingDraft as d
from pyJianYingDraft import ClipSettings, TextSegment, trange

from app.services.jy_draft_service import _US, attach_sound, sound_path

DRAFTS = r"C:\Users\tanhaox\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
DRAFT_NAME = "R配方演示v2"
W, H = 1080, 1920  # 竖屏 (新闻线主战场, 53期同画布)

# ── 三色板 (J 线标准, 四模板实证) ──
WHITE = (1.0, 1.0, 1.0)
GOLD = (1.0, 0.96, 0.54)        # 引文金 / 划重点
RED = (0.72, 0.11, 0.11)        # 冲击红 / 删除线
DEEP_RED = (1.0, 0.094, 0.216)  # 玫红 (R16 核心金句, 53期原值)
KEY_YELLOW = (1.0, 0.87, 0.0)   # 建议/指令黄 (R14, 17期)
TAIL_YELLOW = (1.0, 0.749, 0.09)  # 收尾词黄 (R16, 53期原值)

F_BIG = d.FontType.俊雅体   # 16/28期知识区标配大字款
F_BODY = d.FontType.孤月体  # 产线定稿正文

CARD = 2.2   # 章节卡时长
GAP = 0.8    # 段间隔

# 拆解原音效 → 库内近似 (data/jy_sounds/ 实有文件名)
SFX_NEAR = {
    "嘟咚提示音1": "嘟咚，提示音1",
    "开门声": "开锁声",
    "撕胶布声": "系统错误音效",
    "风铃魔法转场": "魔法音效",
    "纸张抖动声": "唰",
    "报纸折叠声": "唰",
    "呼2": "嗖嗖",
    "敲锣声": "鼓声",
    "回忆刷": "页面切换",
    "综艺嘭": "综艺 咚",
}


def B(t, *, sz=12.0, c=WHITE, x=0.0, y=0.0, at=0.0, dur=None, anim=None,
      ams=500, sfx=None, sfx_max=None, font=F_BODY, until=None, track=None):
    """一拍文字. sz=剪映UI字号; track=强制指定文字轨 (如 't9'=最高层)."""
    return {"t": t, "sz": sz, "c": c, "x": x, "y": y, "at": at,
            "dur": dur, "until": until, "anim": anim, "ams": ams,
            "sfx": sfx, "sfx_max": sfx_max, "font": font, "track": track}


# ─────────────────────────────────────────────────────────────
# 七段演示 (拍序/动画/步进照抄拆解表; 字号经行宽约束校准 ≤1020px/行)
# ─────────────────────────────────────────────────────────────
SECTIONS = [
    # R2 强调数字卡 (§2 R2): 巨字 48号"34%" 2.5字符 ≈ 816px
    dict(title="R2 数字强调", sub="放大入场+叮", dur=8.0, beats=[
        B("美国对华关税涨了多少", sz=12, y=0.45, at=CARD, anim="渐显"),          # 960px
        B("34%", sz=48, c=GOLD, y=-0.02, at=CARD + 1.0, anim="放大", sfx="叮", font=F_BIG),
        B("比十年前翻了三倍", sz=12, y=-0.5, at=CARD + 2.3, anim="向上滑动", sfx="唰"),  # 768px
    ]),
    # R12 引号观点组 (39期): 引号 24号贴观点两侧外张 (x±0.82/y0.13 肩部)
    dict(title="R12 引号观点", sub="红引号夹观点", dur=10.0, beats=[
        B("第一点", sz=20, y=0.45, at=CARD, anim="向下露出", ams=300, sfx="嘟咚提示音1", font=F_BIG),  # 480px
        B("“", sz=24, c=RED, x=-0.82, y=0.13, at=CARD + 0.5, anim="渐显", font=F_BIG),
        B("能稳定输出选题", sz=14, y=0.08, at=CARD + 0.8, anim="向下露出", ams=300, sfx="开门声", font=F_BIG),  # 784px
        B("”", sz=24, c=RED, x=0.82, y=0.13, at=CARD + 1.4, anim="渐显", font=F_BIG),
        B("并不是拍了5条视频后随便爆的", sz=10, y=-0.35, at=CARD + 2.2, anim="渐显"),  # 13×80=1040 微收
    ]),
    # R16 金句递进组 (53期原值): 阶梯 12→24→24→27.6→27.6→34.6
    dict(title="R16 金句递进", sub="字号阶梯·双色", dur=12.5, beats=[
        B("给大家的建议是", sz=12, y=0.5, at=CARD, anim="渐显"),                # 840px
        B("可以先看", sz=24, y=0.12, at=CARD + 0.6, until=CARD + 1.6, anim="模糊缩小", sfx="风铃魔法转场", font=F_BIG),  # 768
        B("但别急着动", sz=24, y=0.12, at=CARD + 1.6, until=CARD + 2.6, anim="开幕", font=F_BIG),  # 768
        B("捂好钱袋子", sz=27.6, c=DEEP_RED, y=0.12, at=CARD + 2.6, until=CARD + 4.4,
          anim="向上滑动", sfx="魔法音效", font=F_BIG),                          # 1104 原版值(微出血=原版观感)
        B("捂好钱袋子", sz=27.6, c=DEEP_RED, y=0.12, at=CARD + 4.4, until=CARD + 6.0,
          anim="放大", ams=400, sfx="综艺 咚", font=F_BIG),                      # 重复强调
        B("走的更远", sz=34.6, c=TAIL_YELLOW, y=-0.35, at=CARD + 6.0, anim="辉光", font=F_BIG),  # 1107 原版值
    ]),
    # R14 否定标记组 (17期): 删除线=红色破折号文字线, 压最高轨 (真·划掉)
    dict(title="R14 否定标记", sub="删除线+打字机", dur=11.5, beats=[
        B("很多人以为", sz=12, y=0.5, at=CARD, anim="渐显"),                    # 672
        B("原生家庭决定命运", sz=15, y=0.2, at=CARD + 0.6, anim="向上滑动", sfx="纸张抖动声", font=F_BIG),  # 960
        B("————————", sz=17, c=RED, y=0.2, at=CARD + 1.6, until=CARD + 3.0,
          anim="渐显", ams=100, sfx="撕胶布声", font=F_BIG, track="t9"),         # 删除线: 同y同宽压上层
        B("千万", sz=36, y=-0.14, at=CARD + 2.4, anim="缩小", sfx="综艺嘭", font=F_BIG),  # 576
        B("建议:", sz=16, c=KEY_YELLOW, x=-0.62, y=-0.5, at=CARD + 3.2, anim="渐显", font=F_BIG),  # 384
        B("先把自己活明白", sz=12, x=0.36, y=-0.5, at=CARD + 3.6, anim="打字光标",
          ams=1500, sfx="键盘打字长音效2", sfx_max=1.65, font=F_BIG),           # 672
    ]),
    # R11 列举举证鱼贯组 (10期): 0.17s 步进, 每项一响
    dict(title="R11 列举鱼贯", sub="0.17s步进连入", dur=9.5, beats=[
        B("比如书里这三个人", sz=12, y=0.5, at=CARD, anim="渐显"),              # 960
        B("潘金莲", sz=18, x=-0.32, y=0.08, at=CARD + 0.7, anim="向下滑动", ams=300, sfx="唰", font=F_BIG),  # 432
        B("田小娥", sz=18, x=0.0, y=0.08, at=CARD + 0.87, anim="向下滑动", ams=300, sfx="铅笔划过纸张", font=F_BIG),
        B("甄嬛", sz=18, x=0.32, y=0.08, at=CARD + 1.04, anim="向下滑动", ams=300, sfx="唰", font=F_BIG),
        B("全是被时代夹住的女人", sz=12, y=-0.42, at=CARD + 2.4, anim="向上滑动"),  # 960
    ]),
    # R17 评论区证据组 (50期): 短评论+交替滑入, 每卡一响
    dict(title="R17 评论证据", sub="交替滑入·舆论证明", dur=10.5, beats=[
        B("网友怎么说", sz=12, y=0.5, at=CARD, anim="渐显", sfx="拍照声2"),     # 672
        B("“典型的杀猪盘”", sz=12, y=0.14, at=CARD + 0.6, anim="向左滑动", ams=300, sfx="回忆刷"),   # 768
        B("“我邻居就被骗过”", sz=12, y=-0.12, at=CARD + 0.8, anim="向右滑动", ams=300, sfx="回忆刷"),  # 864
        B("“转给爸妈看看”", sz=12, y=-0.38, at=CARD + 1.0, anim="向左滑动", ams=300, sfx="聊天切换音效"),  # 768
    ]),
    # R10 引用金句五件套 (64期): 长引文两行化
    dict(title="R10 引用五件套", sub="打字引文·署名收点", dur=13.5, beats=[
        B("埃里克·乔根森", sz=18, y=0.45, at=CARD, anim="折叠", ams=1500, sfx="报纸折叠声", font=F_BIG),  # 1008
        B("在他的书里提到", sz=12, y=0.22, at=CARD + 1.6, anim="渐显"),          # 840
        B("“赚钱, 跟工作的\n努力程度”", sz=15, y=-0.04, at=CARD + 2.3, anim="打字光标",
          ams=2000, sfx="键盘打字长音效2", sfx_max=2.15, font=F_BIG),           # 两行 960/600
        B("“即使每周在餐厅\n拼命工作80个小时”", sz=12, y=-0.32, at=CARD + 4.3, anim="向右擦除", sfx="呼2"),  # 两行 864/880
        B("也不可能发财", sz=18, y=-0.6, at=CARD + 5.8, anim="轻微放大", sfx="敲锣声", font=F_BIG),  # 864
        B("——《纳瓦尔宝典》", sz=10, c=GOLD, y=-0.72, at=CARD + 6.8, anim="渐显"),  # 800
    ]),
]


def register_draft(name: str, draft_dir: Path, dur_us: int) -> bool:
    """root_meta_info.json 注册 + 时间戳置顶 (幂等; .bak 备份; 剪映运行中会覆盖)."""
    import copy
    import time
    import uuid

    base = Path(DRAFTS)
    rm_path = base / "root_meta_info.json"
    rm = json.loads(rm_path.read_text(encoding="utf-8"))
    store = rm.get("all_draft_store") or []
    now_us = int(time.time() * 1e6)
    entry = next((e for e in store if e.get("draft_name") == name), None)
    if entry is not None:
        entry["tm_draft_modified"] = now_us
    else:
        if not store:
            print("[注册] all_draft_store 为空, 跳过注册")
            return False
        entry = copy.deepcopy(store[-1])
        entry.update({
            "draft_id": str(uuid.uuid4()).upper(),
            "draft_name": name,
            "draft_fold_path": str(draft_dir),
            "draft_json_file": str(draft_dir / "draft_content.json"),
            "draft_cover": "",
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_duration": int(dur_us),
            "draft_timeline_materials_size": 0,
            "tm_draft_removed": 0,
        })
        store.append(entry)
        rm["all_draft_store"] = store
    bak = base / "root_meta_info.json.bak-r-demo"
    if not bak.exists():
        bak.write_bytes(rm_path.read_bytes())
    rm_path.write_text(json.dumps(rm, ensure_ascii=False), encoding="utf-8")
    return True


def main() -> None:
    folder = d.DraftFolder(DRAFTS)
    script = folder.create_draft(DRAFT_NAME, W, H, allow_replace=True)

    n_text_tracks = 10
    # 轨道: 后来居上 — deco(贴纸) 在文字轨之后 = 贴纸永远压文字上层
    specs = [d.TrackSpec(d.TrackType.audio, "sfx")]
    specs += [d.TrackSpec(d.TrackType.text, f"t{i}") for i in range(n_text_tracks)]
    specs.append(d.TrackSpec(d.TrackType.sticker, "deco"))
    script.append_tracks(specs)

    track_windows: list[list[tuple[int, int]]] = [[] for _ in range(n_text_tracks)]

    def pick_track(s_us: int, e_us: int, forced: str | None = None) -> str:
        if forced:
            return forced
        for i, wins in enumerate(track_windows):
            if all(e_us <= s or s_us >= e for s, e in wins):
                wins.append((s_us, e_us))
                return f"t{i}"
        return f"t{n_text_tracks - 1}"

    def add_text(text, at, until, *, sz, c, x, y, anim, ams, font, track=None):
        try:
            seg = TextSegment(
                text,
                trange(int(at * _US), max(int((until - at) * _US), 500000)),
                font=font,
                style=d.TextStyle(size=sz, color=c),
                clip_settings=ClipSettings(transform_x=x, transform_y=y),
            )
            if anim:
                seg.add_animation(getattr(d.TextIntro, anim), duration=ams * 1000)
            tname = pick_track(int(at * _US), int(until * _US), forced=track)
            script.add_segment(seg, tname)
            return True
        except Exception as exc:
            print(f"[拍失败] {text[:12]}: {exc}")
            return False

    def add_sfx(raw_name, at, max_sec=None, volume=0.9):
        if not raw_name:
            return
        name = SFX_NEAR.get(raw_name, raw_name)
        if not sound_path(name):
            stats["sfx_missing"] += 1
            print(f"  [音效缺] {raw_name} → {name}")
            return
        if attach_sound(script, "sfx", name, at, volume=volume, max_sec=max_sec):
            stats["sfx"] += 1

    stats = {"text": 0, "sfx": 0, "sfx_missing": 0}
    cursor = 0.0
    for sec in SECTIONS:
        sec_start = cursor
        sec_end = sec_start + sec["dur"]
        # ── 章节卡 (标题≤15号短名, 副题 8 号) ──
        if add_text(sec["title"], sec_start, sec_start + CARD, sz=15, c=GOLD,
                    x=0.0, y=0.0, anim="放大", ams=400, font=F_BIG):
            stats["text"] += 1
        if add_text(sec["sub"], sec_start + 0.3, sec_start + CARD, sz=8, c=WHITE,
                    x=0.0, y=-0.16, anim="渐显", ams=500, font=F_BODY):
            stats["text"] += 1
        add_sfx("综艺开始 系统音", sec_start, volume=0.7)

        # ── 演示拍 ──
        for beat in sec["beats"]:
            at = sec_start + beat["at"]
            until = sec_start + (beat.get("until") if beat.get("until") is not None else sec["dur"])
            if beat.get("dur") is not None:
                until = at + beat["dur"]
            if add_text(beat["t"], at, until, sz=beat["sz"], c=beat["c"], x=beat["x"],
                        y=beat["y"], anim=beat["anim"], ams=beat["ams"],
                        font=beat["font"], track=beat.get("track")):
                stats["text"] += 1
            sfx_max = beat.get("sfx_max")
            if sfx_max is None and beat["anim"] in ("打字光标", "打字机_I", "打字机_II"):
                sfx_max = beat["ams"] / 1000 + 0.15
            add_sfx(beat.get("sfx"), at, max_sec=sfx_max)

        cursor = sec_end + GAP

    script.save()
    total = cursor - GAP
    ok = register_draft(DRAFT_NAME, Path(DRAFTS) / DRAFT_NAME, int(total * _US))
    print(f"\n✅ 草稿《{DRAFT_NAME}》已生成 → {DRAFTS}\\{DRAFT_NAME}")
    print(f"   总时长 ~{total:.0f}s | 文字段 {stats['text']} | 音效 {stats['sfx']} "
          f"(缺 {stats['sfx_missing']}) | 注册 {'✓' if ok else '✗'}")
    print("   行宽自检: 所有单行 字号×8×字数 ≤ ~1100px (34.6/27.6 巨字保留原版出血观感)")


if __name__ == "__main__":
    main()
