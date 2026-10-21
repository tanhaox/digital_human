/* 账号分析看板 — 数据全部来自 /api/analytics/daily (data/analytics.db),
 * 页面零内嵌数据: 新导出只需 scripts/import_douyin_metrics.py 入库后刷新。
 * 口径 (异常日=净增≥3×中位数 / 常态基线=剔除异常日) 由服务端计算, 见 app/services/analytics_service.py */
"use strict";

var API = "/api/analytics/daily";
var INK = "#17202E", PRIM = "#0E6B5C", AMBER = "#C77A22", BAD = "#B4533F", MUT = "#6B7482", LINE = "#E4E7EC";
var AXIS_LABEL = { color: "#6B7482", fontSize: 11 };
var grid = { left: 8, right: 12, top: 36, bottom: 8, containLabel: true };
var charts = [];

function fmt2(x) { return Number(x).toFixed(2); }
function fmt1(x) { return Number(x).toFixed(1); }
function sign1(x) { return (x >= 0 ? "+" : "") + fmt1(x); }
function md(d) { return String(d).slice(5); }
function byId(id) { return document.getElementById(id); }

function showEmpty(msg) {
  ["secFindings", "secCharts", "secWeekly", "secScatter", "secAnomaly", "secRecs", "secDetail"].forEach(function (s) { byId(s).style.display = "none"; });
  byId("emptyState").style.display = "block";
  byId("heroSub").textContent = msg;
}

function chart(el) { var ch = echarts.init(byId(el)); charts.push(ch); window.addEventListener("resize", function () { ch.resize(); }); return ch; }

