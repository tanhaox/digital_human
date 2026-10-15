"""Tests for chunk-mode semantic grouping (0913 深夜, 用户令"包缝必须语义切").

Contract pinned here:
- _group_lines_semantic: 包缝只落在句末行之后 (回退收包), 绝不腰斩单行,
  情绪墙强制断包且回退不跨墙, 覆盖 = 连续整行划分 (display/reroll 依赖)。
- synthesize_lines(synth_unit="chunk") dispatch: 语义分组仅 chunk 模式生效,
  行模式 (_merge_lines_for_batch) 行为逐字不变。
- 旧风险在案: 单行超限独包上限是行自身长度 (140 钳制管不住), 悬崖 ~170 字。

Run:
    cd /d F:\\AI-Agent-Local\\digital_human
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_tts_chunk_semantic.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DH = Path(__file__).resolve().parents[1]
if str(_DH) not in sys.path:
    sys.path.insert(0, str(_DH))

from scripts.tts_lib.lines import synthesize_lines  # noqa: E402
from scripts.tts_lib.text import (  # noqa: E402
    _ends_sentence,
    _group_lines_semantic,
    _merge_lines_for_batch,
)


# ── _ends_sentence ──


@pytest.mark.parametrize("line", ["他走了。", "你敢信？", "别废话！", "他说……", "第一；", "他说完了。”"])
def test_ends_sentence_true(line: str) -> None:
    assert _ends_sentence(line)


@pytest.mark.parametrize("line", ["还没说完，", "他说的对", "", "   ", "列举如下：", "（完）"])
def test_ends_sentence_false(line: str) -> None:
    assert not _ends_sentence(line)


# ── _group_lines_semantic ──


def _assert_partition(groups: list[list[int]], n: int) -> None:
    """包必须是有序连续整行划分 — display.json/reroll 的硬约束。"""
    flat = [i for g in groups for i in g]
    assert flat == list(range(n))
    for g in groups:
        assert g == list(range(g[0], g[-1] + 1))


def test_all_fit_single_package() -> None:
    lines = ["第一句结束了。", "第二句也结束了。"]
    assert _group_lines_semantic(lines, 140) == [[0, 1]]


def test_overflow_backtracks_to_sentence_end() -> None:
    # 40 字上限: [0,1]=38 装下, 行 2 会溢出; 行 1 句末 → 包缝落在行 1 后,
    # 行 2 (半句开头) 与行 3 同包 — 旧行为会把包缝切在行 1|2 之间吗? 会,
    # 但若行 1 不以句号结尾旧行为照切 — 本例钉回退语义: 溢出点前有句末行。
    lines = ["第一句结束了。", "第二句也结束了。", "第三句开头没完", "接后半句。"]
    groups = _group_lines_semantic(lines, 40)
    _assert_partition(groups, 4)
    for g in groups[:-1]:  # 非末包: 收包行必句末
        assert _ends_sentence(lines[g[-1]])


def test_no_sentence_end_hard_cut_fallback() -> None:
    # 整段无句末行: 只能硬切兜底 (同旧行为), 但分区性质不破
    lines = ["无标点短句一", "无标点短句二", "无标点短句三", "无标点短句四"]  # 各 6 字
    groups = _group_lines_semantic(lines, 12)
    _assert_partition(groups, 4)
    assert [len(g) for g in groups] == [2, 2]


def test_overlong_line_own_package() -> None:
    long_line = "超" * 150 + "。"
    lines = [long_line, "正常句。", "另一句。"]
    groups = _group_lines_semantic(lines, 140)
    assert groups[0] == [0]
    _assert_partition(groups, 3)


def test_walls_force_close_and_backtrack_never_crosses_wall() -> None:
    # 情绪墙在行 2 (0-based): 行 0-1 同段, 行 2-3 另一段。
    # 上限 30: 行 0+1 = 22 字, 行 2 溢出 → 包 0 = [0,1] (句末收包);
    # 墙强制行 2 开新包。
    lines = ["第一段的第一句结束。", "第一段的第二句结束。", "第二段开头来了。", "第二段收尾。"]
    walls = frozenset({2})
    groups = _group_lines_semantic(lines, 30, walls=walls)
    _assert_partition(groups, 4)
    assert all(2 not in g or g[0] == 2 for g in groups)
    # 包内行必属同一情绪段 (墙之间)
    assert groups[0][-1] < 2 <= groups[1][0]


def test_seams_always_at_sentence_end_on_realistic_script() -> None:
    # 模拟拆书稿形态 (短句一行, 偶有超长行), 140 上限下全部包缝句末
    lines = (
        ["增长停了，客户也在走。", "做账号的、开店的、带团队的，甚至就是拼命上班的你——"]
        + [f"这是第{i}句测试文本，讲一个道理。" for i in range(40)]
        + ["最后一句收尾。"]
    )
    groups = _group_lines_semantic(lines, 140)
    _assert_partition(groups, len(lines))
    for g in groups[:-1]:
        assert _ends_sentence(lines[g[-1]]), f"包缝落半句: 收包行 {g[-1]!r}"


# ── 行模式零改动 ──


def test_line_mode_grouping_unchanged() -> None:
    # 行模式仍走纯字数贪心 (静音切分依赖), 与语义分组结果可不同
    lines = ["第一句结束了。", "第二句也结束了。", "第三句开头没完", "接后半句。"]
    assert _merge_lines_for_batch(lines, 40) == _group_lines_semantic(lines, 40)  # 本例恰好一致
    # 但半句边界处两者必须不同: 行 2-3 是半句跨行, 字数满即切 vs 回退句末
    lines2 = ["句子A第一个逗号前半部分到底还是结束了。", "句子B开始没完", "句子B后半。", "句子C。"]
    g_line = _merge_lines_for_batch(lines2, 30)
    g_sem = _group_lines_semantic(lines2, 30)
    for g in g_sem[:-1]:
        assert _ends_sentence(lines2[g[-1]])
    # 行模式无此保证 (可能在句中收包)
    assert any(not _ends_sentence(lines2[g[-1]]) for g in g_line) or g_line == g_sem


# ── synthesize_lines chunk dispatch 集成 (合成打桩) ──


def test_chunk_dispatch_uses_semantic_grouping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np
    import soundfile as sf

    import scripts.tts_lib.lines as lines_mod

    captured: list[str] = []

    def fake_synth_single(syn, text, output_path, **kwargs):
        captured.append(text)
        sf.write(str(output_path), np.zeros(240, dtype=np.float32), 24000)

    monkeypatch.setattr(lines_mod, "_synth_single", fake_synth_single)
    monkeypatch.setattr(
        lines_mod, "normalize_audio", None, raising=False
    )  # loudnorm 路径 app 不可用时本就跳过

    text = "\n".join(
        ["第一句结束了。", "第二句也结束了。", "第三句开头没完", "接后半句。"]
    )
    manifest = synthesize_lines(text=text, output_dir=tmp_path, backend="indextts",
                                synth_unit="chunk", batch_max_chars=40)
    segs = manifest["segments"]
    assert all(s.get("chunk") for s in segs)
    idx_groups = [s["line_indices"] for s in segs]
    _assert_partition(idx_groups, 4)
    for g, s in zip(idx_groups, segs):
        assert s["text"].split("\n") == [  # 包文本 = 整行 join
            ["第一句结束了。", "第二句也结束了。", "第三句开头没完", "接后半句。"][i] for i in g
        ]


def test_chunk_dispatch_respects_emotion_walls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np
    import soundfile as sf

    import scripts.tts_lib.lines as lines_mod

    def fake_synth_single(syn, text, output_path, **kwargs):
        sf.write(str(output_path), np.zeros(240, dtype=np.float32), 24000)

    monkeypatch.setattr(lines_mod, "_synth_single", fake_synth_single)

    text = "\n".join(["第一段一句。", "第一段二句。", "第二段一句。", "第二段二句。"])
    emo = {"vector": [0.1] * 8, "alpha": 1.0}
    segments = [
        {"text": "第一段一句。第一段二句。", "vector": [0.2] * 8, "alpha": 1.0},
        {"text": "第二段一句。第二段二句。", "vector": [0.3] * 8, "alpha": 1.0},
    ]
    manifest = synthesize_lines(text=text, output_dir=tmp_path, backend="indextts",
                                synth_unit="chunk", batch_max_chars=140,
                                emotion_segments=segments)
    segs = manifest["segments"]
    assert all(s.get("chunk") for s in segs)
    # 上限 140 装得下全部 — 但情绪墙强制两包
    assert len(segs) == 2
    assert segs[0]["line_indices"] == [0, 1] and segs[1]["line_indices"] == [2, 3]


def test_question_pause_packing_and_padding():
    """气口引擎 (0914 用户令: 气口=留观众反应时间): 问句1.2s(系统0.7+再加0.5)/
    大段落边界1s/-Xs-标记叠加(歧义句手控); 问句/标记行强制包尾。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from tts_lib.text import _group_lines_semantic, _is_question, _pause_after, _tts_text

    assert _is_question("你现在铺着几个入口？")
    assert _is_question("他说\"行吗？\"-0.5s-")
    assert not _is_question("陈述句。")
    assert _pause_after("垫半秒。-0.5s-") == 0.5
    assert _tts_text("垫半秒。-0.5s-") == "垫半秒。"

    lines = ["今天说个事儿。", "你现在铺着几个入口？", "接着讲。", "垫一秒。-1s-", "收尾。"]
    b = _group_lines_semantic(lines, 140)
    assert b == [[0, 1], [2, 3], [4]]        # 问句/标记行永远包尾
    assert _group_lines_semantic(["一。", "二。", "三。"], 140) == [[0, 1, 2]]

    # 三规则 e2e: 问句1.2 / 段末1.0 / 标记叠加
    import tempfile, subprocess
    from tts_lib.lines import _paragraph_final_indices, _apply_breath_pauses
    text = "段一句一。\n你现在铺着几个入口？\n\n段二句一。\n段二末句。\n\n末段。"
    assert sorted(_paragraph_final_indices(text)) == [1, 3]   # 末段末行除外(音频收尾无气口)
    d = Path(tempfile.mkdtemp())
    lines = ["段一句一。", "问句？", "段二陈述。", "歧义句。-0.5s-", "段二末句。"]
    import soundfile as sf
    segs = []
    for i, ln in enumerate(lines):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                        "-t", "1", str(d / f"{i:03d}.wav")], capture_output=True)
        segs.append({"index": i, "text": ln, "file": f"{i:03d}.wav", "duration": 1.0})
    _apply_breath_pauses(d, lines, {4}, segs, 1.2, 1.0)
    got = [round(s.get("breath_pause", 0.0), 2) for s in segs]
    assert got == [0.0, 1.2, 0.0, 0.5, 1.0], got
    assert abs(sf.info(str(d / "001.wav")).duration - 2.2) < 0.05


def test_breath_tier_defaults_0918():
    """气口档位定标 (0918 用户听感令): 问句 0.8 (自然档) / 段末 1.0 / 模块 1.5=上限.

    约束: 1.2-1.5 区间只有段落级边界可进 (模块墙), 系统自动气口不得 >1.5;
    -Xs- 手动标记是显式叠加, 不在档位管辖。"""
    import sys
    import inspect
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from tts_lib import lines as _lines
    sig = inspect.signature(_lines.synthesize_lines)
    assert sig.parameters["question_breath_sec"].default == 0.8
    assert sig.parameters["para_breath_sec"].default == 1.0
    assert sig.parameters["module_breath_sec"].default == 1.5
