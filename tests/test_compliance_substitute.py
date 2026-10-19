# -*- coding: utf-8 -*-
"""1019 字幕限流词主闸 substitute_text 测试 — 平台缓冲词/暴力书面化/protect 防误伤."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.compliance import substitute_text


def test_platform_buffer_words():
    out, hits = substitute_text("他当年在微信上做电商, 后来转战小红书和快手")
    assert "微信" not in out and "小红书" not in out and "快手" not in out
    assert "绿泡泡" in out and "小红薯" in out and "某手" in out
    assert set(hits) == {"微信", "小红书", "快手"}


def test_violence_phrase_written_form():
    out, hits = substitute_text("目击者被人杀死, 尸体三天后才被发现")
    assert "遇害" in out and "遗体" in out
    assert "杀死" not in out and "尸体" not in out
    assert set(hits) == {"杀死", "尸体"}


def test_long_word_priority_no_double_replace():
    """杀死 必须整词先替 — 不能先替单字杀变'行凶死'再补死."""
    out, hits = substitute_text("他杀死了对手")
    assert out == "他遇害了对手" or out == "他除掉了对手"
    assert "行凶" not in out


def test_protect_words_untouched():
    text = "血压升高, 花了大量心血, 最后血本无归, 画风很死板"
    out, hits = substitute_text(text)
    assert out == text
    assert hits == []


def test_isolated_single_char_fallback():
    out, hits = substitute_text("枪 就放在桌上")
    assert "枪支" in out
    assert hits == ["枪"]


def test_digits_never_corrupted():
    """protect 占位符不得破坏正文数字 (历史事故预防位)."""
    text = "2023年5月, 57个账号, 第3集, 卖了1000多单"
    out, _ = substitute_text(text)
    assert out == text


def test_bare_toutiao_not_touched():
    """裸词'头条'多为新闻头版义 — 只禁全词'今日头条'."""
    out, _ = substitute_text("这条新闻上了热搜头条, 头版头条")
    assert "某资讯平台" not in out
    out2, hits2 = substitute_text("今日头条的算法")
    assert "某资讯平台" in out2 and hits2 == ["今日头条"]


def test_empty_and_clean_text_passthrough():
    assert substitute_text("") == ("", [])
    assert substitute_text("好书值得慢慢读") == ("好书值得慢慢读", [])


def test_hits_deduped():
    _, hits = substitute_text("死了一次又一次, 死了三回")
    assert hits == ["死了"]


# ── 1019 B2: 搭配表升级 (豆包 ep6 "方案离世在哪儿"病句实锤) + 黑话/名词出口闸 ──

def test_violence_collocation_not_mangled():
    """孤立单字万金油不得误伤合法搭配: 死规矩/死在/写死了 各配自然说法."""
    out, hits = substitute_text("我不设死规矩。方案死在哪儿？计划全写死了。")
    assert "离世" not in out
    assert "铁规矩" in out and "折在" in out and "白写了" in out
    assert set(hits) >= {"死规矩", "死在", "写死了"}


def test_violence_single_still_works():
    """搭配之外的孤立'死'仍走万金油 (安全边界不回退)."""
    out, hits = substitute_text("他死了。")  # 死了 本就在 phrase 表 → hits 记短语
    assert "离世" in out and "死了" in hits


def test_jargon_substitute_pipeline_words():
    from app.services.compliance import jargon_substitute
    out, hits = jargon_substitute("闲话层说一句：独立交付。微行动：翻记录。下集预告：明天见。")
    assert "闲话层" not in out and "微行动" not in out and "下集预告" not in out
    assert out.startswith("独立交付。") and "马上能做的一步：翻记录" in out and "下一集，明天见" in out
    assert len(hits) >= 3


def test_series_noun_substitute_swap_and_generic_kept():
    from app.services.compliance import series_noun_substitute
    out, n = series_noun_substitute("这是五步转型的第1步，也是这件工具。", "五件工具")
    assert "五件工具" in out and "五步转型" not in out and "第1步" in out and n == 1
    # 无钦定 → 不动
    out2, n2 = series_noun_substitute("五步转型讲透了。", None)
    assert out2 == "五步转型讲透了。" and n2 == 0
