# -*- coding: utf-8 -*-
"""地缘提示词优化循环 — 批量成稿器 (2026-08-16).

循环协议: 用户收集地缘新闻原文 → 本脚本批量洗稿(laotan-geo) → 产出成稿文件
→ 用户逐篇复制给豆包(用固定评估指令保证可比) → 点评粘回 _豆包点评.txt
→ AI 汇总点评提炼新规则 → 迭代 laotan-geo.txt → 下一批。

用法:
    python scripts/geo_batch_review.py                    # 处理 data/geo_batch/in/ 全部 .txt
    python scripts/geo_batch_review.py --model pro        # 指定模型(flash/pro)
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import load_config, set_config

set_config(load_config())
from app.services.llm_service import LLMService

IN_DIR = Path("data/geo_batch/in")
OUT_ROOT = Path("data/geo_batch/out")

# 固定评估指令 — 每篇都用同一段问豆包, 保证点评口径可比 (循环协议核心)
DOUBAO_PROMPT = """请以抖音时政/国际历史内容审核专家身份，评估以下口播稿的发布风险（限流/审核不过/下架），逐维度输出：
① 高危词与情绪化表述（含网络梗、强主观定性词，引用原文）
② 主体泛化问题（是否把政客行为等同于国家/全体国民）
③ 数据来源标注缺失（军费/预算/经济数据是否有来源）
④ 官方话术风险（是否替官方表态/点赞官方机构/加码定性）
⑤ 事实与时间线疑点
⑥ 绝对化表述与极端预言（铁律/必然/注定/开战/崩盘）
⑦ 标题与结尾话术风险
每条给【原文引用→修改建议】。最后给整体风险等级：高/中/低。
以下是稿件全文：
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="flash", help="flash / pro")
    args = ap.parse_args()

    if not IN_DIR.exists() or not list(IN_DIR.glob("*.txt")):
        sys.exit(f"请先把地缘新闻原文 .txt 放进 {IN_DIR.resolve()}（文件名=话题名）")

    batch_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M")
    batch_dir.mkdir(parents=True, exist_ok=True)
    llm = LLMService(load_config())

    files = sorted(IN_DIR.glob("*.txt"))
    index_lines = [f"# 地缘批次 {batch_dir.name}（模型={args.model}, 模板=laotan-geo CR-0 版）\n",
                   "| # | 话题 | 字数 | 成稿 |\n|---|---|---|---|\n"]
    for i, f in enumerate(files, 1):
        raw = f.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        topic = f.stem
        print(f"[{i}/{len(files)}] 洗稿: {topic} ({len(raw)} 字原文)…")
        try:
            script = llm.rewrite_article(raw, prompt_template="laotan-geo", model=args.model)
        except Exception as exc:
            print(f"  ✗ 失败: {exc}")
            continue
        out_txt = batch_dir / f"{i:02d}_{topic}_成稿.txt"
        out_txt.write_text(script, encoding="utf-8")
        # 豆包点评模板: 成稿在前(方便整页复制), 评估指令在头部
        review = batch_dir / f"{i:02d}_{topic}_豆包点评.txt"
        review.write_text(
            f"【喂豆包: 把下面整段(指令+成稿)一起复制】\n{DOUBAO_PROMPT}\n{script}\n"
            f"\n{'='*60}\n【豆包点评粘到这行下面】\n",
            encoding="utf-8",
        )
        index_lines.append(f"| {i} | {topic} | {len(script)} | {out_txt.name} |")
        print(f"  ✓ {len(script)} 字 → {out_txt.name}")

    (batch_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(f"\n批次目录: {batch_dir.resolve()}")
    print("下一步: 每篇把「豆包点评.txt」上段整体复制给豆包 → 回复粘回该文件下段 → 全部完成后叫 AI 汇总提炼。")


if __name__ == "__main__":
    main()
