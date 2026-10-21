/* 账号分析看板 v2 — 数据全部来自 /api/analytics/daily (data/analytics.db),
 * 页面零内嵌数据。新增: 集数挂钩 / 视频侧 / 里程碑 / 预警 / lag 检验 / xlsx 上传导入 / 复盘摘要。
 * 口径由 app/services/analytics_service.py 计算; 集数挂钩表 scripts/seed_episode_links.py 维护。 */
"use strict";

var API = "/api/analytics/daily";
var INK = "#17202E", PRIM = "#0E6B5C", AMBER = "#C77A22", BAD = "#B4533F", MUT = "#6B7482", LINE = "#E4E7EC";
var BLUE = "#3b82f6", VIOLET = "#8b5cf6", GRAY = "#9aa3af";
var AXIS_LABEL = { color: "#6B7482", fontSize: 11 };
var grid = { left: 8, right: 12, top: 36, bottom: 8, containLabel: true };
var charts = [];

var SERIES_COLORS = { "HBO的内容战略": PRIM, "定位": BLUE, "卓有成效的管理者": VIOLET, "非正片": GRAY };
var SERIES_PREFIX = { "HBO的内容战略": "H", "定位": "定", "卓有成效的管理者": "卓", "非正片": "非" };

function fmt2(x) { return Number(x).toFixed(2); }
function fmt1(x) { return Number(x).toFixed(1); }
function sign1(x) { return (x >= 0 ? "+" : "") + fmt1(x); }
function pct1(x) { return fmt1(Number(x) * 100) + "%"; }
function md(d) { return String(d).slice(5); }
function byId(id) { return document.getElementById(id); }
function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
function toast(msg) {
  var t = byId("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(function () { t.classList.remove("show"); }, 3200);
}

function chart(el) { var ch = echarts.init(byId(el)); charts.push(ch); window.addEventListener("resize", function () { ch.resize(); }); return ch; }

function showEmpty(msg) {
  ["secFindings", "secEpisodes", "secPortrait", "secCharts", "secWeekly", "secVideo", "secScatter", "secAnomaly", "secRecs", "secDetail"].forEach(function (s) { byId(s).style.display = "none"; });
  byId("emptyState").style.display = "block";
  byId("heroSub").textContent = msg;
}

function render(data) {
  var s = data.summary, rows = data.rows, eps = data.episodes, v = data.video;
  var dates = rows.map(function (r) { return r.date; });
  var totals = rows.map(function (r) { return r.total_fans; });
  var nets = rows.map(function (r) { return r.fan_net; });
  var absorb = rows.map(function (r) { return r.fan_new; });
  var losts = rows.map(function (r) { return r.fan_lost; });
  var rets = rows.map(function (r) { return r.revisit_fans; });
  var viewsArr = rows.map(function (r) { return r.views; });
  var anomDates = {};
  s.anomaly_days.forEach(function (a) { anomDates[a.date] = true; });
  var epByDate = {};
  eps.forEach(function (e) { epByDate[e.date] = e; });
  window.__DATA = data; // 供复制复盘摘要

  /* ── 预警条 + hero ── */
  byId("alerts").innerHTML = data.alerts.map(function (a) {
    return '<div class="alert ' + a.level + '">' + esc(a.text) + "</div>";
  }).join("");
  byId("heroSub").textContent = "数据范围 " + s.from + " ～ " + s.to + "（" + s.days + " 天）· 已对异常高增长日识别并剔除 · 总粉丝从 " +
    s.start_total_fans + " 增长至 " + s.end_total_fans;
  var imp = data.freshness.last_import || "";
  byId("fresh").textContent = "数据截至 " + s.to + " · 上次导入 " + (imp ? imp.slice(0, 16).replace("T", " ") : "未知");
  byId("kTotal").innerHTML = s.end_total_fans + "<small> 人</small>";
  byId("kNet").innerHTML = fmt2(s.avg.net) + "<small> /日</small>";
  byId("kBase").innerHTML = fmt2(s.baseline_avg.net) + "<small> /日</small>";
  byId("kChurn").innerHTML = fmt2(s.churn_pct) + "<small>%</small>";

  /* ── 01 核心结论 ── */
  var nonpos = s.nonpos_days.map(function (d) { return md(d.date) + "(" + d.net + ")"; }).join("、");
  byId("f1d").innerHTML = s.days + " 天累计吸粉 <b>" + s.total_new + "</b>、累计脱粉仅 <b>" + s.total_lost +
    "</b>，脱粉/吸粉 <b>" + fmt2(s.churn_pct) + "%</b>；日均净增 <b>" + fmt2(s.avg.net) + "</b>，期末总粉丝 <b>" + s.end_total_fans + "</b>。";
  byId("f2d").innerHTML = s.anomaly_days.length + " 个高增长异常日全部是发布日（含收官集/新书首集），合计净增 <b>" + s.anomaly_net +
    "</b>，占总增长 <b>" + fmt1(s.anomaly_net_share_pct) + "%</b>；剔除后日均净增降至 <b>" + fmt2(s.baseline_avg.net) + "</b>（约 <b>" +
    fmt1(s.deltas.net) + "%</b>），常态基线明显更低。";
  byId("f3d").innerHTML = "回访粉丝量与净增<b>同频共振</b>（同日 r≈" + fmt2(s.r_net_revisit) +
    "；位移 ±1 天 r 均弱 — 先行性未证实，见 §07 检验）。爆款内容同时带动新增与回访。";
  byId("f4d").innerHTML = nonpos + " 共 <b>" + s.nonpos_days.length + " 天净增≤0</b>，需关注发布节奏与选题连续性。";

  /* ── 02 集数挂钩 ── */
  byId("capEps").innerHTML = "口径 = 发布日当天的账号吸粉 (单集真值含长尾, 见 docs/analytics/数据记录/ 各档 md) · 颜色: " +
    Object.keys(SERIES_COLORS).map(function (k) {
      return '<span class="sers" style="background:' + SERIES_COLORS[k] + '"></span>' + k;
    }).join(" ");
  chart("cEps").setOption({
    grid: grid,
    tooltip: { confine: true, formatter: function (p) {
      var e = eps[p.dataIndex];
      return e.date + " · " + e.series + " " + e.ep + "<br>" + esc(e.ep_title || "非正片") +
        "<br>当日: 播放 " + e.views + " · 净增 " + e.fan_net + " · 吸粉 <b>" + e.fan_new + "</b>" +
        (e.next_net != null ? "<br>次日尾: 净增 " + e.next_net : "");
    } },
    xAxis: { type: "category", data: eps.map(function (e) {
      return (SERIES_PREFIX[e.series] || "?") + (e.ep === "非正片" ? "" : e.ep.replace("ep", ""));
    }), axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "bar", data: eps.map(function (e) {
      return { value: e.fan_new, itemStyle: { color: SERIES_COLORS[e.series] || GRAY, borderRadius: [3, 3, 0, 0] } };
    }), barMaxWidth: 22,
      label: { show: true, position: "top", color: INK, fontSize: 10, fontWeight: 600 } }]
  });
  byId("epTableTitle").textContent = "挂钩明细（" + eps.length + " 个投稿日）";
  byId("epBody").innerHTML = eps.map(function (e) {
    var out = e.is_anomaly ? ' class="out"' : "";
    return "<tr" + out + "><td>" + md(e.date) + '</td><td class="l"><span class="sers" style="background:' +
      (SERIES_COLORS[e.series] || GRAY) + '"></span>' + e.series + "</td><td>" + e.ep + '</td><td class="l" title="' +
      esc(e.ep_title || "") + '">' + esc(e.ep_title || "—") + "</td><td>" + (e.publish_time || "—") + "</td><td>" +
      e.views + "</td><td" + (e.fan_net < 0 ? ' class="neg"' : "") + ">" + e.fan_net + "</td><td>" + e.fan_new +
      "</td><td>" + (e.next_net == null ? "—" : e.next_net) + '</td><td class="l">' + esc(e.note || "") + "</td></tr>";
  }).join("");
  byId("epNote").innerHTML = "投稿日 ↔ 集数 1:1 对齐（本档 " + eps.length + "/" + v.posts_total +
    "）；10-20 接档 = 卓有成效的管理者 ep1（48h 试验车）。新书接档后补 scripts/seed_episode_links.py 一行重跑。";

  /* ── 03 粉丝画像 ── */
  var pt = data.portrait && data.portrait[0];
  if (!pt) {
    byId("secPortrait").style.display = "none";
  } else {
    byId("ptTitle").textContent = "年龄分布（快照 " + pt.date + " · 账号级粉丝）";
    byId("capPt").textContent = "抖音不提供画像导出 — 本区数据 = 后台截图 → GLM-4.6V 转录 (scripts/seed_fan_portrait.py 入库)";
    var ages = pt.dims.age || [];
    var maxAge = Math.max.apply(null, ages.map(function (a) { return a.pct || 0; }));
    chart("cAge").setOption({
      grid: grid,
      tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var a = ages[ps[0].dataIndex]; return a.label + " 岁: <b>" + a.pct + "%</b>" + (a.note ? "（" + a.note + "）" : ""); } },
      xAxis: { type: "category", data: ages.map(function (a) { return a.label; }), axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL) },
      yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL, { formatter: "{value}%" }), splitLine: { lineStyle: { color: "#F0F1F4" } }, max: 100 },
      series: [{ type: "bar", data: ages.map(function (a) { return { value: a.pct, itemStyle: { color: a.pct === maxAge ? PRIM : "#6FB7A8", borderRadius: [4, 4, 0, 0] } }; }), barMaxWidth: 48,
        label: { show: true, position: "top", color: INK, fontWeight: 700, formatter: "{c}%" } }]
    });
    var its = (pt.dims.interest || []).slice().sort(function (a, b) { return a.pct - b.pct; });
    chart("cInterest").setOption({
      grid: { left: 8, right: 40, top: 12, bottom: 8, containLabel: true },
      tooltip: { confine: true, formatter: function (p) { var a = its[p.dataIndex]; return a.label + ": <b>" + a.pct + "%</b>" + (a.note ? "（" + a.note + "）" : ""); } },
      xAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL, { formatter: "{value}%" }), splitLine: { lineStyle: { color: "#F0F1F4" } } },
      yAxis: { type: "category", data: its.map(function (a) { return a.label; }), axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { fontSize: 12 }) },
      series: [{ type: "bar", data: its.map(function (a) { return { value: a.pct, itemStyle: { color: AMBER, borderRadius: [0, 4, 4, 0] } }; }), barMaxWidth: 20,
        label: { show: true, position: "right", color: INK, fontWeight: 600, formatter: "{c}%" } }]
    });
    function dimLine(dim, emptyText) {
      var g = pt.dims[dim];
      if (!g) return "—";
      var withNums = g.filter(function (x) { return x.pct != null; });
      if (!withNums.length) return emptyText || "无数字";
      return withNums.map(function (x) { return x.label + " " + x.pct + "%"; }).join(" · ");
    }
    var mid3to5 = (ages.find(function (a) { return a.label === "31-40"; }) || {}).pct || 0;
    var mid4to5 = (ages.find(function (a) { return a.label === "41-50"; }) || {}).pct || 0;
    byId("ptBody").innerHTML =
      "<tr><td>地域</td><td class=\"l\">" + dimLine("region") + "</td></tr>" +
      "<tr><td>设备</td><td class=\"l\">" + dimLine("device") + "</td></tr>" +
      "<tr><td>性别</td><td class=\"l\">" + dimLine("gender", "环形图无数字 (截图不悬停不显示); 视觉男性显著占优 — 0929 单集观众男95% 参照") + "</td></tr>" +
      "<tr><td>活跃度</td><td class=\"l\">" + dimLine("activity", "环形图无数字; 视觉重度最大 (8折有效粉代理指标, 待悬停截图补真值)") + "</td></tr>" +
      "<tr><td>热词</td><td class=\"l\">粉丝量未达 1000 暂无热词分析 (后台原文) — 千粉毕业线解锁</td></tr>";
    byId("ptNote").innerHTML = "跨快照同构核对 (判据① 依据): 随拍 0929 ep6 观众 <b>36%</b> → 1021 全账号粉 <b>35%</b> ✓ 同构; " +
      "31-50 岁: 0929 单集观众 ≈90% → 本快照全账号粉 <b>" + (mid3to5 + mid4to5) + "%</b> (口径不同仅记录)。 " +
      "下一份画像快照 (建议 500 粉节点) 再现同构即可触发判据① P1 判定。";
  }

  /* ── 04 累计 + 里程碑 + 每日净增 ── */
  byId("hTotal").textContent = "总粉丝累计增长（" + s.start_total_fans + " → " + s.end_total_fans + "）";
  byId("etaChips").innerHTML = data.milestones.map(function (m) {
    if (m.done) return '<span class="chip ms done">✓ ' + m.target + "</span>";
    return '<span class="chip ms"><b>' + m.target + "粉</b> 差 " + m.remaining + ' · <span class="eta">ETA 基线 ' +
      md(m.eta_base_date) + " / 全量 " + md(m.eta_full_date) + "</span></span>";
  }).join("");
  var msLines = data.milestones.filter(function (m) { return !m.done; }).map(function (m) {
    return { yAxis: m.target, label: { formatter: m.target + "", color: MUT, fontSize: 10, position: "insideEndTop" },
      lineStyle: { color: "#b9c4cc", type: "dashed" } };
  });
  chart("cTotal").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var i = ps[0].dataIndex; return dates[i] + "<br>总粉丝 <b>" + totals[i] + "</b> 人<br>净增 " + nets[i] + " 人" + (epByDate[dates[i]] ? "<br>发布: " + epByDate[dates[i]].ep : ""); } },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { interval: 5, formatter: md }) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "line", data: totals, smooth: true, symbol: "none", lineStyle: { width: 3, color: PRIM },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(14,107,92,.28)" }, { offset: 1, color: "rgba(14,107,92,.02)" }]) },
      itemStyle: { color: PRIM }, markLine: { symbol: "none", silent: true, data: msLines } }]
  });
  byId("capNet").innerHTML = "琥珀色为识别出的 " + s.anomaly_days.length + " 个高增长异常日（净增 ≥ " +
    s.anomaly_threshold + " = 3×中位数）；红色为负增长日；柱顶标注 = 当日发布集。";
  chart("cNet").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var i = ps[0].dataIndex; var o = nets[i] >= s.anomaly_threshold ? "（高增长异常日）" : nets[i] < 0 ? "（负增长）" : ""; var ep = epByDate[dates[i]]; return dates[i] + (ep ? " · " + ep.ep : "") + "<br>净增 <b>" + nets[i] + "</b> 人" + o + "<br>吸粉 " + absorb[i] + " · 脱粉 " + losts[i] + " · 回访 " + rets[i]; } },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { interval: 5, formatter: md }) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } }, axisLine: { show: false } },
    series: [{ type: "bar", data: nets.map(function (val, i) { return { value: val, itemStyle: { color: val >= s.anomaly_threshold ? AMBER : val < 0 ? BAD : "#6FB7A8", borderRadius: val >= s.anomaly_threshold ? [3, 3, 0, 0] : [0, 0, 0, 0] } }; }), barMaxWidth: 16,
      markPoint: { symbol: "circle", symbolSize: 1, label: { show: true, position: "top", color: INK, fontSize: 10, fontWeight: 700 },
        data: eps.filter(function (e) { return anomDates[e.date]; }).map(function (e) { return { coord: [e.date, e.fan_net], name: (SERIES_PREFIX[e.series] || "") + e.ep.replace("ep", "") }; }),
        labelLayout: { hideOverlap: false } } }]
  });

  /* ── 04 周度 + 口径对比 ── */
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
    return '<div class="row"><span class="r-n">' + d[0] + '</span><span class="r-d">' + fmt2(d[1]) + '</span><span class="r-c">' + fmt2(d[2]) + '</span><span class="r-p">' + sign1(d[3]) + "%</span></div>";
  }).join("");
  byId("cmpNote").innerHTML = "异常日合计贡献净增 <b>" + s.anomaly_net + "</b>（" + fmt1(s.anomaly_net_share_pct) + "%）、吸粉 <b>" +
    s.anomaly_new + "</b>（" + fmt1(s.anomaly_new_share_pct) + "%）。剔除后 <b>" + s.baseline_days + " 天累计净增 " +
    s.baseline_total_net + "</b>，日均 <b>" + fmt2(s.baseline_avg.net) + "</b>，可作为日常内容增长的稳健预期。";

  /* ── 05 视频侧 ── */
  byId("vChips").innerHTML =
    '<span class="chip">5s完播 <b>' + pct1(v.sec5_rate) + "</b></span>" +
    '<span class="chip">2s跳出 <b>' + pct1(v.sec2_bounce) + "</b></span>" +
    '<span class="chip">均播放时长 <b>' + fmt1(v.avg_watch_sec) + "s</b></span>" +
    '<span class="chip">互动率 <b>' + fmt2(v.interaction_rate) + "%</b></span>" +
    '<span class="chip">吸粉效率 <b>' + fmt1(v.fan_efficiency) + " 粉/万播</b></span>" +
    '<span class="chip">互动量 <b>' + v.interactions + "</b>（赞 " + v.likes + " · 评 " + v.comments + " · 享 " + v.shares + "）</span>";
  chart("cViews").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var i = ps[0].dataIndex; var ep = epByDate[dates[i]]; return dates[i] + (ep ? " · 发布 " + ep.ep : "") + "<br>播放 <b>" + viewsArr[i] + "</b> · 投稿 " + (rows[i].posts || 0); } },
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL, { interval: 5, formatter: md }) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "bar", data: viewsArr.map(function (val, i) { return { value: val, itemStyle: { color: (rows[i].posts || 0) > 0 ? AMBER : "#c3cdd6", borderRadius: [3, 3, 0, 0] } }; }), barMaxWidth: 16,
      markLine: { symbol: "none", silent: true, lineStyle: { color: MUT, type: "dashed" },
        label: { formatter: "日均 " + Math.round(v.views_total / s.days), color: MUT, fontSize: 10 },
        data: [{ yAxis: Math.round(v.views_total / s.days) }] } }]
  });
  var pg = v.post_group, ng = v.nopost_group;
  byId("pvHeadPost").textContent = "投稿日(" + pg.days + "天)";
  byId("pvHeadNo").textContent = "无投稿日(" + ng.days + "天)";
  var pvDefs = [
    ["日均播放", pg.avg_views, ng.avg_views],
    ["日均吸粉", pg.avg_fan_new, ng.avg_fan_new],
    ["吸粉合计", pg.fan_new_total, ng.fan_new_total],
    ["5s完播(加权)", pct1(pg.sec5_rate), pct1(ng.sec5_rate)],
    ["2s跳出(加权)", pct1(pg.sec2_bounce), pct1(ng.sec2_bounce)],
  ];
  byId("pvBody").innerHTML = pvDefs.map(function (d) {
    return '<div class="row"><span class="r-n">' + d[0] + '</span><span class="r-d">' + d[1] + '</span><span class="r-c">' + d[2] + "</span></div>";
  }).join("");
  // 倍率行 (播放/吸粉) 单独算
  var viewX = (ng.avg_views ? pg.avg_views / ng.avg_views : 0).toFixed(1);
  var newX = (ng.avg_fan_new ? pg.avg_fan_new / ng.avg_fan_new : 0).toFixed(1);
  byId("pvBody").insertAdjacentHTML("beforeend",
    '<div class="row"><span class="r-n">倍率 (投稿÷无投稿)</span><span class="r-d">播放 ×' + viewX + '</span><span class="r-c">吸粉 ×' + newX + "</span></div>");
  byId("pvNote").innerHTML = "投稿日日均播放是无投稿日的 <b>×" + viewX + "</b>、吸粉 <b>×" + newX +
    "</b>；无投稿日底流 (账号资产读数) ≈ <b>" + ng.avg_views + " 播放/日</b>。发布日数值含新集首日主导 (数据记录1005 §五.3: 首日≈单集总量88%)。";
  chart("cWeekday").setOption({
    grid: grid,
    tooltip: { trigger: "axis", confine: true, formatter: function (ps) { var w = data.weekday[ps[0].dataIndex]; return w.label + "（" + w.days + " 天）<br>日均净增 <b>" + w.avg_net + "</b> · 日均吸粉 " + w.avg_fan_new + "<br>日均播放 " + w.avg_views; } },
    xAxis: { type: "category", data: data.weekday.map(function (w) { return w.label; }), axisLine: { lineStyle: { color: LINE } }, axisTick: { show: false }, axisLabel: Object.assign({}, AXIS_LABEL) },
    yAxis: { type: "value", axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "bar", data: data.weekday.map(function (w) { return { value: w.avg_net, itemStyle: { color: PRIM, borderRadius: [4, 4, 0, 0] } }; }), barMaxWidth: 36,
      label: { show: true, position: "top", color: INK, fontSize: 11 } }]
  });

  /* ── 06 回访 + lag ── */
  byId("capScatter").innerHTML = "Pearson 相关 r≈" + fmt2(s.r_net_revisit) + "（与吸粉量 r≈" + fmt2(s.r_new_revisit) +
    "）：触达好 → 回访高 → 净增高，三者同频共振。";
  chart("cScatter").setOption({
    grid: grid,
    tooltip: { confine: true, formatter: function (p) { var i = p.dataIndex; return dates[i] + "<br>净增 " + nets[i] + " 人 · 回访 " + rets[i] + " 人次"; } },
    xAxis: { type: "value", name: "每日净增(人)", nameTextStyle: { color: MUT, fontSize: 12 }, axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    yAxis: { type: "value", name: "回访粉丝量(人次)", nameTextStyle: { color: MUT, fontSize: 12 }, axisLabel: Object.assign({}, AXIS_LABEL), splitLine: { lineStyle: { color: "#F0F1F4" } } },
    series: [{ type: "scatter", data: nets.map(function (val, i) { return [val, rets[i]]; }), symbolSize: function (val) { return 8 + Math.min(val[0], 42) * 0.25; }, itemStyle: { color: PRIM, opacity: .8 } }]
  });
  var lagLabels = { "-2": "回访 → 净增 −2 天", "-1": "回访 → 净增 −1 天", "0": "同日", "1": "回访 → 净增 +1 天", "2": "回访 → 净增 +2 天" };
  var maxR = Math.max.apply(null, data.lag.map(function (x) { return x.r; }));
  byId("lagBody").innerHTML = data.lag.map(function (x) {
    var hi = x.r === maxR ? ' style="font-weight:700;color:' + PRIM + '"' : "";
    return "<tr><td>" + lagLabels[String(x.lag)] + "</td><td>r(回访[t], 净增[t+k])</td><td" + hi + ">" + fmt2(x.r) + "</td><td>" + x.n + "</td></tr>";
  }).join("");
  byId("lagNote").innerHTML = "实测 <b>同频主导</b>（k=0 的 r 最高），位移 ±1 天相关性均弱 — 「回访先行」未获数据支持，" +
    "回访异动与净增<b>同日共振</b>，应作为当日触达质量的共振信号使用（1021 lag 检验修正）。";

  /* ── 07 异常表 ── */
  byId("anomalyMethod").innerHTML = "导入时逐行校验「净增 = 吸粉 − 脱粉」，总粉丝量缺失日按 上日总粉+净增 回填。分布右偏、由少数爆款日驱动，故以<b>净增 ≥ 3×中位数（中位数=" +
    s.median_net + "，阈值=" + s.anomaly_threshold + "）</b>判定为高增长异常日并剔除。";
  byId("anomalyBody").innerHTML = s.anomaly_days.map(function (a) {
    var ep = epByDate[a.date];
    return "<tr><td>" + md(a.date) + "</td><td>" + a.net + "</td><td>" + a.new + "</td><td>" + a.lost + "</td><td>" + a.revisit +
      "</td><td>高增长异常" + (a.is_peak ? "（峰值）" : "") + '</td><td class="l">' + (ep ? ep.series + " " + ep.ep : "—") + "</td></tr>";
  }).join("");
  byId("anomalyNote").innerHTML = s.anomaly_days.length + " 个异常日合计净增 <b>" + s.anomaly_net + "</b>、占 " + s.days +
    " 天总净增 <b>" + fmt1(s.anomaly_net_share_pct) + "%</b>。剔除后使用 <b>" + s.baseline_days + " 天有效数据</b>计算常态基线（日均净增 " + fmt2(s.baseline_avg.net) + "）。";

  /* ── 08 建议 ── */
  var top3 = s.anomaly_days.slice().sort(function (a, b) { return b.net - a.net; }).slice(0, 3).map(function (a) {
    var ep = epByDate[a.date];
    return md(a.date) + "(" + a.net + (ep ? " · " + ep.ep : "") + ")";
  }).join("、");
  var lo = Math.floor(s.baseline_avg.net), hi2 = Math.ceil(s.baseline_avg.net) + 1;
  byId("rec1").innerHTML = '<span class="tag">基线</span><b>以 ' + lo + "～" + hi2 + " 人/日作为常态增长预期</b>。剔除爆款日后日均净增 " +
    fmt2(s.baseline_avg.net) + "，据此做内容排期与目标设定，避免被个别爆款日拉高预期、产生数据焦虑。";
  byId("rec2").innerHTML = '<span class="tag">复制</span><b>把爆款日打法沉淀为可复用 SOP</b>。复盘 ' + top3 + " 的选题、发布时间、话题与封面，提升非爆款日的常态基线。";
  byId("rec3").innerHTML = '<span class="tag">共振</span><b>盯住回访粉丝量作为当日触达质量信号</b>。回访与净增同日 r≈' + fmt2(s.r_net_revisit) +
    '（先行性未证实），回访异动 = 当日内容触达质量的即时读数。';
  byId("rec4").innerHTML = '<span class="tag">干预</span><b>建立净增≤0 日的干预机制</b>。连续 2 天及以上净增≤0 时，及时调整发布频率或选题方向，避免账号进入失速区间。';

  /* ── 09 明细 ── */
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
  byId("sourceNote").innerHTML = "数据源 <b>data/analytics.db</b>（douyin_daily " + s.days + " 天全量）· 更新: 页面上传或 <b>python scripts/import_douyin_metrics.py &lt;导出.xlsx&gt;</b> · 所有指标均可在上表核验。";
}

