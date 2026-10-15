# -*- coding: utf-8 -*-
"""证据图管线② — 扫图 / 质检 / 选图 .

链路位置: 管线① (url_fetcher.extract_images, 抓取时顺手提 URL) 之后 —
素材包条目页候选图 → 下载缓存 → 9B VLM 打标 → 合格图池 → 段级匹配选图。

四个入口:
- scan_package_images: 建包后台自动扫 / 手动补扫共用 (写回 material_items.images_json)
- collect_evidence_pool: 导演闸门与 slot 执行共用 (script → 合格证据图池)
- is_evidence_claim: 用户硬条件的代码闸门 — 只有测试/比较段才配证据图
- pick_image_for_slot: 数字重合最强信号的段级选图

失败语义: 全链 best-effort — 单图下载失败丢弃、VLM 不可用 fail-closed
(存 vlm:null 不进池), 扫图失败绝不拖垮素材包主流程。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests
from sqlalchemy.orm import Session

from ..models import Article, MaterialItem, MaterialPackage
from .tts_verify import _num_to_reading
from .url_fetcher import extract_images, fetch_html

logger = logging.getLogger(__name__)

__all__ = [
    "scan_package_images",
    "collect_evidence_pool",
    "is_evidence_claim",
    "pick_image_for_slot",
]

# ── 扫图预算 ──
_SCAN_ITEM_CAP = 20        # 单包最多扫的条目页数 (article 条目优先占位)
_SCAN_PER_ITEM = 4         # 每条目页候选图上限
_SCAN_PER_ARTICLE = 8      # 原文条目 (用户搜图总闸抓的) 候选上限 = extract cap, 全量进扫
_SCAN_TOTAL_CAP = 24       # 整包候选总上限
_DL_TIMEOUT = (5, 20)      # 连接/读取超时
_DL_MAX_BYTES = 12 * 1024 * 1024  # 单图 12MB 上限
_IMG_MIN_W = 400           # Pillow 实测宽度下限 (太小没法看)
_VLM_BUDGET_SEC = 120.0    # 整包 VLM 打标墙钟预算 (超时后剩余图存 vlm:null)
_MIN_QUALITY = 4           # 进池最低画质分

_PROGRESS_CB = Callable[[str, dict[str, Any]], None] | None

# ── 语义闸门: 测试/比较词集 (数字 + 词集双条件, 见 is_evidence_claim) ──
# "比"不单列 (比如/比划误伤), 走 _CLAIM_CMP 句式; "元/块/折"同理并入组合词。
_CLAIM_KW = re.compile(
    r"测试|基准|跑分|榜单|排名|排第|分数|成绩|对比|比较|超过|领先|反超|追平|"
    r"价格|定价|成本|单价|便宜|贵|倍|美元|欧元|英镑|块钱|个百分点|百分点|"
    r"满分|考了|评测|评分|性价比|胜过|碾压|%"
)
_CLAIM_CMP = re.compile(
    r"比[^，。；！？、\s]{0,8}?(高|快|强|便宜|贵|低|慢|多|少|好|大|小|领先)"
)
# 事实来源信号 (0909 新闻线): 事件/声明/引语段直接构成证据论断 — 本人推文、
# 公开信、公告、辞职回应等, 无须数字+比较双条件 (旧规则按跑分文设计,
# "连辞两家"稿全篇 0 命中 → 证据图全降级)。
_NEWS_KW = re.compile(
    r"声明|宣布|辞职|离职|裸辞|公开信|推文|推特|发帖|发文|回应|证实|否认|"
    r"澄清|曝光|爆料|文件|邮件|公告|备忘录|名单|截图|采访|受访|喊话|"
    r"坦言|放话|说了一句|一句狠话|原话"
)
_CLAIM_NUM = re.compile(r"[\d零一二三四五六七八九十百千万亿两]")
# "一"字词素剥离: 一样/一下/一般…及功能计数词 (一个/一条/一点点/第一张牌)
# 的"一"不是数据数字, 不然中文几乎句句有"一" (真机 Gemini 稿 21/106 段漏判实证)
_NUM_MORPHEME = re.compile(
    r"一样|一下|一起|一般|一切|一直|统一|唯一|一边|一旦|一些|一丝|一味|一边倒|"
    r"一个|一条|一点点|一点|一手|一次|一代|一季度|凑一块|第[一二三]张牌"
)

# ── VLM 打标 ──
_VLM_SYSTEM = "你是短视频素材编辑，正在验收从新闻页面抓来的截图，判断它能不能当'证据图'放进成片（给口播里的测试成绩/对比数据/价格当画面证据）。只输出一行 JSON，不要其他文字。"
_VLM_USER = """看这张图，输出:
{"is_chart": <true|false>, "kind": "<benchmark|leaderboard|price|comparison|screenshot|photo|other>", "desc_zh": "<不超过40字的中文描述，说清图上是什么数据>", "numbers": [<图上能看到的数字，原样字符串，最多8个>], "quality": <1-10 画质分>, "watermark": "<none|corner|heavy>", "has_source": <true|false>}

