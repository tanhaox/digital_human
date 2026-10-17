// ── 任务面板: SSE 推流 + 轮询兜底, 生命周期统一管理 ──
// 为什么双通道: SSE 即时但存在浏览器自动重连/静默断开的边界, 轮询保证最终一致。
import { $ } from './util.js';
import { toast } from './ui.js';

const POLL_MS = 3000;      // 轮询间隔
const LOG_MAX = 200;       // 日志区保留行数
const HIDE_MS = 4000;      // 任务结束后面板驻留时长
const POLL_ERR_MAX = 10;   // 连续轮询失败上限 (熔断, 原实现会永远轮询下去)
const SSE_CLOSED = 2;      // EventSource.CLOSED (用数字常量, node 测试环境无此全局)

/** 面板相位中文标签 (原裸 phase 串 — redo 首次出现时顺手补全) */
const PHASE_LABEL = {
  plan: '🧭 规划', replan: '🔁 重规划', redo: '♻️ 列队重做',
  k2: '🎨 K2 批生图', h3: '🎬 H3 批生视频', brand: '🏔 品牌卡',
  facefix: '👤 修脸', textpass: '🔤 压字',
};

/** 真实 DOM 引用 (测试时由 deps.dom 注入桩对象替代) */
const realDom = () => ({
  panel: $('#jobpanel'), phase: $('#job-phase'), bar: $('#job-bar'),
  prog: $('#job-prog'), log: $('#log'),
});

/**
 * @param {{fetchJob:(id:string)=>Promise<any>,
 *          cancelJob:(id:string)=>Promise<any>,
 *          onFinished:()=>void,
 *          notify?:(msg:string, err?:boolean)=>void,   // 测试注入
 *          dom?:object, pollMs?:number, hideMs?:number, // 测试注入
 *          sseFactory?:(id:string)=>EventSource}} deps 依赖注入, 便于单测时替换
 */
export function createJobRunner({
  fetchJob, cancelJob, onFinished,
  notify = toast, dom = null, pollMs = POLL_MS, hideMs = HIDE_MS,
  sseFactory = id => new EventSource('/api/jobs/' + id + '/events'),
}) {
  let jobId = null, es = null, pollId = null, hideId = null;
  let errStreak = 0, finished = false;
  const el = () => dom || realDom();

  function appendLog(msg) {
    const { log } = el();
    log.textContent = (log.textContent + '\n' + msg).split('\n').slice(-LOG_MAX).join('\n');
    log.scrollTop = log.scrollHeight;
  }

  function setProg(p) {
    if (!p) return;
    const { bar, prog } = el();
    bar.style.width = p.total ? Math.round((p.done / p.total) * 100) + '%' : '0%';
    prog.textContent = `${p.done}/${p.total} · ${p.current ?? ''}`;
  }

  function showPanel(job) {
    // 关键: 新任务挂上前, 撤掉上一个任务遗留的"自动隐藏"定时器 (原版竞态 bug)
    clearTimeout(hideId);
    hideId = null;
    const { panel, phase, prog } = el();
    panel.hidden = false;
    phase.textContent =
      `${PHASE_LABEL[job.phase] || job.phase}${job.arc ? ' ' + job.arc : ''} · ${(job.book_id || '').slice(0, 8)} ep${job.ep}`;
    if (job.phase === 'plan' && !job.progress) {
      prog.textContent = 'LLM 规划中… 2-4 分钟 (静默属正常)';
    }
  }

  function openSSE() {
    es = sseFactory(jobId);
    es.onmessage = ev => {
      let d;
      try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === 'anim_progress') {
        setProg({ done: d.done, total: d.total, current: d.shot_id });
      } else if (d.type === 'anim_log' || d.type === 'anim_service') {
        appendLog(d.message);
      } else if (d.type === 'anim_done' || d.type === 'anim_error' || d.type === 'anim_cancelled') {
        appendLog(d.type === 'anim_done'
          ? '== 完成 ' + ((d.result && JSON.stringify(d.result)) || '')
          : `== ${d.type}: ${d.error || ''}`);
        el().phase.textContent = d.type === 'anim_done' ? '✅ 完成' : '❌ ' + d.type.replace('anim_', '');
        finish();
      }
    };
    // 服务端拒绝 (4xx/5xx) 时 EventSource 会永久自动重连刷屏 — CLOSED 即停手交给轮询
    es.onerror = () => {
      if (es.readyState === SSE_CLOSED) {
        appendLog('⚠ SSE 已关闭 — 轮询兜底');
        es = null;
      } else {
        appendLog('⚠ SSE 重连中 — 轮询兜底');
      }
    };
  }

  async function poll() {
    // 页面隐藏时空转纯属浪费 (node 测试环境无 document, 跳过该检查)
    if (!jobId || (typeof document !== 'undefined' && document.hidden)) return;
    try {
      const job = await fetchJob(jobId);
      errStreak = 0;
      setProg(job.progress);
      if (job.error) appendLog('ERROR ' + job.error);
      if (job.status !== 'running') finish();
    } catch (e) {
      // 1009 小坑3 修: 404 = job 已从服务端淘汰 (终态后被内存清理), 继续轮询
      // 纯刷 404 噪音 — 直接结算, 不再攒 errStreak (后台标签页实锤 12s×N 次)
      if (String(e.message || '').includes('404')) {
        appendLog('⚠ 任务已从服务端归档 (404) — 停止轮询');
        finish();
        return;
      }
      if (++errStreak >= POLL_ERR_MAX) {
        notify('任务状态轮询失败: ' + e.message, true);
        finish();
      }
    }
  }

  function finish() {
    if (finished) return;                    // SSE 与轮询双通道都会报终点, 只结算一次
    finished = true;
    detach();
    hideId = setTimeout(() => { el().panel.hidden = true; }, hideMs);
    onFinished();
  }

  function detach() {
    clearInterval(pollId); pollId = null;
    clearTimeout(hideId); hideId = null;     // 残留的隐藏定时器会误关下一个任务的面板
    if (es) { es.close(); es = null; }
    jobId = null;
  }

  return {
    /** 挂载任务 (幂等: 同一任务重复 attach 不重开通道/不重置定时器) */
    attach(job) {
      if (jobId === job.id) { showPanel(job); return; }
      detach();
      finished = false; errStreak = 0;
      jobId = job.id;
      showPanel(job);
      openSSE();
      pollId = setInterval(poll, pollMs);
    },
    activeId: () => jobId,
    cancel: async () => {
      if (!jobId) return;
      try { await cancelJob(jobId); notify('已请求停止 — 当前镜完成后中断'); }
      catch (e) { notify(e.message, true); }
    },
    appendLog,
    clearLog() { el().log.textContent = ''; },
    detach,
  };
}