function render(data) {
  var s = data.summary, rows = data.rows;
  var dates = rows.map(function (r) { return r.date; });
  var totals = rows.map(function (r) { return r.total_fans; });
  var nets = rows.map(function (r) { return r.fan_net; });
  var absorb = rows.map(function (r) { return r.fan_new; });
  var losts = rows.map(function (r) { return r.fan_lost; });
  var rets = rows.map(function (r) { return r.revisit_fans; });
  var anomDates = {};
  s.anomaly_days.forEach(function (a) { anomDates[a.date] = true; });

  /* ── hero + KPI ── */
  byId("heroSub").textContent = "数据范围 " + s.from + " ～ " + s.to + "（" + s.days + " 天）· 已对异常高增长日识别并剔除 · 总粉丝从 " +
    s.start_total_fans + " 增长至 " + s.end_total_fans;
  byId("kTotal").innerHTML = s.end_total_fans + "<small> 人</small>";
  byId("kNet").innerHTML = fmt2(s.avg.net) + "<small> /日</small>";
  byId("kBase").innerHTML = fmt2(s.baseline_avg.net) + "<small> /日</small>";
  byId("kChurn").innerHTML = fmt2(s.churn_pct) + "<small>%</small>";

  /* ── 01 核心结论 ── */
  var nonpos = s.nonpos_days.map(function (d) { return md(d.date) + "(" + d.net + ")"; }).join("、");
  byId("f1d").innerHTML = s.days + " 天累计吸粉 <b>" + s.total_new + "</b>、累计脱粉仅 <b>" + s.total_lost +
    "</b>，脱粉/吸粉 <b>" + fmt2(s.churn_pct) + "%</b>；日均净增 <b>" + fmt2(s.avg.net) + "</b>，期末总粉丝 <b>" + s.end_total_fans + "</b>。";
  byId("f2d").innerHTML = s.anomaly_days.length + " 个高增长异常日合计净增 <b>" + s.anomaly_net + "</b>，占总增长 <b>" +
    fmt1(s.anomaly_net_share_pct) + "%</b>；剔除后日均净增降至 <b>" + fmt2(s.baseline_avg.net) + "</b>（约 <b>" +
    fmt1(s.deltas.net) + "%</b>），常态基线明显更低。";
  byId("f3d").innerHTML = "回访粉丝量与净增强正相关（<b>r≈" + fmt2(s.r_net_revisit) + "</b>），爆款内容同时带动新增与回访，回访异动往往领先净增。";
  byId("f4d").innerHTML = nonpos + " 共 <b>" + s.nonpos_days.length + " 天净增≤0</b>，需关注发布节奏与选题连续性。";

  /* ── 02 图表 ── */
  byId("hTotal").textContent = "总粉丝累计增长（" + s.start_total_fans + " → " + s.end_total_fans + "）";
  byId("capNet").innerHTML = "琥珀色为识别出的 " + s.anomaly_days.length + " 个高增长异常日（净增 ≥ " +
    s.anomaly_threshold + " = 3×中位数）；红色为负增长日。";

  chart("cTotal").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var i = ps[0].dataIndex; return dates[i] + "<br>总粉丝 <b>" + totals[i] + "</b> 人<br>净增 " + nets[i] + " 人"; } },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { interval: 5, formatter: md }) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "line", data: totals, smooth: true, symbol: "none", lineStyle: { width: 3, color: PRIM },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(14,107,92,.28)" }, { offset: 1, color: "rgba(14,107,92,.02)" }]) },
      itemStyle: { color: PRIM } }]
  });

  chart("cNet").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var i = ps[0].dataIndex; var o = nets[i] >= s.anomaly_threshold ? "（高增长异常日）" : nets[i] < 0 ? "（负增长）" : ""; return dates[i] + "<br>净增 <b>" + nets[i] + "</b> 人" + o + "<br>吸粉 " + absorb[i] + " · 脱粉 " + losts[i] + " · 回访 " + rets[i]; } },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { interval: 5, formatter: md }) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } }, axisLine: { show: false } },
    series: [{ type: "bar", data: nets.map(function (v) { return { value: v, itemStyle: { color: v >= s.anomaly_threshold ? AMBER : v < 0 ? BAD : "#6FB7A8", borderRadius: v >= s.anomaly_threshold ? [3, 3, 0, 0] : [0, 0, 0, 0] } }; }), barMaxWidth: 16 }]
  });

  /* ── 03 周度 + 口径对比 ── */
  var peakWeek = s.weeks.filter(function (w) { return w.is_peak; })[0] || s.weeks[0];
  var peakDay = s.anomaly_days.filter(function (a) { return a.is_peak; })[0];
  byId("capWeek").innerHTML = "第 " + peakWeek.week_no + " 周（" + peakWeek.label.replace("~", "～") +
    (peakDay ? "，含峰值日 " + md(peakDay.date) : "") + "）净增 " + peakWeek.net + " 为全期最高，之后热度脉冲式回落。";
  chart("cWeek").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { return ps[0].name.replace("~", "～") + "<br>净增 <b>" + ps[0].value + "</b> 人"; } },
    xAxis: { type: "category", data: s.weeks.map(function (w) { return w.label; }), axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "bar", data: s.weeks.map(function (w) { return { value: w.net, itemStyle: { color: w.is_peak ? PRIM : "#6FB7A8", borderRadius: [4, 4, 0, 0] } }; }), barMaxWidth: 44,
      label: { show: true, position: "top", color: INK, fontWeight: 700, fontSize: 12 } }]
  });

  byId("cmpHeadFull").textContent = "全量(" + s.days + "天)";
  byId("cmpHeadBase").textContent = "剔除异常(" + s.baseline_days + "天)";
  var cmpDefs = [["日均净增", s.avg.net, s.baseline_avg.net, s.deltas.net], ["日均吸粉", s.avg.new, s.baseline_avg.new, s.deltas.new],
    ["日均回访", s.avg.revisit, s.baseline_avg.revisit, s.deltas.revisit], ["日均脱粉", s.avg.lost, s.baseline_avg.lost, s.deltas.lost]];
  byId("cmpBody").innerHTML = cmpDefs.map(function (d) {
    return '<div class="row"><span class="r-n">' + d[0] + '</span><span class="r-d">' + fmt2(d[1]) + '</span><span class="r-c">' + fmt2(d[2]) + '</span><span class="r-p">' + sign1(d[3]) + '%</span></div>';
  }).join("");
  byId("cmpNote").innerHTML = "异常日合计贡献净增 <b>" + s.anomaly_net + "</b>（" + fmt1(s.anomaly_net_share_pct) + "%）、吸粉 <b>" +
    s.anomaly_new + "</b>（" + fmt1(s.anomaly_new_share_pct) + "%）。剔除后 <b>" + s.baseline_days + " 天累计净增 " +
    s.baseline_total_net + "</b>，日均 <b>" + fmt2(s.baseline_avg.net) + "</b>，可作为日常内容增长的稳健预期。";

  /* ── 04 散点 ── */
  byId("capScatter").innerHTML = "Pearson 相关 r≈" + fmt2(s.r_net_revisit) + "（与吸粉量 r≈" + fmt2(s.r_new_revisit) +
    "）：触达好 → 回访高 → 净增高，三者同频共振。";
  chart("cScatter").setOption({
    grid: grid,
    tooltip: { confine: true, formatter: function (p) { var i = p.dataIndex; return dates[i] + "<br>净增 " + nets[i] + " 人 · 回访 " + rets[i] + " 人次"; } },
    xAxis: { type: "value", name: "每日净增(人)", nameTextStyle: { color: MUT, fontSize: 12 }, axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    yAxis: { type: "value", name: "回访粉丝量(人次)", nameTextStyle: { color: MUT, fontSize: 12 }, axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "scatter", data: nets.map(function (v, i) { return [v, rets[i]]; }), symbolSize: function (v) { return 8 + Math.min(v[0], 42) * 0.25; }, itemStyle: { color: PRIM, opacity: .8 } }]
  });

  /* ── 05 异常表 ── */
  byId("anomalyMethod").innerHTML = "导入时逐行校验「净增 = 吸粉 − 脱粉」，总粉丝量缺失日按 上日总粉+净增 回填。分布右偏、由少数爆款日驱动，故以<b>净增 ≥ 3×中位数（中位数=" +
    s.median_net + "，阈值=" + s.anomaly_threshold + "）</b>判定为高增长异常日并剔除。";
  byId("anomalyBody").innerHTML = s.anomaly_days.map(function (a) {
    return '<tr><td>' + md(a.date) + '</td><td>' + a.net + '</td><td>' + a.new + '</td><td>' + a.lost + '</td><td>' + a.revisit + '</td><td>高增长异常' + (a.is_peak ? "（峰值）" : "") + "</td></tr>";
  }).join("");
  byId("anomalyNote").innerHTML = s.anomaly_days.length + " 个异常日合计净增 <b>" + s.anomaly_net + "</b>、占 " + s.days +
    " 天总净增 <b>" + fmt1(s.anomaly_net_share_pct) + "%</b>。剔除后使用 <b>" + s.baseline_days + " 天有效数据</b>计算常态基线（日均净增 " + fmt2(s.baseline_avg.net) + "）。";

  /* ── 06 建议 ── */
  var top3 = s.anomaly_days.slice().sort(function (a, b) { return b.net - a.net; }).slice(0, 3).map(function (a) { return md(a.date) + "(" + a.net + ")"; }).join("、");
  var lo = Math.floor(s.baseline_avg.net), hi = Math.ceil(s.baseline_avg.net) + 1;
  byId("rec1").innerHTML = '<span class="tag">基线</span><b>以 ' + lo + "～" + hi + " 人/日作为常态增长预期</b>。剔除爆款日后日均净增 " +
    fmt2(s.baseline_avg.net) + "，据此做内容排期与目标设定，避免被个别爆款日拉高预期、产生数据焦虑。";
  byId("rec2").innerHTML = '<span class="tag">复制</span><b>把爆款日打法沉淀为可复用 SOP</b>。复盘 ' + top3 + " 的选题、发布时间、话题与封面，提升非爆款日的常态基线。";
  byId("rec3").innerHTML = '<span class="tag">先行</span><b>盯住回访粉丝量作为早期预警</b>。回访与净增 r≈' + fmt2(s.r_net_revisit) + "，回访异动往往领先净增，可作为内容触达质量的先行指标。";
  byId("rec4").innerHTML = '<span class="tag">干预</span><b>建立净增≤0 日的干预机制</b>。连续 2 天及以上净增≤0 时，及时调整发布频率或选题方向，避免账号进入失速区间。';

  /* ── 07 明细表 (较归档版扩 投稿/播放 两列) ── */
  byId("detailTitle").textContent = "全量明细（" + s.days + " 天）";
  var head = ["日期", "投稿", "播放", "总粉丝量", "粉丝净增", "吸粉量", "脱粉量", "回访粉丝量"];
  var html = "<table><thead><tr>" + head.map(function (h) { return "<th>" + h + "</th>"; }).join("") + "</tr></thead><tbody>";
  rows.forEach(function (r) {
    var out = anomDates[r.date] ? ' class="out"' : "";
    html += "<tr" + out + "><td>" + md(r.date) + "</td><td>" + (r.posts == null ? "–" : r.posts) + "</td><td>" +
      (r.views == null ? "–" : r.views) + "</td><td>" + r.total_fans + "</td><td" +
      (r.fan_net < 0 ? ' class="neg"' : "") + ">" + r.fan_net + "</td><td>" + r.fan_new + "</td><td>" + r.fan_lost + "</td><td>" + r.revisit_fans + "</td></tr>";
  });
  byId("dataTable").innerHTML = html + "</tbody></table>";
  byId("sourceNote").innerHTML = "数据源 <b>data/analytics.db</b>（douyin_daily 表，" + s.days + " 天全量）· 更新入口: <b>python scripts/import_douyin_metrics.py &lt;数据表现导出.xlsx&gt;</b> · 所有指标均可在上表核验。";
}

function main() {
  fetch(API).then(function (res) {
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }).then(function (data) {
    if (!data.rows || !data.rows.length) { showEmpty("暂无数据 — data/analytics.db 为空"); return; }
    render(data);
  }).catch(function (err) {
    showEmpty("加载失败: " + err.message + " — 请确认后端已启动 (run_web.py, 端口 54321)");
  });
}

main();
