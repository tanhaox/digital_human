"""TTS ASR 回听校验测试 (2026-09-03): 拼音对比核心 + 闭环回滚逻辑. 无 GPU —
transcriber/resynth 全注入, wav 用真实小文件驱动备份/回滚字节级断言。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.tts_verify import (
    _apply_markup,
    _diff_readings,
    _pinyin_pairs,
    verify_pronunciation,
)


def test_pinyin_pairs_phrase_aware():
    """词组感知: 银行→hang / 行李→xing; 标点/数字/拼音标注过滤."""
    assert [p[1] for p in _pinyin_pairs("银行")] == ["yin", "hang"]
    assert [p[1] for p in _pinyin_pairs("行李")] == ["xing", "li"]
    # 已有 <铟|YIN1> 标注: 铟只计一次汉字, 标记本身不产生汉字
    pairs = _pinyin_pairs("磷化<铟|YIN1>材料")
    assert [p[0] for p in pairs] == ["磷", "化", "铟", "材", "料"]
    # 纯非汉字且无数字 → []
    assert _pinyin_pairs("GPT, hello!") == []
    # 阿拉伯数字先展开成中文读法再取拼音 (2026 → 二零二六)
    assert [p[0] for p in _pinyin_pairs("GPT-5, 2026!")] == ["五", "二", "零", "二", "六"]


def test_diff_readings_homophone_immune():
    """同音字替换 (的地得) 不报; 声/韵母不同才报; 吞字也报."""
    # 的/地/得 轻声两读豁免 (pypinyin 地=di4 与 TTS 实读 de 常态摇摆, 不修)
    assert _diff_readings("慢慢地走过去", "慢慢的走过去") == []
    # 儿化音吞"儿"/语气词变换豁免
    assert _diff_readings("突然觉得累得有点儿喘不上气", "突然觉得累得有点喘不上气") == []
    # 说(shuo) 读成 睡(shui) → 报
    d = _diff_readings("他说服了团队", "他睡服了团队")
    assert len(d) == 1 and d[0]["char"] == "说" and d[0]["expect"] == "shuo"
    assert d[0]["heard"] == "shui"
    # 吞字 (delete) → 报, heard 为空
    d2 = _diff_readings("银行门口人来人往", "银门口人来人往")
    assert d2 and d2[0]["char"] == "行" and d2[0]["heard"] == ""
    # 行(hang) 听成 号(hao) → 报
    d3 = _diff_readings("银行门口人来人往", "银号门口人来人往")
    assert len(d3) == 1 and d3[0]["char"] == "行" and d3[0]["expect"] == "hang"


def test_diff_readings_nasal_tolerance():
    """前后鼻音混淆 (中文 ASR 最常见噪声) 容差不报; 声母不同仍报."""
    # 井(jing) 听成 进/近(jin) → 不报
    assert _diff_readings("秩序井然有序", "秩序进然有序") == []
    # 懂(dong) 听成 总(zong) → 声母 d≠z, 报
    d = _diff_readings("你听懂了吗朋友", "你听总了吗朋友")
    assert len(d) == 1 and d[0]["char"] == "懂" and d[0]["expect"] == "dong"


def test_diff_readings_retroflex_tolerance():
    """卷舌/平舌混淆容差 (2026-09-03 全库回听实测: 债→zai×10/纯→cun×5 全是 whisper 伪影)."""
    # 债(zhai) 听成 宰/再(zai) → 不报
    assert _diff_readings("国债规模很大", "国宰规模很大") == []
    # 纯(chun) 听成 存(cun) → 不报
    assert _diff_readings("纯粹的逻辑", "存粹的逻辑") == []
    # 专(zhuan) 听成 钻(zuan) → 不报
    assert _diff_readings("专注拆解本质", "钻注拆解本质") == []
    # 卷舌容差不吞真错: 战(zhan) 听成 三(san) → 声母类别不同, 报
    d = _diff_readings("全面战略已经定型", "全面三略已经定型")
    assert len(d) == 1 and d[0]["char"] == "战" and d[0]["expect"] == "zhan"


def test_diff_readings_asr_arabic_digits():
    """whisper 把口播数字转写成阿拉伯数字且按数值归一 (实测: 十块钱→"10块钱",
    五百万→"500万") → 展开对齐, 不再误报吞字。全库回听 (2026-09-03): 十吞×87/
    一吞×63/百吞×52 全是该伪影。"""
    # 值形式: 10→十, 500→五百 (万 字两侧自然对齐)
    assert _diff_readings(
        "同样十块钱，以前能生成五百万处理量。",
        "同样10块钱以前能生成500万处理量") == []
    # 年份: 4位 1900-2099 逐位读 → 二零二四
    assert _diff_readings("今年是二零二四年", "今年是2024年") == []
    # 小数: 0.045 → 零点零四五
    assert _diff_readings("每项烧掉零点零四五美元", "每项烧掉0.045美元") == []
    # 拼音标注里的声调数字不受展开影响
    pairs = _pinyin_pairs("磷化<铟|YIN1>材料")
    assert [p[0] for p in pairs] == ["磷", "化", "铟", "材", "料"]
    # 已知残留 (不在本测试断言): 「百分之X」whisper 写成「X%」, 百分之 三字报吞
    # — 口播稿百分比走「三成七/个百分点」读法, 语料中占比极低, 由 30% 噪声线兜底。


def test_diff_readings_initial_class_tolerance():
    """同韵母+声母混淆对容差 (全库回听 2026-09-03 上下文抽查全为 whisper 听岔):
    就→有(j/y ×10)、达→塔(d/t ×7 全是英伟达)、灌→欢(g/h ×4)、期→记(j/q ×3)。"""
    # 就(jiu) 听成 有/右(you) → iu/ou 同韵 + j/y 同组, 不报
    assert _diff_readings("就事论事讲道理", "右事论事讲道理") == []
    # 达(da) 听成 塔(ta) → 不报
    assert _diff_readings("英伟达的芯片", "英伟塔的芯片") == []
    # 灌(guan) 听成 欢(huan) → 不报
    assert _diff_readings("灌溉面积很大", "欢溉面积很大") == []
    # 期(qi) 听成 记(ji) → 不报 (实测语料: 超期部署/履约周期)
    assert _diff_readings("超期部署要罚款", "超记部署要罚款") == []
    # 不吞真错: 最(zui) 听成 队(dui) → z/d 不在容差组, 报
    d = _diff_readings("最核心的问题", "队核心的问题")
    assert len(d) == 1 and d[0]["char"] == "最"


def test_diff_readings_liang_er_value_equal():
    """两/二同值异读 (×31 全库实测): 稿「两百四十一天」whisper 按值写「241天」,
    数字展开成 二百四十一天 — 两/二 对不上不是错读。"""
    assert _diff_readings("两百四十一天不靠岸", "241天不靠岸") == []
    assert _diff_readings("约合人民币两百万元", "约合人民币200万元") == []


def test_verify_loop_polyphone_guard(workdir: Path):
    """多音字歧义守卫: pypinyin 语境猜错期望 (长文档→zhang, 实读 chang 正确) —
    不重合成 (强标会把正确音频改坏), 不烧修复预算, 报 polyphone_ambiguous。"""
    heard_map: dict[Path, str] = {}
    resynth_calls: list[int] = []
    entries = [(0, "再跑一个长文档测试给你看")]
    _write_wav(workdir / "000.wav", fill=0.0)
    # TTS 实读 chang (正确); ASR 原样听到 — pypinyin 期望 zhang 才报
    heard_map[workdir / "000.wav"] = "再跑一个常温档测试给你看"

    report = verify_pronunciation(
        output_dir=workdir, manifest=_manifest(*entries),
        resynth_line=lambda idx, c, m: resynth_calls.append(idx),
        transcriber=lambda w: heard_map.get(w, ""),
    )
    assert resynth_calls == []                       # 没动手
    assert not list(workdir.glob("*.bak"))           # 没备份没回滚
    assert sf.read(str(workdir / "000.wav"))[0].max() < 0.1  # 原音频原样
    assert report["unresolved"][0]["reason"] == "polyphone_ambiguous"
    assert report["unresolved"][0]["diffs"][0]["char"] == "长"
    # 预算未消耗: 同批后续真错读仍可修
    assert report["checked"] == 1


def test_apply_markup_positional():
    """重复字按位置精确标注 (第2个'行'标, 第1个不标)."""
    line = "我们行走在北京银行的行伍里"  # 4 个行; 汉字序: ...银(7)行(8)的(9)行(10)...
    out = _apply_markup(line, {8: "hang2"})
    assert out == "我们行走在北京银<行|HANG2>的行伍里"


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def _write_wav(path: Path, fill: float) -> None:
    sf.write(str(path), np.full(2400, fill, dtype="float32"), 24000)


def _manifest(*entries: tuple[int, str]) -> dict:
    return {"segments": [{"index": i, "text": t, "file": f"{i:03d}.wav"}
                         for i, t in entries]}


def test_verify_loop_fix_rollback_and_noise(workdir: Path):
    """闭环主流程: 修复成功保留新音频 / 修复失败字节级回滚 / ASR噪声不修 / 短行跳过."""
    cases = {
        7: ("他读书给团队所有人听", "他读竹给团队所有人听"),   # 修 → 好转
        8: ("银行门口人来人往", "银号门口人来人往"),         # 修 → 仍错 → 回滚
        9: ("今天天气真不错呀朋友", "今天天气真不错呀朋友"), # 本来就对
        10: ("这本书讲了七个道理给你听", "完全无关的别的字啊喂"),  # ASR 噪声 (>30%)
        11: ("好的呀", None),                               # 短行跳过
    }
    heard_map: dict[Path, str] = {}
    resynth_calls: list[tuple[int, str]] = []
    events: list[tuple[str, str]] = []

    for idx, (script, heard) in cases.items():
        _write_wav(workdir / f"{idx:03d}.wav", fill=0.0)
        if heard:
            heard_map[workdir / f"{idx:03d}.wav"] = heard

    def transcriber(wav: Path) -> str:
        return heard_map.get(wav, "")

    def resynth(idx: int, clean: str, marked: str) -> None:
        resynth_calls.append((idx, marked))
        _write_wav(workdir / f"{idx:03d}.wav", fill=1.0)  # 新音频内容可区分
        # 重合成后: 行7 读对, 行8 仍读错
        fixed_text = {7: "他读书给团队所有人听", 8: "银号门口人来人往"}[idx]
        heard_map[workdir / f"{idx:03d}.wav"] = fixed_text

    report = verify_pronunciation(
        output_dir=workdir, manifest=_manifest(*[(i, t) for i, (t, _) in cases.items()]),
        resynth_line=resynth, on_event=lambda m, lv: events.append((m, lv)),
        transcriber=transcriber,
    )

    assert report["checked"] == 4          # 行11 短行不计
    assert [f["index"] for f in report["fixed"]] == [7]
    assert {u["index"]: u["reason"].split(":")[0] for u in report["unresolved"]} == {
        8: "resynth_failed", 10: "asr_noise"}
    # 行7 保留重合成音频 (fill≈1.0), 行8 字节级回滚 (fill≈0.0) — 16bit PCM 量化有微差
    assert sf.read(str(workdir / "007.wav"))[0].max() > 0.9
    assert sf.read(str(workdir / "008.wav"))[0].max() < 0.1
    # 标注文本: 行7 书→SHU1, 行8 行→HANG2
    marked7 = next(m for i, m in resynth_calls if i == 7)
    assert "<书|SHU1>" in marked7
    marked8 = next(m for i, m in resynth_calls if i == 8)
    assert "<行|HANG2>" in marked8
    # .bak 清理干净
    assert not list(workdir.glob("*.bak"))
    assert any(lv == "ok" for _, lv in events)


def test_verify_fix_budget(workdir: Path):
    """修复预算: 超过 max_fixes 的疑似行只报告不动手."""
    heard_map: dict[Path, str] = {}
    calls: list[int] = []
    entries = []
    for idx in range(3):
        text, heard = f"第{idx}本书读给听众们听清楚", f"第{idx}本竹读给听众们听清楚"
        entries.append((idx, text))
        _write_wav(workdir / f"{idx:03d}.wav", fill=0.0)
        heard_map[workdir / f"{idx:03d}.wav"] = heard

    report = verify_pronunciation(
        output_dir=workdir, manifest=_manifest(*entries),
        resynth_line=lambda idx, c, m: calls.append(idx),
        transcriber=lambda w: heard_map.get(w, ""),
        max_fixes=1,
    )
    # 注: 说服 的 shui 是说的合法异读 → 多音字守卫拦截不修 (宁可不修不可修错),
    # 故 fixture 用 书→竹 (shu 单读字) 驱动预算逻辑
    assert len(calls) == 1
    reasons = sorted(u["reason"].split(":")[0] for u in report["unresolved"])
    assert reasons == ["fix_budget", "fix_budget", "resynth_failed"]
