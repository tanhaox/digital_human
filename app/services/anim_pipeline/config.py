# -*- coding: utf-8 -*-
"""独立配置 — 默认值内置, app/services/anim_pipeline/config.json 可覆盖, key 走 .env/环境变量.

不 import app.config (保持独立); .env 从 digital_human 根读取 (KIMI_API_KEY/DEEPSEEK_API_KEY),
与系统共用同一份密钥不复制. 0914 系统化整体平移: sandbox/anim_pipeline → app/services/anim_pipeline.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[2]  # digital_human/


def _load_dotenv() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class LLMConfig:
    base_url: str = "https://api.kimi.com/coding/v1"  # kimi 硬约束: 必须带 /v1
    api_key_env: str = "KIMI_API_KEY"
    model: str = "kimi-for-coding-highspeed"  # flash 档
    max_tokens: int = 32000  # 16000 仍截断(40段导演JSON≈24k字符), 0911 实测抬到 32000
    # 兜底 (kimi 连接失败/4xx 限流时切)
    fallback_base_url: str = "https://api.deepseek.com"
    fallback_api_key_env: str = "DEEPSEEK_API_KEY"
    # 1017 修: 模型名读 app.yaml deepseek 单一源 (0912 用户令统一 flash 档;
    # 旧硬编码 deepseek-chat 与平台侧脱节 → 400 实锤), 读不到退 deepseek-flash
    fallback_model: str = ""
    fallback_max_tokens: int = 8000


@dataclass
class ComfyConfig:
    base_url: str = "http://127.0.0.1:8188"
    output_dir: str = r"E:/AI/ComfyUI_windows_portable/ComfyUI/output"
    input_dir: str = r"E:/AI/ComfyUI_windows_portable/ComfyUI/input"
    timeout_sec: int = 900
    # 0921 软超时判卡 (用户令: 3min 没交货=卡住): 到点 /interrupt 步间生效,
    # 该镜 anim_fail 降级、批队列继续 (⚡连带重试败镜可救) — 不再陪爬行镜干等。
    soft_timeout_sec: int = 180
    interrupt_grace_sec: int = 180   # /interrupt 后再等的宽限 (步间生效, 爬行步可达数分钟)


@dataclass
class TTSConfig:
    """IndexTTS2.5 老谭读书专用通道 (0912 接入; 配方与产线 indextts25_tts 同源).

    音色克隆 timbre_wav + 韵律克隆 emo_wav (樊登说书感, 不传情绪向量);
    do_sample 必须开 (关=念经); duration_factor 1.16 为情感接通后校准值。
    """
    base_url: str = "http://127.0.0.1:7866"
    timbre_wav: str = "G:/音乐/untitled #8.wav"     # ≡ laotan25_timbre.wav (md5 同; RF64 头, wave 模块读不了但服务端可)
    emo_wav: str = "G:/音乐/untitled #4.wav"        # 0912 用户令终版情绪参考 (替换樊登切片)
    master_text: str = ""            # 老谭·2.5说书 voice 行 master_text 为空 (零样本克隆)
    duration_factor: float = 1.16
    emo_alpha: float = 0.8           # voice config_json 显式值 (引擎默认 0.6, DB 覆盖)
    temperature: float = 0.8
    top_p: float = 0.8
    top_k: int = 30
    do_sample: bool = True
    gap_sec: float = 0.2             # 镜间呼吸
    title_card_sec: float = 2.0      # 头部标题卡预留
    pinyin_map: str = str(PROJECT_ROOT / "config" / "tts_pinyin_map.json")
    lufs: float = -16.0              # loudnorm 口播标准 (与产线 normalize_audio 同参)


@dataclass
class BrandCardConfig:
    """品牌卡零 GPU 直通 (0914 系统化; 配方见 outputs/动画/_资产/品牌卡_配方.txt).

    每集身份句镜 (落款仪式句/收官品牌句, laotan-book.txt 冻结文案) 直接插定稿卡资产,
    不重新生成. match_rules: 规则列表, 每条 = 规范化后 narration 必须全含的子串组.
    """
    video: str = str(PROJECT_ROOT / "outputs" / "动画" / "_资产" / "品牌卡_登山客_赛璐璐_v2.mp4")
    keyframe: str = str(PROJECT_ROOT / "outputs" / "动画" / "_资产" / "品牌卡_登山客_赛璐璐_v2.png")
    title_bg: str = str(PROJECT_ROOT / "outputs" / "动画" / "_资产" / "标题底图_v1.png")
    # 开场预留 (剪辑层, 素材拼贴非生成, 与生成动画不抢时间):
    # 0-2s 黑底花底打字卡 + 2-3s 定格 + 3-4.2s 书封墙飞入 + 4.2-5.5s 主书封定格 (storyboard 线同源)
    opening_sec: float = 5.5
    match_rules: list[list[str]] = field(default_factory=lambda: [
        # "一个硬本事" 不钉 多/涨: 导演规划 LLM 转写仪式句会漂字 (ep6 s56
        # "涨一个硬本事" 漏标实锤 — 剧本冻结句是"多", 分镜转写变"涨"),
        # 容错只松动词; 身份词 "我是老谭" 系统注入专属 (稿内禁写), 齐含即品牌镜
        ["我是老谭", "读一本书", "一个硬本事"],   # 落款仪式句 (每集)
        ["我是老谭", "拆一本书", "翻一座山"],     # 收官品牌句 (末集 CTA 冻结形)
        ["我是老谭", "咱们下一集"],               # 收尾 CTA 兜底 (转写漂变族:
        #   ep2 s33 "读一本，翻一座山咱们下一集" / ep4 s89 "点点关注咱们下一集")
    ])


@dataclass
class AnimConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    comfy: ComfyConfig = field(default_factory=ComfyConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    brand_card: BrandCardConfig = field(default_factory=BrandCardConfig)
    db_path: str = str(PROJECT_ROOT / "data" / "pipeline.db")
    output_root: str = str(PROJECT_ROOT / "outputs" / "动画")
    director_prompt_file: str = str(MODULE_DIR / "director_prompt.txt")
    translator_prompt_file: str = str(MODULE_DIR / "translator_prompt.txt")


def load() -> AnimConfig:
    _load_dotenv()
    cfg = AnimConfig()
    override = MODULE_DIR / "config.json"
    if override.exists():
        raw = json.loads(override.read_text(encoding="utf-8"))
        for k, v in (raw.get("llm") or {}).items():
            setattr(cfg.llm, k, v)
        for k, v in (raw.get("comfy") or {}).items():
            setattr(cfg.comfy, k, v)
        for k, v in (raw.get("tts") or {}).items():
            setattr(cfg.tts, k, v)
        for k, v in (raw.get("brand_card") or {}).items():
            if k != "match_rules" or isinstance(v, list):
                setattr(cfg.brand_card, k, v)
        for k in ("db_path", "output_root", "director_prompt_file", "translator_prompt_file"):
            if raw.get(k):
                setattr(cfg, k, raw[k])
    # 1017 修: deepseek 兜底模型名注水 — 读 app.yaml deepseek 段单一源
    # (0912 用户令统一 flash 档); config.json 显式覆盖优先
    if not cfg.llm.fallback_model:
        try:
            from app.config import get_config as _gc
            _ds = getattr(_gc(), "deepseek", None)
            cfg.llm.fallback_model = str(
                getattr(_ds, "model_flash", "") or "deepseek-flash")
        except Exception:  # noqa: BLE001 — app 配置不可达退 0912 令的 flash
            cfg.llm.fallback_model = "deepseek-flash"
    return cfg


# ── 风格皮肤: 引擎(workflow结构)冻结, 皮肤(LoRA/风格锚)随书绑定 ──
# 风格随书变 (0914 用户令) 的生成层落地: 书级绑定文件 → 风格库成员;
# 新书换风格 = 换一个绑定 JSON, 零代码改动; 库可扩展, 将来可让圣经 LLM 在库内推荐.

# 新书默认皮肤 (0919 用户令: 下一本书起用国风chibi_3D; 存量书靠 风格绑定_*.json 锁定,
# 兜底配方仍是下方 DEFAULT_STYLE 赛璐璐 — 库 json 缺失/损坏时名实相符地回退).
DEFAULT_STYLE_NAME = "国风chibi_3D"

DEFAULT_STYLE = {
    "name": "赛璐璐_1988",
    "desc": "",
    "style_prompt_head": "传统赛璐璐手绘动画风格，线条干净色彩鲜艳明暗分明，12帧手绘呼吸感，1988年代赛璐珞胶片质感。",
    "style_prompt_tail": "传统赛璐璐手绘动画风格，线条干净色彩鲜艳明暗分明，12帧手绘呼吸感，1988年代赛璐珞胶片质感，16:9。",
    "h3_style": "1988 hand-drawn cel animation style, clean line art, flat vivid colors, sharp cel-shading",
    "lora": "Krea2-风格\\Krea2-漫画白鲸记风格_NSW.safetensors",
    "lora_strength": 0.8,
}


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "unnamed"


def style_binding_missing(book_title: str) -> bool:
    """0922 风格硬挡板: 书级绑定不存在 → 动画开工拒跑 (治默认皮肤悄悄生效)."""
    root = Path(load().output_root)
    return not (root / "_资产" / f"风格绑定_{_safe_name(book_title)}.json").exists()


def resolve_style(book_title: str) -> dict:
    """书级风格绑定 → 风格库成员 (缺失全走默认赛璐璐; 库成员字段缺省回退默认).

    1009 大坑修 (backlog ID-080): 绑定内嵌 recipe 快照优先 (select 起落全配方) —
    书级冻结, 库卡跨书共用被别书 retune 改动不再连带漂移; 旧绑定 (只有名字) 走库。
    """
    import copy
    root = Path(load().output_root)
    bind = root / "_资产" / f"风格绑定_{_safe_name(book_title)}.json"
    style_name = DEFAULT_STYLE_NAME
    snap: dict | None = None
    if bind.exists():
        try:
            _b = json.loads(bind.read_text(encoding="utf-8"))
            style_name = _b.get("style") or style_name
            if isinstance(_b.get("recipe"), dict) and _b["recipe"].get("style_prompt_head"):
                snap = _b["recipe"]
        except Exception:  # noqa: BLE001 — 绑定坏走默认
            pass
    recipe = copy.deepcopy(DEFAULT_STYLE)
    lib = root / "_资产" / "风格库" / f"{style_name}.json"
    if lib.exists():
        try:
            data = json.loads(lib.read_text(encoding="utf-8"))
            recipe.update({k: v for k, v in data.items() if v})
            # 显式无 LoRA 风格 (lora: null): 置空不回退默认 NSW (0922 心智地图_商务插画)
            if "lora" in data and not data["lora"]:
                recipe["lora"] = ""
        except Exception:  # noqa: BLE001 — 库成员坏走默认
            pass
    if snap:
        # 快照覆盖库字段 (书级冻结); 库先兜底补快照可能缺的 page_overrides 等
        recipe.update({k: v for k, v in snap.items() if v})
    recipe["name"] = style_name
    return recipe


def pick_main_hue(color_palette: str) -> str:
    """圣经主色提取 (1026 收口单源, k2._bible_hue 与试渲调味共用 — 两处正则已漂移一次):

    主色字段顿号列表取第一**彩色**项; 中性项 (含 白/灰/黑) 跳过 — 主色首项常是
    底色级白系 (暖羊毛白实锤: '以暖羊毛白为主色调点缀'=全图洗白, GLM 眼验实锤),
    调味尾要的是可点缀的彩色。全中性/无主色字段 → 回退第一项/空串。"""
    import re as _re
    # 顿号放行进捕获 (主色常为顿号列表), 到 ;；,，。(（ 截断; 捕获后分项过滤
    m = _re.search(r"主色[:：]?\s*([^;；,，。(（]{2,40})", str(color_palette or ""))
    if not m:
        return ""
    terms = [t.strip() for t in _re.split(r"[、,，]", m.group(1)) if t.strip()]
    chroma = [t for t in terms if not any(k in t for k in ("白", "灰", "黑"))]
    return (chroma or terms or [""])[0][:12]


def main_color_tint(hue: str, lang: str = "zh") -> str:
    """圣经主色调味尾 (1016 W1 单源化): 旧版三处拷贝 (k2 中文/英文 + style_board
    试渲), 漂移即 bug — 以 k2 主路径为 canonical 收口于此。

    域界随宪法 (1012 朱砂红烧衬衫实锤): 主色只管环境与道具, 服装颜色归画面句;
    哑光织物锚压塑料光面 (0925 实锤)。空 hue 返回空串 (调用方免 if)。"""
    if not hue:
        return ""
    if lang == "en":
        return (f", environment and props accented in {hue} tones, "
                "clothing colors strictly as already written in this description "
                "(no recolor), fabric is matte textile (real folds, no plastic sheen)")
    return (f"，环境与核心道具以{hue}为主色调点缀，"
            "人物服装颜色严格按本画面句已写的执行(禁改色禁换色)，"
            "服装为哑光织物质感(布料褶皱真实, 禁塑料光面)")
