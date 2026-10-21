# -*- coding: utf-8 -*-
"""账号数据看板纯计算测试 — 异常日/常态基线/周桶/Pearson (口径: 1021 归档报告)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.analytics_service import (
    build_alerts, lag_table, milestone_rows, pearson, summarize,
    video_summary, week_buckets, weekday_dist,
)


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


# ---------- 看板 v2 ----------

def test_video_summary_post_vs_nopost():
    rows = [
        _row("2026-10-01", 10, 10, 10, 0, 0) | {"posts": 1, "views": 1000, "sec5_rate": 0.2, "sec2_bounce": 0.5},
        _row("2026-10-02", 15, 5, 5, 0, 0) | {"posts": 1, "views": 500, "sec5_rate": 0.4, "sec2_bounce": 0.4},
        _row("2026-10-03", 16, 1, 1, 0, 0) | {"posts": 0, "views": 100, "sec5_rate": 0.1, "sec2_bounce": 0.6},
    ]
    v = video_summary(rows)
    assert v["posts_total"] == 2
    assert v["post_group"]["days"] == 2 and v["nopost_group"]["days"] == 1
    assert v["post_group"]["avg_views"] == 750 and v["nopost_group"]["avg_views"] == 100
    assert v["post_group"]["avg_fan_new"] == 7.5
    # 加权完播: (0.2*1000+0.4*500)/1500 = 0.267
    assert v["post_group"]["sec5_rate"] == 0.27
    assert v["views_total"] == 1600
    assert v["fan_efficiency"] == round(16 / 0.16, 2)  # 16粉 / 0.16万播放


def test_weekday_dist_counts():
    # 2026-10-05(一) ~ 10-11(日) 各一天, 净增 1..7
    rows = [_row(f"2026-10-0{d}", d, d) | {"weekday": d} for d in range(1, 8)]
    ws = weekday_dist(rows)
    assert len(ws) == 7 and ws[0]["label"] == "周一" and ws[0]["avg_net"] == 1
    assert ws[6]["label"] == "周日" and ws[6]["avg_net"] == 7


def test_lag_table_perfect_lead():
    # 构造: 净增[t+1] = 回访[t] (回访领先 1 天) → k=1 时 r=1
    revs = [3, 1, 4, 1, 5]
    # net[t] = revs[t-1] (今天的净增 = 昨天的回访) → r(回访[t], 净增[t+1]) = 1
    nets = [7, 3, 1, 4, 1, 5]
    rows = [_row(f"2026-10-0{t + 1}", 0, nets[t], revisit=revs[t] if t < 5 else 2) for t in range(6)]
    lt = {x["lag"]: x["r"] for x in lag_table(rows)}
    assert lt[1] == 1.0 and lt[0] < 0.9


def test_milestone_rows_eta():
    ms = milestone_rows(end_total=411, base_rate=8.29, full_rate=12.45, last_date="2026-10-20")
    m500 = ms[0]
    assert m500["target"] == 500 and m500["remaining"] == 89
    assert m500["eta_base_days"] == round(89 / 8.29)  # 11
    assert m500["eta_base_date"] == "2026-10-31"
    ms2 = milestone_rows(end_total=1300, base_rate=8, full_rate=10, last_date="2026-10-20")
    assert all(m["done"] for m in ms2)


def test_build_alerts_stale_and_stall():
    from datetime import date
    rows = [
        _row("2026-10-01", 10, 10, 10, 0, 0),
        _row("2026-10-02", 9, -1, 0, 1, 0),
        _row("2026-10-03", 9, 0, 1, 1, 0),
    ]
    a = build_alerts(rows, today=date(2026, 10, 10))
    kinds = {x["kind"] for x in a}
    assert kinds == {"stale", "stall"}
    fresh = [_row("2026-10-10", 5, 5, 5, 0, 1)]
    assert build_alerts(fresh, today=date(2026, 10, 10)) == []


def test_import_num_coercion_and_detect(tmp_path):
    import openpyxl
    from app.services.analytics_import import _num, detect_kind

    assert _num("2847") == 2847 and _num("0.005727") == 0.005727
    assert _num(None) is None and _num("") is None and _num(12) == 12

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["作品名称", "发布时间", "播放量"])
    ws.append(["t", "2026-10-20 20:00:00", "2847"])
    f1 = tmp_path / "v.xlsx"; wb.save(f1)
    assert detect_kind(f1) == "videos"

    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(["日期", "星期", "投稿量"])
    f2 = tmp_path / "d.xlsx"; wb2.save(f2)
    assert detect_kind(f2) == "daily"


def test_episodes_table_joins_video_truth():
    from app.services.analytics_service import episodes_table
    rows = [_row("2026-10-20", 411, 38, 40, 2, 9)]
    links = [{"date": "2026-10-20", "series": "卓有成效的管理者", "book_id": None,
              "book_title": "x", "ep_index": 1, "ep_title": "T", "publish_time": "20:00", "note": ""}]
    summ = {"anomaly_days": [{"date": "2026-10-20"}]}
    vids = [{"publish_date": "2026-10-20", "views": 2847, "completion_rate": 0.0057,
             "fan_growth": 39, "collects": 20, "profile_visits": 35, "genre": "3-5min视频"}]
    out = episodes_table(rows, links, summ, vids)
    assert out[0]["v_views"] == 2847 and out[0]["v_fan_growth"] == 39
    assert out[0]["fan_new"] == 40 and out[0]["v_genre"] == "3-5min视频"
    # 无视频 (如 09-27 非正片) → None
    out2 = episodes_table(rows, links, summ, [])
    assert out2[0]["v_views"] is None and out2[0]["fan_new"] == 40
