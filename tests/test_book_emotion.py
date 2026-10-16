"""读书线情绪分流测试 (2026-09-03): melancholic 入字典 + P5 读书版 prompt +
annotate_emotions style 分流 + 拆书稿 prompt_template 识别逻辑。"""
from __future__ import annotations

from app.services.boost_service.prompts import (
    P5_SPAN_PROMPT,
    P5_SPAN_PROMPT_BOOK,
)
from app.services.emotion_dict import (
    EMOTION_CN,
    EMOTIONS,
    resolve_emotion,
)


def test_melancholic_in_dict_and_resolvable():
    """melancholic 补进字典: 向量和 0.8 (规范), resolve 全链路可解析不 KeyError。"""
    assert "melancholic" in EMOTIONS
    assert EMOTION_CN["melancholic"] == "怅然"
    v = EMOTIONS["melancholic"].vector
    assert abs(sum(v) - 0.8) < 1e-6
    r = resolve_emotion("melancholic", 3)
    assert r["alpha"] >= 0.30
    assert r["visual_intent"] == "共情/低回"
    # 中文别名归一 (此前降级 calm)
    from app.services.emotion_dict import normalize_emotion_key
    assert normalize_emotion_key("忧郁") == "melancholic"
    assert normalize_emotion_key("伤感") == "melancholic"
    assert normalize_emotion_key("怅然") == "melancholic"


def test_book_prompt_rules():
    """读书版 prompt: 混合打底铁律存在, 惊讶铁律不在; 新闻版不动。"""
    assert "calm+confident 混合打底" in P5_SPAN_PROMPT_BOOK
    assert "melancholic" in P5_SPAN_PROMPT_BOOK
    assert "惊讶打底" not in P5_SPAN_PROMPT_BOOK
    assert "{persona}" in P5_SPAN_PROMPT_BOOK
    # 新闻版保持 2026-08-27 定稿
    assert "全程惊讶打底" in P5_SPAN_PROMPT


def test_annotate_emotions_book_vs_news(monkeypatch):
    """style 分流: book 用读书版 prompt + 漏句兜底 calm/2; news 惊讶版不动。"""
    import app.services.boost_service.emotions as em

    captured: dict = {}

    def fake_call(prompt, json_mode=True, max_tokens=1200):
        captured["prompt"] = prompt
        return '{"spans": [[1, 2, "melancholic", 3]]}'

    monkeypatch.setattr(em, "_call", fake_call)
    text = "第一句讲书中情节。\n第二句人物低落了。\n第三句没被 span 覆盖。\n第四句也没有。"

    out = em.annotate_emotions(text, "静姐", style="book")
    assert "读书陪伴基调" in captured["prompt"]
    assert "[melancholic/3]" in out
    assert "[calm/2]" in out  # 漏句继承读书版兜底, 不再是 surprised

    out2 = em.annotate_emotions(text, "老谭")
    assert "全程惊讶打底" in captured["prompt"]
    assert "[surprised/2]" in out2  # 新闻版漏句兜底不变


def test_book_style_detection_from_prompt_template():
    """audio._do_tts 的线别识别口径: prompt_template 含 book → book。"""
    def style_of(pt: str | None) -> str:
        return "book" if pt and "book" in pt else "news"

    assert style_of("jingshu-book") == "book"
    assert style_of(None) == "news"
    assert style_of("laotan-tech-7layer") == "news"
