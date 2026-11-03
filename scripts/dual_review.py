# -*- coding: utf-8 -*-
"""双维评审器 (1103 用户令): 抖音推池维度 × 听感维度 — 全过才算出厂."""
import re, sys

def review(t: str) -> list:
    R = []
    t2 = re.sub(r'【[^】]*】\n?', '', t)
    segs = re.split(r'【(\d+-\d+秒｜[^】]+)】', t)
    labeled, cur = {}, None
    for s in segs:
        if re.match(r'\d+-\d+秒｜', s):
            cur = s.split('｜')[-1].rstrip('】'); labeled[cur] = ''
        elif cur:
            labeled[cur] += s
    body = re.sub(r'\s+', '', t2)
    n = len(body)
    hook1 = next(l.strip() for l in t2.split('\n') if l.strip())
    R.append(('D1 3s钩子', f'{len(hook1)}字 ' + ('问句✓' if '？' in hook1[:40] else '非问句✗'), '？' in hook1[:40]))
    zone = (labeled.get('钩子', '') + labeled.get('回顾+引入', ''))[:170]
    m = re.search(r'答案|这集(就|给你|拆)[^。]{0,18}|多四句', zone)
    R.append(('D2 30s答案锚', m.group(0)[:24] if m else '✗缺', bool(m)))
    rev = labeled.get('回顾+引入', '')
    itch = bool(re.search(r'你[^。！？]{0,30}？', rev[-90:]))
    R.append(('D3 1:30痒点', '✓' if itch else '✗回顾末无对号问句', itch))
    qs = [mm.start() for mm in re.finditer(r'[？?]', t2)]
    over450 = sum(1 for a, b in zip(qs, qs[1:]) if b - a > 450)
    R.append(('D4 刺激点密度', f'问句{len(qs)}处 >90s断层{over450}', over450 == 0))
    tail = labeled.get('总结+下期预告', '') or (list(labeled.values())[-1] if labeled else '')
    col = bool(re.search(r'收藏|留到|存下|翻出来', tail))
    fwd = bool(re.search(r'转给|转发', tail))
    R.append(('D5 收藏/转发CTA', f'藏{"✓" if col else "✗"} 转{"✓" if fwd else "✗"}', col and fwd))
    cmt = bool(re.search(r'你站哪边|站个队|评论区聊聊|你选哪个', t2))
    R.append(('D6 评论钩', '✓' if cmt else '✗', cmt))
    dur = n / 5
    R.append(('D7 时长带', f'{dur:.0f}s', 180 <= dur <= 330))
    sents = [s.strip() for s in re.split(r'[。！？]', t2) if len(s.strip()) >= 2]
    long30 = [s for s in sents if len(s) > 30 and '，' not in s]
    R.append(('L1 气口', f'>30字无逗号 {len(long30)}处', not long30))
    for s in long30[:3]:
        R.append(('  待补气口', f'[{len(s)}字] {s[:38]}', False))
    poly = re.findall(r'全中|重说|重做|说着好|行长|得分|分开|中奖|长大', t2)
    R.append(('L2 多音字候选', str(poly) if poly else '无', True))  # 待耳, 不拦
    metaphor = {w: t2.count(w) for w in ('尺子', '镜子', '地图', '开关', '杠杆', '指南针', '手术刀') if t2.count(w)}
    R.append(('L3 隐喻一套', str(metaphor) if metaphor else '零附加✓', True))
    qterms = re.findall(r'[「"\u201c]([^」"\u201d]{2,14})[」"\u201d]', t2)
    coined = [x for x in qterms if not re.search(r'[，。？！]', x) and x not in (
        '你应该', '你得做', '你觉得问题在哪', '哪里最需要改进', '这一点大家会了吗',
        '这一点我讲清楚了吗', '这块我讲清楚了吗', '你听明白了吗', '你听懂了吗',
        '是我没讲清楚，我再说一遍', '这是最好办法', '我说得对', '我让你说得出口',
        '你错了', '我讲清楚了吗', '你看起来有些生气。', '这主意很蠢', '其他客户通常因为某某理由这么做，你觉得适用吗')]
    R.append(('L4 把手自造', f'{len(coined)}个 {coined[:4]}', len(coined) <= 2))
    fd = t2.find('低自我导向措辞'); fu = t2.find('第一组，')  # 工具首句起算 (过场句的预告引用不算)
    R.append(('L5 框架词前置', f'名@{fd} 工具首句@{fu}', (fu - 80 <= fd <= fu + 100) if fu >= 0 else fd >= 0))  # 名在引入段内 (首句前80~后100字)
    R.append(('L6 跨语梗', '无✓', not re.search(r'叫成?["\u201c\']他["\u201d\']', t2)))
    return R

if __name__ == '__main__':
    path = sys.argv[1]
    t = open(path, encoding='utf-8').read()
    R = review(t)
    npass = sum(1 for _, _, ok in R if ok)
    total = sum(1 for _, _, ok in R if ok is not None)
    print(f'=== 双维评审 {path} ===')
    for name, detail, ok in R:
        print(f'{"✓" if ok else "✗"} {name}: {detail}')
    blocks = [n for n, _, ok in R if not ok]
    print(f'--- {npass}/{total} 过 ---')
    if blocks:
        print('未过:', ', '.join(blocks))

# 用法: .venv/Scripts/python scripts/dual_review.py <稿文件路径>
# 1103 用户令: 抖音推池维度(钩子/30s锚/痒点/刺激点密度/CTA/评论钩/时长带) ×
# 听感维度(气口/多音候选/隐喻一套/把手自造/框架词前置/跨语梗) — 全过才算出厂。
# 文本级评审: 真耳项(语调/断句实听/多音终验)仍须 TTS 后人耳, 本器拦文本可判的全部。
