"""Application configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.yaml"


@dataclass(frozen=True)
class AppConfig:
    name: str
    port: int
    host: str
    database_url: str
    data_dir: Path


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str
    model_flash: str
    model_pro: str
    default_model: str


@dataclass(frozen=True)
class QwenConfig:
    api_key: str
    base_url: str
    model_flash: str
    model_pro: str
    default_model: str = "flash"


@dataclass(frozen=True)
class ZhipuConfig:
    """智谱联网搜索 (2026-08-15): 素材聚合补搜专用, 独立 /web_search 端点."""

    api_key: str
    base_url: str  # https://open.bigmodel.cn/api/paas/v4
    search_engines: tuple[str, ...]  # 轮流降级阶梯 (一个用完自动切下一个); 单引擎场景填一项即可
    content_size: str  # medium(摘要) / high(详细)
    count: int
    recency: str  # oneDay/oneWeek/oneMonth/oneYear/noLimit
    timeout_sec: int
    free_quota_expires: str  # 额度到期日 YYYY-MM-DD; 留空=关闭本地拦截(付费资源包, 服务端额度报错兜底)


@dataclass(frozen=True)
class LocalLLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout_sec: int
    max_retries: int
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class DefaultsConfig:
    host_id: str
    voice_id: str
    backend: str
    base_url_fish: str
    base_url_f5: str
    base_url_indextts: str
    # IndexTTS2.5 语速 (2026-08-25): 2.5 基线比 2 慢 ~26% (实测中位 4.8 vs 6.5 字/s),
    # 0.75 ≈ 拉回旧版听感; 音色 config_json.params.duration_factor 显式值优先于此默认。
    indextts_duration_factor: float
    # 2.5 老谭读书通道 (2026-09-10): 情感参考音频 + 实测配方
    indextts25_emo_ref: str
    # bs1 页间大气口 (0910 用户令: 段落级呼吸, 拼装层静音+画面定格)
    bs1_page_gap_sec: float
    base_url_comfyui: str
    comfyui_output_dir: str
    comfyui_input_dir: str
    comfyui_timeout_sec: int
    roles_output_root: str
    # HF (HyperFrames) 视觉渲染模块 — 独立管线,不接 ComfyUI
    hyperframes_bin: str
    hf_render_timeout_sec: int
    hf_visual_root: str
    hf_template_root: str
    # L0 蒸馏产物根目录 (2026-08-22): 项目内 data/l0, 随项目迁移可盘查
    l0_output_root: str
    # IndexTTS2 / 对齐 / 导演 2.0
    indextts_timeout_sec: int
    whisper_model_size: str
    whisper_device: str
    director_output_root: str
    composition_output_root: str
    # PPT 出片产线 (2026-08-20): 工作根目录 (上传/解析/渲染中间产物)
    ppt_work_root: str
    # Pexels 素材 resolve 工具 (ID-003)
    pexels_api_key_env: str
    materials_dir: str
    pexels_daily_download_quota: int
    pexels_default_max_results: int
    pexels_min_duration_sec: int
    pexels_preferred_resolution: str
    # 单条 Pexels 下载总时长上限 (秒): requests (10,300) 只卡字节间隔, 慢滴流
    # 可拖数小时 (2026-09-04 slot 02 挂死实证); 超限抛错走 fallback 链。
    pexels_download_total_timeout_sec: int
    max_host_slots: int
    job_auto_cleanup_days: int
    # 素材保留策略 (2026-08-07): completed job 的 slot 素材保留天数。
    # 0 = 关闭保留 (回退到旧行为, 出片即删 slots/)。
    slot_retention_days: int
    # 导演素材输入模式 (ID-034): "vocabulary" = 注入关键词词表包 (默认, ~3KB,
    # 解决全量 catalog ~26KB 拖慢 DeepSeek); "full" = 注入全量素材库清单 (旧行为)。
    director_catalog_mode: str
    # 本地素材成片复用上限 (2026-08-15): 同一素材最多进入 N 个成片 (used_count
    # 硬过滤, 防一批"万能素材"每个视频都被选中)。0 = 关闭限制。
    local_asset_max_uses: int
    material_reuse_cooldown: int  # 每N次选用才能复用一次 (2026-08-28 用户令)
    # J 线剪映草稿目录 (2026-08-15): 导出的草稿直接落剪映草稿文件夹,
    # 打开剪映即可在列表顶部看到 (注册+时间戳由剪映自身扫描完成)。
    jianying_drafts_dir: str
    # P 线本地碰撞策略 (2026-08-16 用户反馈烂素材反复用): normal=原75%门槛 /
    # strict=仅强命中(85%)才用本地(默认) / off=全走 Pexels 新下载
    p_line_local_collision: str
    # 本地素材内容质量硬底线 (2026-08-16 治理③): VLM 质量分低于此值出局;
    # 0=关闭。未打分素材暂放行 (asset_quality_scan 渐进收紧)
    local_asset_min_quality: int
    # 拆书项目 (2026-08-19): 书库目录扫描 (精华/原书, 文件名=书名, txt+epub 自动识别)
    book_source_dir: str
    # Pexels 下载即质检 (2026-08-16 治理③闭环): 下载后 VLM 打分;
    # 视觉模型不可用时 fail-open 放行
    pexels_download_quality_gate: bool
    # 质检模式 (2026-08-16 生产考量): sync=同步打分不合格换候选(最严, 每片+4~6分钟) /
    # async=默认, 下载即返回后台补打分(当前slot可能带病, 后续与未来全片受保护)
    pexels_quality_gate_mode: str
    # TTS ASR 回听校验 (2026-09-03): 合成后每行过 whisper 拼音对比, 错音自动
    # <字|PINYIN> 重合成修复, 修不了标红人工听。False=关闭 (旧行为)。
    tts_verify_asr: bool


@dataclass(frozen=True)
class Config:
    app: AppConfig
    deepseek: DeepSeekConfig
    kimi: DeepSeekConfig
    qwen: QwenConfig
    local_llm: LocalLLMConfig
    zhipu: ZhipuConfig
    defaults: DefaultsConfig
    raw: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_env(value: Any) -> Any:
    """Replace strings like ${VAR} or ${VAR:default} with environment values."""
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        inner = value[2:-1]
        if ":" in inner:
            key, default = inner.split(":", 1)
            return os.environ.get(key, default)
        return os.environ.get(inner, "")
    return value


def _load_dotenv(path: Path | None = None) -> None:
    """轻量 .env 加载器 (无第三方依赖).

    仅 setdefault, 不覆盖进程已有环境变量 — 启动脚本显式 set 的值优先.
    pexels_service 等直接读 os.environ 的模块依赖此处注入.
    """
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def load_config(path: Path | str | None = None) -> Config:
    _load_dotenv()
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = _load_yaml(config_path)

    app_raw = raw.get("app", {})
    data_dir_raw = app_raw.get("data_dir", str(PROJECT_ROOT / "data"))
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    app = AppConfig(
        name=app_raw.get("name", "digital-human-pipeline"),
        port=int(app_raw.get("port", 54321)),
        host=app_raw.get("host", "127.0.0.1"),
        database_url=app_raw.get(
            "database_url",
            f"sqlite:///{data_dir / 'pipeline.db'}",
        ),
        data_dir=data_dir,
    )

    ds_raw = raw.get("deepseek", {})
    api_key = _resolve_env(ds_raw.get("api_key", ""))
    if not api_key:
        # Allow missing key in development, but LLMService will raise on actual use.
        api_key = ""
    deepseek = DeepSeekConfig(
        api_key=api_key,
        base_url=ds_raw.get("base_url", "https://api.deepseek.com"),
        model_flash=ds_raw.get("model_flash", "deepseek-v4-flash"),
        model_pro=ds_raw.get("model_pro", "deepseek-v4-pro"),
        default_model=ds_raw.get("default_model", "flash"),
    )

    # kimi (2026-09-09 用户令): 主力 LLM, deepseek 降级为系统兜底。
    # 复用 DeepSeekConfig 形状 (base_url/flash/pro/default_model 四元)。
    km_raw = raw.get("kimi", {})
    kimi = DeepSeekConfig(
        api_key=_resolve_env(km_raw.get("api_key", "")),
        base_url=km_raw.get("base_url", "https://api.kimi.com/coding/v1"),
        model_flash=km_raw.get("model_flash", "kimi-for-coding-highspeed"),
        model_pro=km_raw.get("model_pro", "k3"),
        default_model=km_raw.get("default_model", "flash"),
    )

    qw_raw = raw.get("qwen", {})
    qwen = QwenConfig(
        api_key=_resolve_env(qw_raw.get("api_key", "")),
        base_url=qw_raw.get("base_url", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        model_flash=qw_raw.get("model_flash", "qwen3.6-flash"),
        model_pro=qw_raw.get("model_pro", "qwen3.8-max"),
        default_model=qw_raw.get("default_model", "flash"),
    )

    llm_raw = raw.get("local_llm", {})
    local_llm = LocalLLMConfig(
        base_url=llm_raw.get("base_url", "http://127.0.0.1:8080"),
        model=llm_raw.get("model", "qwythos-9b"),
        api_key=_resolve_env(llm_raw.get("api_key", "")),
        timeout_sec=int(llm_raw.get("timeout_sec", 120)),
        max_retries=int(llm_raw.get("max_retries", 2)),
        temperature=float(llm_raw.get("temperature", 0.3)),
        max_tokens=int(llm_raw.get("max_tokens", 1024)),
    )

    defaults_raw = raw.get("defaults", {})
    defaults = DefaultsConfig(
        host_id=defaults_raw.get("host_id", "laochen"),
        voice_id=defaults_raw.get("voice_id", "laochen_default"),
        backend=defaults_raw.get("backend", "fish"),
        base_url_fish=defaults_raw.get("base_url_fish", "http://127.0.0.1:7860"),
        base_url_f5=defaults_raw.get("base_url_f5", "http://127.0.0.1:7861"),
        base_url_indextts=defaults_raw.get("base_url_indextts", "http://127.0.0.1:7862"),
        indextts_duration_factor=float(defaults_raw.get("indextts_duration_factor", 0.75)),
        indextts25_emo_ref=str(defaults_raw.get(
            "indextts25_emo_ref", "E:/数字人计划/voices/laotan25_emo_fandeng.mp3")),
        bs1_page_gap_sec=float(defaults_raw.get("bs1_page_gap_sec", 0.5)),
        base_url_comfyui=defaults_raw.get("base_url_comfyui", "http://127.0.0.1:8188"),
        comfyui_output_dir=defaults_raw.get(
            "comfyui_output_dir", "E:/AI/ComfyUI_windows_portable/ComfyUI/output"
        ),
        comfyui_input_dir=defaults_raw.get(
            "comfyui_input_dir", "E:/AI/ComfyUI_windows_portable/ComfyUI/input"
        ),
        comfyui_timeout_sec=int(defaults_raw.get("comfyui_timeout_sec", 600)),
        roles_output_root=defaults_raw.get(
            "roles_output_root", "E:/数字人计划/roles"
        ),
        # HF 视觉渲染模块
        hyperframes_bin=defaults_raw.get("hyperframes_bin", "npx"),
        hf_render_timeout_sec=int(defaults_raw.get("hf_render_timeout_sec", 300)),
        hf_visual_root=defaults_raw.get(
            "hf_visual_root", "E:/数字人计划/hf_visual"
        ),
        hf_template_root=defaults_raw.get(
            "hf_template_root", "E:/AI/digital_human/hf_prep"
        ),
        l0_output_root=defaults_raw.get("l0_output_root", "data/l0"),
        # Pexels 素材 resolve 工具 (ID-003)
        pexels_api_key_env=defaults_raw.get("pexels_api_key_env", "PEXELS_API_KEY"),
        materials_dir=defaults_raw.get("materials_dir", "E:/数字人计划/materials"),
        pexels_daily_download_quota=int(
            defaults_raw.get("pexels_daily_download_quota", 200)
        ),
        pexels_default_max_results=int(
            defaults_raw.get("pexels_default_max_results", 5)
        ),
        pexels_min_duration_sec=int(defaults_raw.get("pexels_min_duration_sec", 5)),
        pexels_preferred_resolution=defaults_raw.get(
            "pexels_preferred_resolution", "FHD"
        ),
        pexels_download_total_timeout_sec=int(
            defaults_raw.get("pexels_download_total_timeout_sec", 180)
        ),
        max_host_slots=int(defaults_raw.get("max_host_slots", 4)),
        job_auto_cleanup_days=int(defaults_raw.get("job_auto_cleanup_days", 7)),
        slot_retention_days=int(defaults_raw.get("slot_retention_days", 7)),
        director_catalog_mode=defaults_raw.get("director_catalog_mode", "vocabulary"),
        local_asset_max_uses=int(defaults_raw.get("local_asset_max_uses", 2)),
        material_reuse_cooldown=int(defaults_raw.get("material_reuse_cooldown", 10)),
        # J 线: 默认落在当前用户的新版剪映草稿目录
        jianying_drafts_dir=defaults_raw.get(
            "jianying_drafts_dir",
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "JianyingPro", "User Data", "Projects", "com.lveditor.draft",
            ),
        ),
        p_line_local_collision=defaults_raw.get("p_line_local_collision", "strict"),
        local_asset_min_quality=int(defaults_raw.get("local_asset_min_quality", 4)),
        book_source_dir=defaults_raw.get("book_source_dir", "G:/Desktop/畅销书"),
        pexels_download_quality_gate=bool(defaults_raw.get("pexels_download_quality_gate", True)),
        pexels_quality_gate_mode=defaults_raw.get("pexels_quality_gate_mode", "async"),
        tts_verify_asr=bool(defaults_raw.get("tts_verify_asr", True)),
        # IndexTTS2 / 对齐 / 导演 2.0
        indextts_timeout_sec=int(defaults_raw.get("indextts_timeout_sec", 300)),
        whisper_model_size=defaults_raw.get("whisper_model_size", "large-v3"),
        whisper_device=defaults_raw.get("whisper_device", "cuda"),
        director_output_root=defaults_raw.get(
            "director_output_root", "E:/数字人计划/director"
        ),
        composition_output_root=defaults_raw.get(
            "composition_output_root", "E:/数字人计划/composition"
        ),
        ppt_work_root=defaults_raw.get("ppt_work_root", "E:/数字人计划/ppt"),
    )

    # 智谱联网搜索 (2026-08-15): 素材聚合补搜, 独立 web_search 端点
    zp_raw = raw.get("zhipu", {})
    # 引擎轮流 (2026-09-20): 阶梯列表优先; 兼容旧单值键 search_engine
    zp_engines = zp_raw.get("search_engines") or [zp_raw.get("search_engine") or "search_pro"]
    zhipu = ZhipuConfig(
        api_key=_resolve_env(zp_raw.get("api_key", "")),
        base_url=zp_raw.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        search_engines=tuple(str(e) for e in zp_engines),
        content_size=zp_raw.get("content_size", "high"),
        count=int(zp_raw.get("count", 5)),
        recency=zp_raw.get("recency", "noLimit"),
        timeout_sec=int(zp_raw.get("timeout_sec", 30)),
        free_quota_expires=zp_raw.get("free_quota_expires", ""),
    )
    return Config(app=app, deepseek=deepseek, kimi=kimi, qwen=qwen, local_llm=local_llm, zhipu=zhipu, defaults=defaults, raw=raw)


# ── Lazy global accessor (loaded by app.main.lifespan) ──────────────
_config: Config | None = None


def set_config(cfg: Config) -> None:
    """Set the module-level config (called from app.main lifespan)."""
    global _config
    _config = cfg


def get_config() -> Config:
    """Return the loaded config; raises RuntimeError if lifespan didn't run."""
    if _config is None:
        raise RuntimeError("Config not loaded (lifespan did not run)")
    return _config