/* ── 复盘摘要 (贴 docs/analytics/数据记录/ 用) ── */
function buildMd(data) {
  var s = data.summary, v = data.video, eps = data.episodes;
  var seriesProgress = {};
  eps.forEach(function (e) {
    if (e.ep === "非正片") return;
    var p = seriesProgress[e.series] || { n: 0, last: "", lastEp: "" };
    p.n += 1; p.last = e.date; p.lastEp = e.ep;
    seriesProgress[e.series] = p;
  });
  var now = new Date();
  var pad = function (x) { return (x < 10 ? "0" : "") + x; };
  var stamp = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate()) + " " + pad(now.getHours()) + ":" + pad(now.getMinutes());
  var lines = [
    "## 账号看板快照 " + stamp,
    "",
    "- 数据范围 " + s.from + "~" + s.to + "（" + s.days + "天）· 期末总粉 **" + s.end_total_fans + "** · 日均净增 全量 " + fmt2(s.avg.net) + " / 常态基线 **" + fmt2(s.baseline_avg.net) + "** · 脱粉/吸粉 " + fmt2(s.churn_pct) + "%",
    "- 异常日(≥3×中位=" + s.anomaly_threshold + "): " + s.anomaly_days.map(function (a) { return md(a.date) + "(" + a.net + ")"; }).join("、") + " 合计 " + s.anomaly_net + "（" + fmt1(s.anomaly_net_share_pct) + "%）",
    "- 挂钩: " + Object.keys(seriesProgress).map(function (k) { return k + " " + seriesProgress[k].n + "集(至" + seriesProgress[k].lastEp + ")"; }).join(" · "),
    "- 视频侧: 投稿日日均播放 " + v.post_group.avg_views + " vs 无投稿 " + v.nopost_group.avg_views + " · 吸粉效率 " + fmt1(v.fan_efficiency) + "粉/万播 · 5s完播 " + pct1(v.sec5_rate) + " · 2s跳出 " + pct1(v.sec2_bounce),
    "- 回访: 同频 r=" + fmt2(s.r_net_revisit) + "（位移±1 弱, 先行未证实）",
  ];
  var ms = data.milestones.filter(function (m) { return !m.done; });
  if (ms.length) lines.push("- 里程碑: " + ms.map(function (m) { return m.target + " 差" + m.remaining + "(基线ETA " + m.eta_base_date + ")"; }).join(" · "));
  lines.push(data.alerts.length ? "- 预警: " + data.alerts.map(function (a) { return a.text; }).join("；") : "- 预警: 无");
  return lines.join("\n");
}

