# -*- coding: utf-8 -*-
"""地缘素材双套装弹 (2026-08-28 用户单):
① 8 国宣传片 (美/日/菲/伊朗/俄/乌/韩/欧盟), 20-40 切片, 干净无字幕
② 领导人讲话 (集会/议会/竞选/G7/国情咨文), 每类 5-6 切片, 只要"在讲"画面
下载+切片阶段 (VPN on) → 打标 (VPN off, llama) → 只入无字幕片段。
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

# (实体标签, 搜索词, 条数, 最大时长秒) — 都市向 (2026-08-28 用户令: 不选风景, 要都市类)
COUNTRY_PROMOS = [
    ("美国",   "ytsearch2:New York City Manhattan 4K aerial", 2, 600),
    ("日本",   "ytsearch2:Tokyo city 4K aerial night", 2, 600),
    ("菲律宾", "ytsearch2:Manila Philippines city 4K", 2, 600),
    ("伊朗",   "ytsearch2:Tehran Iran city 4K", 2, 600),
    ("俄罗斯", "ytsearch2:Moscow city 4K aerial", 2, 600),
    ("乌克兰", "ytsearch2:Kyiv Ukraine city 4K", 2, 600),
    ("韩国",   "ytsearch2:Seoul city 4K aerial", 2, 600),
    ("欧盟",   "ytsearch2:Brussels EU quarter city 4K", 2, 600),
]

LEADER_SPEECHES = [
    ("特朗普",   "ytsearch2:Trump rally speech", 2, 3600),
    ("特朗普",   "ytsearch1:Trump address nation speech", 1, 3600),
    ("高市早苗", "ytsearch2:Takaichi Sanae speech", 2, 3600),
    ("普京",     "ytsearch2:Putin speech", 2, 3600),
    ("泽连斯基", "ytsearch2:Zelensky speech", 2, 3600),
    ("马科斯",   "ytsearch2:Marcos president speech Philippines", 2, 3600),
    ("李在明",   "ytsearch2:Lee Jae-myung speech", 2, 3600),
    ("佩泽希齐扬", "ytsearch2:Iran president Pezeshkian speech", 2, 3600),
    ("冯德莱恩", "ytsearch2:von der Leyen speech", 2, 3600),
    ("G7峰会",   "ytsearch2:G7 summit leaders gathering", 2, 3600),
]

# 0828 首轮未命中/零干净 → 补弹清单 (下次 VPN 连上时跑)
RETRY_ROUND = [
    ("美国",   "ytsearch3:New York City 4K timelapse", 3, 600),
    ("韩国",   "ytsearch3:Seoul Korea 4K timelapse", 3, 600),
    ("普京",   "ytsearch3:Putin speech highlights", 3, 3600),
    ("高市早苗", "ytsearch3:Takaichi press conference", 3, 3600),
    ("李在明", "ytsearch3:Lee Jae-myung inauguration speech", 3, 3600),
    ("马科斯", "ytsearch3:Marcos SONA speech", 3, 3600),
    ("G7峰会", "ytsearch3:G7 summit press conference", 3, 3600),
]

# VIP 轮 (2026-08-29 用户单): 过去一年高频新闻人物, 每国 3-4 人,
# 剔除临时角色(某司令/某政府人士); 每人 2-3 部多下 — 筛选是漏斗, 量大出净货。
# 已有富余的(特朗普54/冯德莱恩83)不重下, 只补缺口。
VIP_ROUND = [
    # 日本 (高市早苗是核心缺口 ×3)
    ("高市早苗", "ytsearch3:Takaichi Sanae press conference", 3, 3600),
    ("小泉进次郎", "ytsearch2:Koizumi Shinjiro speech", 2, 3600),
    ("石破茂",   "ytsearch2:Ishiba Shigeru speech", 2, 3600),
    # 俄罗斯 (普京缺口 ×3)
    ("普京",     "ytsearch3:Putin speech highlights", 3, 3600),
    ("拉夫罗夫", "ytsearch2:Lavrov press conference", 2, 3600),
    ("梅德韦杰夫", "ytsearch2:Medvedev interview speech", 2, 3600),
    # 韩国 (李在明缺口 ×3)
    ("李在明",   "ytsearch3:Lee Jae-myung speech press", 3, 3600),
    ("尹锡悦",   "ytsearch2:Yoon Suk Yeol speech", 2, 3600),
    # 美国 (补人物 + 补国家片)
    ("万斯",     "ytsearch2:JD Vance speech", 2, 3600),
    ("鲁比奥",   "ytsearch2:Marco Rubio press conference", 2, 3600),
    ("马斯克",   "ytsearch2:Elon Musk press conference", 2, 3600),
    ("美国",     "ytsearch3:New York City 4K timelapse", 3, 600),
    # 伊朗
    ("哈梅内伊", "ytsearch2:Khamenei speech", 2, 3600),
    ("阿拉格齐", "ytsearch2:Araghchi interview", 2, 3600),
    # 乌克兰 (泽连斯基只 2 条, 补)
    ("泽连斯基", "ytsearch3:Zelensky speech", 3, 3600),
    # 菲律宾
    ("马科斯",   "ytsearch2:Marcos speech", 2, 3600),
    ("莎拉杜特尔特", "ytsearch2:Sara Duterte speech", 2, 3600),
    # 欧盟 (法德 + EU 外交)
    ("马克龙",   "ytsearch2:Macron speech", 2, 3600),
    ("默茨",     "ytsearch2:Friedrich Merz speech", 2, 3600),
    ("卡拉斯",   "ytsearch2:Kaja Kallas speech", 2, 3600),
    # 北约
    ("吕特",     "ytsearch2:Mark Rutte NATO speech", 2, 3600),
    # 韩国国家片
    ("韩国",     "ytsearch3:Seoul Korea 4K timelapse", 3, 600),
    # G7 重试
    ("G7峰会",   "ytsearch3:G7 summit leaders", 3, 3600),
]


def run_retry() -> None:
    """补弹: 首轮未命中的实体换口径重搜 (需 VPN)."""
    for row in RETRY_ROUND:
        run(*row)


def run(entity: str, url: str, max_n: int, max_dur: int) -> None:
    print(f"\n━━━ {entity} | {url} ━━━", flush=True)
    subprocess.run(
        [sys.executable, str(ROOT / "sandbox/yt_ingest.py"), "batch",
         "--company", entity, "--url", url,
         "--max", str(max_n), "--max-duration", str(max_dur)],
        cwd=str(ROOT), timeout=1800)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "retry":
        run_retry()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "vip":
        for row in VIP_ROUND:
            run(*row)
        print("\nVIP 轮完成 → 断 VPN 后 tag --all + clean 入库", flush=True)
        return
    for row in COUNTRY_PROMOS + LEADER_SPEECHES:
        run(*row)
    print("\n全部下载+切片完成 → 等用户断 VPN 后跑 tag --all + clean-only 入库", flush=True)


if __name__ == "__main__":
    main()