判定:
- is_chart=true 仅限: 榜单/跑分/基准结果图、参数对比表、价格页截图、性能测试曲线、新闻数据图表
- is_chart=false: 广告banner、表情包、头像、产品渲染图(无数据)、纯风景/人物照片、页面导航截图
- watermark=heavy: 大面积水印/马赛克盖住数据 (corner=角落水印可接受)
- quality 按"截图数字清不清楚"打: 模糊到看不清数字 ≤3
- has_source=true 仅限: 图上能看到**发布平台的界面元素或机构标识** — 社交平台账号(头像/用户名/平台UI框架/点赞转发栏)、机构logo/信头、带URL的浏览器页面。⚠ 文档自身的标题页/署名/日期/人数**不算出处** (任何人都可排版伪造, 信服力等于自制卡片) — 裸封面页/裸标题图/纯排版文字一律 = false"""

_vlm_client: Any = None
_vlm_ready = False


def _get_vlm_client() -> Any | None:
    """懒加载 9B 视觉客户端 (llama-server 自拉起, 同 asset_quality._get_client).
    失败返回 None → fail-closed (图存 vlm:null, 不进池)."""
    global _vlm_client, _vlm_ready
    if _vlm_ready:
        return _vlm_client
    _vlm_ready = True
    try:
        from app.config import get_config
        from app.services.local_llm_client import LocalLLMClient
        from app.services.video_tagging_service.llama import _ensure_llama_server

        if not _ensure_llama_server():
            logger.warning("[evidence] llama-server 不可用, VLM 打标跳过 (fail-closed)")
            return None
        _vlm_client = LocalLLMClient(get_config().local_llm)
    except Exception as exc:
        logger.warning("[evidence] VLM 客户端初始化失败: %s", exc)
        return None
    return _vlm_client


def reset_vlm_client() -> None:
    """测试钩子: 清空模块级客户端缓存."""
    global _vlm_client, _vlm_ready
    _vlm_client = None
    _vlm_ready = False


def _parse_vlm_json(raw: str) -> dict[str, Any] | None:
    """VLM 原始输出 → 规整 dict; 解析失败返回 None."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    import json

    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "other")
    if kind not in {"benchmark", "leaderboard", "price", "comparison", "screenshot", "photo", "other"}:
        kind = "other"
    numbers = data.get("numbers")
    if not isinstance(numbers, list):
        numbers = []
    try:
        quality = int(data.get("quality") or 0)
    except (TypeError, ValueError):
        quality = 0
    watermark = str(data.get("watermark") or "none")
    if watermark not in {"none", "corner", "heavy"}:
        watermark = "corner"
    has_source = data.get("has_source")
    if not isinstance(has_source, bool):
        has_source = False
    return {
        "is_chart": bool(data.get("is_chart")),
        "kind": kind,
        "has_source": has_source,
        "desc_zh": str(data.get("desc_zh") or "")[:60],
        "numbers": [str(n).strip() for n in numbers if str(n).strip()][:8],
        "quality": max(0, min(10, quality)),
        "watermark": watermark,
    }