function copyMd() {
  if (!window.__DATA) { toast("数据未加载"); return; }
  var text = buildMd(window.__DATA);
  navigator.clipboard.writeText(text).then(function () {
    toast("复盘摘要已复制 — 可直接贴入 docs/analytics/数据记录/");
  }, function () {
    toast("复制失败 (剪贴板权限) — 摘要已打印到新窗口");
    var w = window.open("", "_blank");
    if (w) w.document.write("<pre>" + text.replace(/</g, "&lt;") + "</pre>");
  });
}

function importFile(file) {
  if (!file) return;
  var fd = new FormData();
  fd.append("file", file);
  toast("导入中: " + file.name + " …");
  fetch("/api/analytics/import", { method: "POST", body: fd }).then(function (res) {
    return res.json().then(function (j) { return { ok: res.ok, j: j }; });
  }).then(function (r) {
    if (!r.ok) throw new Error(r.j.detail || "导入失败");
    toast("导入 " + r.j.imported + " 行 (新 " + r.j.new + " / 刷新 " + r.j.refreshed + ") · 库现存 " + r.j.db_days + " 行 — 刷新中");
    setTimeout(function () { location.reload(); }, 1400);
  }).catch(function (err) {
    toast("导入失败: " + err.message);
  });
}

function main() {
  byId("btnImport").addEventListener("click", function () { byId("fileInput").click(); });
  byId("fileInput").addEventListener("change", function () {
    importFile(this.files[0]);
    this.value = "";
  });
  byId("btnCopyMd").addEventListener("click", copyMd);
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
