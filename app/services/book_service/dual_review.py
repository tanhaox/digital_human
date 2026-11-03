# -*- coding: utf-8 -*-
"""双维评审器 (1103 用户令, Z1 库层): 抖音推池维度 × 听感维度 — 全过才算出厂。

推池: D1 3s钩子 / D2 30s答案锚 / D3 1:30痒点 / D4 刺激点密度(>90s问句断层) /
      D5 收藏转发CTA / D6 评论钩 / D7 时长带(180-330s)
听感: L1 气口(>30字无逗句) / L2 多音字候选(待耳, 不拦) / L3 隐喻一套 /
      L4 把手自造(引号工具本体/口诀/对白豁免) / L5 框架词定义前置(引入段内) /
      L6 跨语梗
文本级评审: 真耳项(语调/断句实听/多音终验)仍须 TTS 后人耳, 本器拦文本可判的全部。
调用: episode_gen._dual_gate_pass (生成链出厂闭环) / scripts/dual_review.py (CLI)。
"""
from __future__ import annotations

import re

# 引号合法白名单域: 工具本体/口诀/对白 (1103 ep4 判读: 引号原生工具集豁免密度预算)
_TOOL_QUOTES = (
    '你应该', '你得做', '你觉得问题在哪', '哪里最需要改进', '这一点大家会了吗',
    '这一点我讲清楚了吗', '这块我讲清楚了吗', '你听明白了吗', '你听懂了吗',
    '是我没讲清楚，我再说一遍', '这是最好办法', '我说得对', '我让你说得出口',
    '你错了', '我讲清楚了吗', '你看起来有些生气。', '这主意很蠢',


)


def review(t: str, concept_names: list[str] | None = None) -> list[tuple[str, str, bool | None]]:
    """评审带【时间标签】的成稿 → [(维度, 详情, 通过)]。concept_names: 概念表
    (L5 用第一个概念做框架名锚; 缺省退 '第一组' 工具首句定位)。"""
    R: list[tuple[str, str, bool | None]] = []
    t2 = re.sub(r'【[^】]*】\n?', '', t)
    segs = re.split(r'【(\d+-\d+秒｜[^】]+)】', t)
    labeled: dict[str, str] = {}
    cur = None
    for s in segs:
        if re.match(r'\d+-\d+秒｜', s):
            cur = s.split('｜')[-1].rstrip('】')
            labeled[cur] = ''
        elif cur:
            labeled[cur] += s
    body = re.sub(r'\s+', '', t2)
    n = len(body)

    hook1 = next((l.strip() for l in t2.split('\n') if l.strip()), '')
    R.append(('D1 3s钩子', f'{len(hook1)}字 ' + ('问句' if '？' in hook1[:40] else '非问句'),
              '？' in hook1[:40]))
    zone = (labeled.get('钩子', '') + labeled.get('回顾+引入', ''))[:170]
    m = re.search(r'答案|这集(就|给你|拆)[^。]{0,18}|多[一二三四五两]句|给你[一二三四五]句', zone)
    R.append(('D2 30s答案锚', m.group(0)[:24] if m else '缺', bool(m)))
    rev = labeled.get('回顾+引入', '')
    itch = bool(re.search(r'你[^。！？]{0,30}？', rev[-90:]))
    R.append(('D3 1:30痒点', '回顾末对号问句在位' if itch else '回顾末90字无对号问句', itch))
    qs = [mm.start() for mm in re.finditer(r'[？?]', t2)]
    over450 = sum(1 for a, b in zip(qs, qs[1:]) if b - a > 450)
    R.append(('D4 刺激点密度', f'问句{len(qs)}处 >90s断层{over450}', over450 == 0))
    tail = labeled.get('总结+下期预告', '') or (list(labeled.values())[-1] if labeled else '')
    col = bool(re.search(r'收藏|留到|存下|翻出来', tail))
    fwd = bool(re.search(r'转给|转发', tail))
    R.append(('D5 收藏转发CTA', f'藏{"有" if col else "缺"}/转{"有" if fwd else "缺"}', col and fwd))
    cmt = bool(re.search(r'你站哪边|站个队|评论区聊聊|你选哪个', t2))
    R.append(('D6 评论钩', '在位' if cmt else '缺', cmt))
    dur = n / 5
    R.append(('D7 时长带', f'{dur:.0f}s', 180 <= dur <= 330))

    sents = [s.strip() for s in re.split(r'[。！？]', t2) if len(s.strip()) >= 2]
    long30 = [s for s in sents if len(s) > 30 and '，' not in s]
    R.append(('L1 气口', f'>30字无逗句{len(long30)}处', not long30))
    for s in long30[:3]:
        R.append((f'  L1待补', f'[{len(s)}字] {s[:36]}', False))
    poly = re.findall(r'全中|重说|重做|说着好|行长|得分|分开|中奖|长大', t2)
    R.append(('L2 多音字候选', str(poly) if poly else '无(终验在耳)', None))  # 待耳不拦
    metaphor = {w: t2.count(w) for w in ('尺子', '镜子', '地图', '开关', '杠杆', '指南针', '手术刀')
                if t2.count(w)}
    R.append(('L3 隐喻一套', str(metaphor) if metaphor else '零附加', True))
    qterms = re.findall(r'[「"“]([^」"”]{2,14})[」"”]', t2)
    coined = [x for x in qterms if x not in _TOOL_QUOTES and not re.search(r'[，。？！]$', x)
              and not any(x in w or w in x for w in _TOOL_QUOTES)]
    R.append(('L4 把手自造', f'{len(coined)}个 {coined[:4]}', len(coined) <= 2))
    fname = (concept_names or [None])[0]
    fd = t2.find(str(fname)) if fname else t2.find('低自我导向措辞')
    fu = t2.find('第一组，')
    ok5 = (fu - 80 <= fd <= fu + 100) if fu >= 0 else fd >= 0
    R.append(('L5 框架词前置', f'名@{fd} 工具首句@{fu}', ok5))
    R.append(('L6 跨语梗', '无' if not re.search(r'叫成?["“\']他["”\']', t2) else '有',
              not re.search(r'叫成?["“\']他["”\']', t2)))
    return R


def failed_items(R: list[tuple[str, str, bool | None]]) -> list[str]:
    return [f'{n}: {d}' for n, d, ok in R if ok is False]


def all_pass(R: list[tuple[str, str, bool | None]]) -> bool:
    return not failed_items(R)