def _tag_image(client: Any, img_path: str) -> dict[str, Any] | None:
    """单图 VLM 打标; 任何失败返回 None (fail-closed)."""
    try:
        raw = client.chat_with_images([Path(img_path)], _VLM_SYSTEM, _VLM_USER)
        return _parse_vlm_json(raw)
    except Exception as exc:
        logger.warning("[evidence] VLM tag failed for %s: %s", img_path, exc)
        return None


# ── 下载 ──

_CT_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _download_image(url: str, cache_dir: Path) -> tuple[str, int, int] | None:
    """下载候选图到缓存 + Pillow 校验 (可解码且宽≥400).
    返回 (local_path, w, h); 失败返回 None. 命中缓存直接复用."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    try:
        from PIL import Image
    except ImportError:
        logger.warning("[evidence] Pillow 不可用, 无法校验图片")
        return None

    for cached in sorted(cache_dir.glob(f"{key}.*")):
        try:
            with Image.open(cached) as im:
                w, h = im.size
            if w >= _IMG_MIN_W:
                return str(cached.resolve()), w, h
            return None
        except Exception:
            cached.unlink(missing_ok=True)  # 损坏缓存重下

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "image/webp,image/png,image/jpeg,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }
    try:
        with requests.get(url, headers=headers, timeout=_DL_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ct and ct not in _CT_EXT:  # html 错误页/text 都挡掉
                return None
            ext = _CT_EXT.get(ct) or (Path(url.split("?")[0]).suffix.lower() if re.fullmatch(
                r"\.(jpe?g|png|webp)", Path(url.split("?")[0]).suffix.lower()) else ".jpg")
            size = int(resp.headers.get("Content-Length") or 0)
            if size and size > _DL_MAX_BYTES:
                return None
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=8192):
                buf.extend(chunk)
                if len(buf) > _DL_MAX_BYTES:
                    return None
    except requests.RequestException as exc:
        logger.debug("[evidence] download failed %s: %s", url, exc)
        return None

    tmp = cache_dir / f"{key}.tmp"
    dest = cache_dir / f"{key}{ext}"
    try:
        tmp.write_bytes(bytes(buf))
        with Image.open(tmp) as im:
            im.verify()
        with Image.open(tmp) as im:
            w, h = im.size
        if w < _IMG_MIN_W:
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(dest)
    except Exception as exc:
        logger.debug("[evidence] image verify failed %s: %s", url, exc)
        tmp.unlink(missing_ok=True)
        return None
    return str(dest.resolve()), w, h


def _has_scan_product(item: MaterialItem) -> bool:
    """条目是否已扫完 (可作为幂等跳过判据): 每条都带 local_path 且 VLM 打标非空.

    - add_item 抓取时存的 URL 级 images_json (只有 {url,alt,w,h}) → 未扫, 恰是待扫对象
    - 首扫遇 VLM 预算耗尽/服务挂 (vlm:null) → 未扫完, 手动补扫可重试这些图
    - 0910 出处闸: 旧标签缺 has_source 字段 → 视为未扫完, 补扫一次即迁移
    """
    entries = item.images_json or []
    return bool(entries) and all(
        isinstance(e, dict) and e.get("local_path")
        and isinstance(e.get("vlm"), dict) and "has_source" in e["vlm"]
        for e in entries
    )


# ── 扫图主入口 ──

def scan_package_images(db: Session, package: MaterialPackage,
                        progress_cb: _PROGRESS_CB = None) -> dict[str, int]:
    """素材包扫图: 条目页候选图 → 下载 → VLM 打标 → 写回 images_json.

    幂等: 已有 images_json 的条目跳过 (手动补扫只补新条目)。
    返回 {scanned_items, candidates, downloaded, charts}。
    """
    from ..config import get_config

    def _notify(stage: str, **payload: Any) -> None:
        if progress_cb:
            try:
                progress_cb(stage, payload)
            except Exception:
                pass

    cache_dir = Path(get_config().defaults.materials_dir) / "evidence" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. 收集候选: 原文条目 (article.images_json, 不重抓) 优先, 其余条目逐页提取
    article: Article | None = db.get(Article, package.article_id)
    article_imgs: list[dict[str, Any]] = [
        {k: e.get(k) for k in ("url", "alt", "w", "h")}
        for e in (article.images_json or []) if isinstance(e, dict) and e.get("url")
    ][:_SCAN_PER_ARTICLE]

    targets: list[tuple[MaterialItem, list[dict[str, Any]]]] = []
    handled_ids: set[str] = set()
    scanned_items = 0
    if article_imgs:
        item = _ensure_article_item(db, package, article)
        if item is not None and not _has_scan_product(item):
            targets.append((item, article_imgs))
            handled_ids.add(item.id)
            scanned_items += 1

    for item in package.items:
        if scanned_items >= _SCAN_ITEM_CAP:
            break
        if item.id in handled_ids or _has_scan_product(item) or not item.source_url:
            continue
        html = fetch_html(item.source_url, timeout=15)
        imgs = extract_images(html, item.source_url, cap=_SCAN_PER_ITEM) if html else []
        scanned_items += 1
        if imgs:
            targets.append((item, imgs))
        _notify("fetch", item=item.title or (item.source_url or "")[:40], found=len(imgs))

    # 2. 截总量 cap → 并行下载
    plan: list[tuple[MaterialItem, dict[str, Any]]] = [
        (item, img) for item, imgs in targets for img in imgs
    ][:_SCAN_TOTAL_CAP]
    candidates = len(plan)
    _notify("download_start", total=candidates)

    downloaded: dict[int, tuple[str, int, int]] = {}  # idx → (path, w, h)
    if plan:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="evidence") as pool:
            futures = {pool.submit(_download_image, img["url"], cache_dir): i
                       for i, (item, img) in enumerate(plan)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res:
                    downloaded[i] = res

    # 3. VLM 打标 (顺序 — llama-server 单槽) + 写回 (按 url 合并, 不与
    #    add_item 存的 URL 级条目重复堆叠)
    client = _get_vlm_client() if downloaded else None
    deadline = time.monotonic() + _VLM_BUDGET_SEC
    charts = 0
    done = 0
    for i, (item, img) in enumerate(plan):
        if i not in downloaded:
            continue
        local_path, w, h = downloaded[i]
        vlm = None
        if client is not None and time.monotonic() < deadline:
            vlm = _tag_image(client, local_path)
        done += 1
        _notify("tag", done=done, total=len(downloaded))
        entry = {
            "url": img["url"], "alt": (img.get("alt") or "")[:120],
            "w": w, "h": h, "local_path": local_path,
            "source_media": (item.media or item.title or "")[:64],
            "vlm": vlm,
        }
        existing = list(item.images_json or [])
        old = next((e for e in existing
                    if isinstance(e, dict) and e.get("url") == img["url"]), None)
        if vlm is None and old and isinstance(old.get("vlm"), dict):
            # 重扫不降级: 本轮 VLM 失败时保住旧标签 (真机二扫 5/8 null 冲掉好标签实证)
            entry["vlm"] = old["vlm"]
            vlm = old["vlm"]
        if old is not None:
            item.images_json = [
                entry if (isinstance(e, dict) and e.get("url") == img["url"]) else e
                for e in existing
            ]
        else:
            item.images_json = existing + [entry]
        if vlm and vlm.get("is_chart"):
            charts += 1

    db.commit()
    logger.info("[evidence] scan pkg=%s items=%d candidates=%d downloaded=%d charts=%d",
                package.id, scanned_items, candidates, len(downloaded), charts)
    return {"scanned_items": scanned_items, "candidates": candidates,
            "downloaded": len(downloaded), "charts": charts}


def _ensure_article_item(db: Session, package: MaterialPackage,
                         article: Article) -> MaterialItem | None:
    """原文条目复用: article.images_json 挂到 source_type='url' 条目 (不重抓原文页)."""
    if not article.source_url:
        return None
    for it in package.items:
        if it.source_type == "url" and it.source_url == article.source_url:
            return it
    item = MaterialItem(
        package_id=package.id,
        source_type="url",
        title=article.title or "原文",
        source_url=article.source_url,
        media="原文页面",
        raw_text=(article.raw_text or "")[:2000],
        fetch_ok=True,
        char_count=len(article.raw_text or ""),
    )
    db.add(item)
    db.flush()
    package.items.append(item)
    return item


# ── 池构建 + 段级匹配 ──

def collect_evidence_pool(db: Session, script: Any) -> list[dict[str, Any]]:
    """script → 合格证据图池 (vlm.is_chart 且 quality≥4 且非 heavy 水印).
    无素材包 / 池空返回 [] (调用方据此降级 broll_pexels)."""
    pkg_id = getattr(script, "material_package_id", None)
    if not pkg_id:
        return []
    package = db.get(MaterialPackage, pkg_id)
    if package is None:
        return []
    pool: list[dict[str, Any]] = []
    for item in package.items:
        for e in item.images_json or []:
            vlm = e.get("vlm") if isinstance(e, dict) else None
            if not isinstance(vlm, dict):
                continue
            # 入池 (2026-09-05 风格页定稿放宽; 0909 新闻线再放宽; 0910 出处闸):
            # 表格/榜单、实物图 或 事实截图 (推文/公开信/声明)。截图类必须
            # has_source (可指认出处: 账号/平台UI/机构抬头/URL) — 裸文字截图
            # 信服力等于自制 HF 卡, 放了白占画面 (0910 用户实锤: 员工声明
            # 封面页/文章头图两张)。杂图防线交给 quality≥4 + watermark 闸。
            if not vlm.get("is_chart") and vlm.get("kind") not in ("photo", "screenshot"):
                continue
            if vlm.get("kind") == "screenshot" and not vlm.get("has_source"):
                continue
            if not isinstance(vlm.get("quality"), (int, float)) or vlm["quality"] < _MIN_QUALITY:
                continue
            if vlm.get("watermark") == "heavy":
                continue
            if not e.get("local_path"):
                continue
            pool.append({
                "url": e.get("url") or "",
                "local_path": e["local_path"],
                "source_media": e.get("source_media") or "",
                "kind": vlm.get("kind") or "other",
                "desc_zh": vlm.get("desc_zh") or "",
                "numbers": [str(n) for n in (vlm.get("numbers") or [])],
                "quality": int(vlm["quality"]),
            })
    return pool


def is_evidence_claim(text: str) -> bool:
    """证据论断闸门: 事实来源段 (声明/推文/公开信…, _NEWS_KW) 直接过;
    其余走原双条件 — 含数字 且 命中测试/比较语义.
    "考了七十三点七" ✓ / "便宜到像白送" ✗ (无数字) / "第六周" ✗ (数字无比较) /
    "他发推宣布离职" ✓ ."""
    if not text:
        return False
    if _NEWS_KW.search(text):
        return True
    if not _CLAIM_NUM.search(_NUM_MORPHEME.sub("", text)):
        return False
    return bool(_CLAIM_KW.search(text) or _CLAIM_CMP.search(text))


def _num_variants(num_str: str) -> list[str]:
    """图上数字 → 文本可匹配形式集: 原样 + 中文读法 ('73.7'→'七十三点七')."""
    num = re.sub(r"[^\d.\-]", "", str(num_str))
    if not num or num in {"-", "."}:
        return []
    out = [num]
    try:
        tok = str(int(float(num))) if "." not in num else num.rstrip("0").rstrip(".") or "0"
        reading = _num_to_reading(tok)
        if reading and reading != num:
            out.append(reading)
    except (ValueError, ZeroDivisionError):
        pass
    return out


# 中文数字字符集 (读法匹配的边界判定, 防部分撞车: 图'60'读'六十' 撞 文'六十二')
_CN_NUM_BOUND = "零一二三四五六七八九十百千万亿两点"


def _number_in_text(num: str, variants: list[str], text: str) -> bool:
    """数字与段落文本匹配: 中文读法带边界的子串, 或阿拉伯数字独立 token (前后非数字)."""
    small = bool(re.fullmatch(r"\d", num))  # 单个数字 ("5") 的中文读法太泛 (五), 只认阿拉伯
    for v in variants:
        if len(v) > 1 and not v.isdigit():
            # 边界: 匹配段前后不能再贴中文数字字符 ('六十'不得命中'六十二')
            if re.search(rf"(?<![{_CN_NUM_BOUND}]){re.escape(v)}(?![{_CN_NUM_BOUND}])", text):
                return True
        elif v.isdigit():
            if not small and v in text:
                return True  # 多位阿拉伯串直接子串 (撞车概率低)
            if small and re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", text):
                return True
    return False


_KIND_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    ("price", re.compile(r"价格|定价|单价|美元|欧元|英镑|块钱|便宜|贵")),
    ("leaderboard", re.compile(r"榜单|排名|排第|榜首|第一|垫底")),
    ("benchmark", re.compile(r"基准|跑分|测试|评测|评分|成绩|满分")),
    ("comparison", re.compile(r"对比|比较|相比|反超|领先|超过|碾压")),
    # 0909 新闻线: 事件/声明/引语文本 ↔ screenshot 图 (VLM kind 词表对齐,
    # 否则 kind_bonus 永不触发 — VLM 只产 photo/screenshot/chart/other)
    ("screenshot", re.compile(r"推文|推特|声明|公开信|公告|辞职|离职|发帖|发文|回应|截图|采访|受访|邮件|文件")),
]


def pick_image_for_slot(text: str, keywords: list[str] | None,
                        pool: list[dict[str, Any]],
                        used_urls: set[str] | None = None) -> dict[str, Any] | None:
    """段级选图: 数字重合最强信号 ×10 + 关键词重合 ×2 + kind 性质匹配 +2.

    门槛: ≥1 个数字重合, 或 ≥2 关键词命中, 或 (关键词×kind 双命中)。
    排除 used_urls; 同分取 quality 高者。无合格图返回 None。
    """
    used = used_urls or set()
    best: dict[str, Any] | None = None
    best_score = 0
    text_l = text or ""
    text_kinds = {kind for kind, pat in _KIND_SIGNALS if pat.search(text_l)}
    kws = [k for k in (keywords or []) if k]

    for cand in pool:
        if cand.get("url") in used:
            continue
        num_hits = 0
        for n in cand.get("numbers") or []:
            variants = _num_variants(n)
            if variants and _number_in_text(n, variants, text_l):
                num_hits += 1
        desc = cand.get("desc_zh") or ""
        kw_hits = sum(1 for k in kws if k in desc)
        kind_bonus = 2 if cand.get("kind") in text_kinds else 0
        score = num_hits * 10 + min(kw_hits, 3) * 2 + (kind_bonus if num_hits or kw_hits else 0)
        ok = num_hits >= 1 or kw_hits >= 2 or (kw_hits >= 1 and cand.get("kind") in text_kinds)
        if ok and score > best_score:
            best_score = score
            best = cand
    if best is not None and best_score >= 4:
        return best
    return None
