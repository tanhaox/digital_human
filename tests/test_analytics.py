# -*- coding: utf-8 -*-
"""账号数据看板纯计算测试 — 异常日/常态基线/周桶/Pearson (口径: 1021 归档报告)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics_service import pearson, summarize, week_buckets


def _row(d, total, net, new=0, lost=0, revisit=0):
    return {"date": d, "total_fans": total, "fan_net": net, "fan_new": new,
            "fan_lost": lost, "revisit_fans": revisit}


def test_pearson_perfect_and_flat():
    import pytest
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([5, 5, 5], [1, 2, 3]) == 0.0  # 零方差
    assert pearson([1], [1]) == 0.0  # 样本不足


def test_summarize_anomaly_and_baseline():
    # nets [1..6, 30]: median=4 → 阈值12 → 仅 30 为异常日
    rows = [
        _row(f"2026-10-0{i}", i, i, i, 0, i) for i in range(1, 7)
    ] + [_row("2026-10-07", 49, 30, 30, 0, 6)]
    s = summarize(rows)
    assert s["days"] == 7 and s["median_net"] == 4 and s["anomaly_threshold"] == 12
    assert [a["date"] for a in s["anomaly_days"]] == ["2026-10-07"]
    assert s["anomaly_days"][0]["is_peak"] is True
    assert s["baseline_days"] == 6 and s["baseline_avg"]["net"] == 3.5
    assert s["baseline_total_net"] == 21
    assert s["nonpos_days"] == []


def test_summarize_churn_and_nonpos():
    rows = [
        _row("2026-10-01", 10, 10, 10, 0, 0),
        _row("2026-10-02", 12, 2, 4, 2, 1),
        _row("2026-10-03", 11, -1, 0, 1, 0),
    ]
    s = summarize(rows)
    assert s["churn_pct"] == round(3 / 14 * 100, 2)
    assert [d["date"] for d in s["nonpos_days"]] == ["2026-10-03"]


def test_week_buckets_monday_aligned():
    # 2026-10-05 是周一: 10-03(六)+10-04(日) 一桶, 10-05~10-07 一桶
    rows = [
        _row("2026-10-03", 1, 1),
        _row("2026-10-04", 2, 1),
        _row("2026-10-05", 4, 2),
        _row("2026-10-06", 5, 1),
        _row("2026-10-07", 6, 1),
    ]
    ws = week_buckets(rows)
    assert [w["label"] for w in ws] == ["10-03~10-04", "10-05~10-07"]
    assert [w["net"] for w in ws] == [2, 4]
    assert ws[1]["week_no"] == 41 and ws[1]["is_peak"] is True


def test_summarize_matches_archive_report_scale():
    """33 天真实口径回归锚: 中位数 9 / 阈值 27 (归档报告 §05)."""
    nets = [1, 4, 4, 11, 0, 6, 9, 3, 24, -1, 22, 12, 30, 3, 21, 15, 42, 2, 31, 7, 19, 5, 12, -1, 9, 1, 20, 9, 15, -1, 38, 1, 38]
    total, rows = 0, []
    for i, n in enumerate(nets):
        total += n
        rows.append(_row(f"2026-09-{i + 1:02d}" if i < 30 else f"2026-10-{i - 29:02d}", total, n))
    s = summarize(rows)
    assert s["median_net"] == 9 and s["anomaly_threshold"] == 27
    assert len(s["anomaly_days"]) == 5
