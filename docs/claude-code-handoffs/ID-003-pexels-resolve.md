# Claude Code 交接包 — ID-003 Pexels 素材 resolve 服务模块

> **任务来源**：Hermes（2026-07-27 用户拍板）
> **执行人**：Claude Code
> **背景**：A 管线 B-Roll 素材源从"本地累积 mp4"改为 Pexels 工具。和 DeepSeek 一样范式——Pexels 自己有公开 API + key，**不**在本地 54321 再架一层 HTTP 中间层
> **核心语义**：本地有就复用，没有就下载，下载不到返元数据 + 标记 degraded

---

## 0. 上下文

- 项目根：`F:\AI-Agent-Local\digital_human\`
- 后端框架：FastAPI（**已存在**，但本任务**不**新增任何 router / 端点）
- 数据库：SQLite（`F:/AI-Agent-Local/digital_human/data/pipeline.db`）
- Web 端口：54321（**不**改 / **不**占用 / **不**做 materials 端点）
- 配置文件：`config/app.yaml`（**已配 5 个 Pexels key**，**不**再动）

**Pexels API 文档**（已实测验证）：https://www.pexels.com/api/documentation/
- 端点：`GET https://api.pexels.com/v1/videos/search?query=...&per_page=...&page=...`
- 鉴权：Header `Authorization: <PEXELS_API_KEY>`
- 限流：200 req/hour + 20000 req/month（free tier）
- 视频规格：每个 `videos[].video_files[]` 有多档（UHD 3840x2160 / QHD 2560x1440 / FHD 1920x1080 / HD 1280x720 / SD 640x360），按 `file_type=video/mp4` + `width` 过滤
- 强制合规：使用素材时必须展示 "Photos/Videos provided by Pexels" + 摄影师署名

**实测数据**（2026-07-27 已跑通）：
- 搜 `port` → 返回 8000+ 结果，每个视频有 4-5 个 mp4 档位
- FHD 1920x1080 25fps mp4 平均 **57.66 MB / 55s**
- 下载速度 4.8 MB/s
- ffprobe 校验 h264/1920x1080 通过
- 18 个候选关键词全部 ≥4500 结果
- 竖版（h>w）默认 13-16%，**orientation 过滤交给调用方**（API 层不强制）

---

## 1. 业务目标

给视觉导演（ID-002）一个 `pexels_service.resolve(query)` 服务，**Python import 调用**，**不**做 HTTP 端点：

```python
from app.services.pexels_service import pexels_service

materials = pexels_service.resolve(query="port cranes", max_results=3)
# 返回 list[MaterialAsset]
# 行为:
#   - 本地有 → 直接返回本地路径（零网络）
#   - 本地没 → 调 Pexels API 下载 FHD mp4 到本地，落 DB，返回
#   - 下载失败（quota 用完 / 网络错 / 找不到） → 返回元数据 + degraded=True
#   - 全部失败 → 返空 + degraded=True
```

**外部系统**（如未来爬虫 / 独立视频合成器）**直接调 Pexels 官方 API**（不经过本项目）：
```bash
curl -H "Authorization: $PEXELS_API_KEY" "https://api.pexels.com/v1/videos/search?query=port"
```

---

## 2. 关键架构决策

**为什么不做 HTTP 中间层**（2026-07-27 用户拍板）：
- Pexels 自己有 API + key，**不**需要本地再架端口
- DeepSeek / OpenAI / ElevenLabs 都是直接调官方 API，**没有**中间层
- 中间层反而带来：端口占用、鉴权复杂度、跨进程调用、多 worker quota 共享
- 视觉导演（ID-002）跟 pexels_service 在同一进程，**直接 import 最快**

---

## 3. 按文件列改动范围

### 3.1 新增文件

| 路径 | 用途 |
|------|------|
| `app/services/pexels_service.py` | **核心**：Pexels API 客户端 + resolve 编排 + quota 管理 + 本地缓存 |
| `sop/Pexels素材合规.md` | Pexels 强制合规 SOP（摄影师署名 + 链接展示） |
| `tests/test_pexels_service.py` | 单元测试（**不**走 mock，必须真实调 Pexels API） |
| `docs/claude-code-handoffs/ID-003-pexels-resolve.md` | 本交接包（已存在，**不**再改） |

### 3.2 修改文件

| 路径 | 改动 | 风险 |
|------|------|------|
| `app/models.py` | 追加 `MaterialAsset` 和 `DownloadLog` 两张 SQLAlchemy 表 | 低（追加） |
| `app/schemas.py` | 追加 `MaterialAssetOut` / `ResolveRequest` / `ResolveResponse` / `ResolveItem` Pydantic（**不**做 router，只是数据类） | 低（追加） |
| `app/database.py` | 检查 `Base.metadata.create_all()` 会扫到新表（可能需要 `init_db` 多加一行） | 低 |
| `config/app.yaml` | **不**再改（已配 5 个 key） | — |
| `docs/backlog.md` | **不**再改（ID-003 已写） | — |

### 3.3 不要碰的文件 / 不要做的事

- ❌ **不**新增 `app/routers/materials.py`（**不**做 HTTP 端点）
- ❌ **不**新增 `web/materials.html`（**不**做 web UI）
- ❌ **不**改 `app/main.py`（无 router 要注册）
- ❌ **不**改 `web/index.html`（无导航要加）
- ❌ **不**碰其他 router (`visual_render.py` / `digital_human_video.py` / `roles.py` 等)
- ❌ **不**改 `config/app.yaml` 现有 19 个 key
- ❌ **不**改 `docs/backlog.md` 其他 ID 条目
- ❌ **不**改视觉导演提示词 `config/visual_director_v2.txt`
- ❌ **不**改 A 管线音色锁定（Fish Speech s2-pro 4B，端口 7860）

---

## 4. 接口契约（pexels_service.py）

### 4.1 `PexelsService` 单例

```python
# app/services/pexels_service.py

pexels_service = PexelsService()  # 模块级单例，从 .env 读 key，从 config 读参数
```

### 4.2 `resolve(query, max_results=None, min_duration_sec=None, prefer_resolution=None, orientation=None) -> list[ResolveItem]`

| 参数 | 类型 | 默认 | 必填 | 含义 |
|------|------|------|------|------|
| `query` | str | — | ✅ | 搜索词（任意字符串） |
| `max_results` | int | `pexels_default_max_results` (5) | ❌ | 最多返回几条 |
| `min_duration_sec` | int | `pexels_min_duration_sec` (5) | ❌ | 过滤太短的视频 |
| `prefer_resolution` | str | `pexels_preferred_resolution` ("FHD") | ❌ | `FHD` / `HD` / `UHD`（失败自动 fallback） |
| `orientation` | str | "any" | ❌ | `landscape` / `portrait` / `any`（**any** = 不在 API 层强制，由 Pexels 自然返回） |

**返回**：`list[ResolveItem]`

**行为流程**：
1. 查本地 DB：`SELECT * FROM material_assets WHERE tags LIKE '%query%' AND duration_sec >= min_duration_sec`
2. 本地够 `max_results` 条 → 直接返回本地路径
3. 本地不够 → 调 Pexels search，按 `(prefer_resolution → 下一档)` 顺序下载缺失条数
4. 当天下载已达 `pexels_daily_download_quota` → 仅返回元数据，不下本地，标记 `degraded=True`
5. 全部失败 → 返空 + `degraded=True`

### 4.3 `ResolveItem` 数据类（dataclass 或 Pydantic）

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | int \| None | 本地 DB id（无本地时 None） |
| `local_path` | str \| None | 本地路径（如有） |
| `source_url` | str \| None | Pexels 原始 URL（无本地时） |
| `degraded` | bool | true = 仅元数据，**没**下到本地 |
| `duration_sec` | int | 时长 |
| `width`, `height` | int | 分辨率 |
| `fps` | int \| None | 帧率 |
| `photographer` | str | 必填（合规用） |
| `photographer_url` | str | 必填（合规用） |
| `pexels_url` | str | 原片链接（合规用） |
| `tags` | list[str] | 关键词（来自 query 拆分） |

### 4.4 辅助方法（内部用 / 不导出）

- `_search_pexels(query, per_page, page=1) -> list[Video]` — 调 Pexels 官方 API
- `_download(url, dest_path) -> bool` — 下载 + ffprobe 校验
- `_get_quota_used_today() -> int` — 查 `DownloadLog` 当日条数
- `_increment_quota(pexels_id, bytes)` — 写 `DownloadLog`

---

## 5. ORM 模型（app/models.py 追加）

### 5.1 `MaterialAsset`

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | int | PK auto | 本地 id |
| `pexels_id` | int | UNIQUE NOT NULL | Pexels 视频 id |
| `source_url` | str | NOT NULL | Pexels 视频原片 URL |
| `pexels_url` | str | NOT NULL | Pexels 视频页 URL（合规用） |
| `photographer` | str | NOT NULL | 摄影师名（合规用） |
| `photographer_url` | str | NOT NULL | 摄影师主页 URL（合规用） |
| `local_path` | str \| None | NULL OK | 本地路径（degraded 时 NULL） |
| `duration_sec` | int | NOT NULL | 时长 |
| `width` | int | NOT NULL | 宽 |
| `height` | int | NOT NULL | 高 |
| `fps` | int \| None | NULL OK | 帧率 |
| `resolution` | str | NOT NULL | "FHD" / "HD" / "UHD" / "SD" |
| `tags` | str | NOT NULL DEFAULT "" | 逗号分隔 tags（用于 LIKE 查询） |
| `created_at` | datetime | NOT NULL DEFAULT now() | 入库时间 |

### 5.2 `DownloadLog`

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| `id` | int | PK auto | |
| `date` | date | NOT NULL | 哪一天（按自然日重置） |
| `pexels_id` | int | NOT NULL | 哪个视频 |
| `bytes` | int | NOT NULL | 下载字节数 |
| `created_at` | datetime | NOT NULL DEFAULT now() | 下载时间 |

索引：`DownloadLog.date` 加速 quota 查询。

---

## 6. 验收清单（落地后**自己**跑过）

> 任何一项没勾 = 任务没完成。不要"理论上能跑"——必须**实际执行过命令**。

### 6.1 配置层

- [ ] 读 `config/app.yaml` 确认 5 个 Pexels key 都存在
- [ ] 读 `.env` 确认 `PEXELS_API_KEY=***（**不**用 echo 打印明文）
- [ ] `git check-ignore -v .env` 返回命中行
- [ ] `git ls-files .env` 为空
- [ ] `git grep "3oqILITC" .` 在已追踪文件里 0 命中

### 6.2 数据库层

- [ ] Python 启动时 `Base.metadata.create_all()` 创出 `material_assets` 和 `download_logs` 两表
- [ ] `sqlite3 data/pipeline.db ".schema material_assets"` 看到完整字段
- [ ] `sqlite3 data/pipeline.db ".schema download_logs"` 看到完整字段
- [ ] **不**报"表已存在"错误（重复 create_all 安全）

### 6.3 服务层（pexels_service.py）

- [ ] **真实 Pexels API 调用**（**不**用 mock）：
  ```python
  from app.services.pexels_service import pexels_service
  results = pexels_service.resolve(query="port", max_results=2)
  # 第一次跑 → 真实下载 2 条到本地 → 落 DB → 返 ResolveItem 列表
  ```
- [ ] **DB 命中复用**：
  ```python
  results2 = pexels_service.resolve(query="port", max_results=2)
  # 第二次跑 → 零网络下载，全部返 local_path
  ```
- [ ] **quota 超限降级**：临时把 `pexels_daily_download_quota` 改 0，重跑 → 返 degraded=True 元数据，**不**下载
- [ ] **min_duration_sec 过滤**：搜一个已知短时长关键词 → 返回结果 duration ≥ 5s
- [ ] **网络断开不崩溃**：拔网线（或临时改 PEXELS_API_KEY=invalid），resolve 不抛异常，返 degraded=True
- [ ] **ffprobe 校验**：下载的 mp4 全部通过 `ffprobe` 验证（h264/avc1/可识别分辨率）

### 6.4 合规层

- [ ] `sop/Pexels素材合规.md` 存在，章节齐全
- [ ] DB 里的 `MaterialAsset` 记录 100% 含 `photographer` + `photographer_url` + `pexels_url`（NOT NULL）
- [ ] ResolveItem 序列化时包含 `photographer` + `photographer_url` + `pexels_url`
- [ ] 合规 SOP 文档里**明确写** "Footage by {photographer} on Pexels" 展示要求

### 6.5 测试层

- [ ] `pytest tests/test_pexels_service.py -v` 全过
- [ ] 测试**真实**调 Pexels API（**不**走 mock），但用小 per_page=2 控制流量
- [ ] 测试结束后清理：DB 里的测试数据、下载的 mp4 文件

### 6.6 现有功能不破坏

- [ ] 完整 pytest 跑过（含 crawler）：`python -m pytest -q`，19 tests passed in <15s
- [ ] `from app.main import app` 仍能 import，routes 数量不变（**没**新增 router）

### 6.7 错误处理

- [ ] `pexels_service.resolve` 在 Pexels API 401 时抛 `PexelsAuthError` 异常（清晰信息，不静默）
- [ ] quota 超限返 `degraded=True` + `reason="quota_exceeded"`
- [ ] DB 写入失败（磁盘满） → 服务层抛异常，下载的临时文件回滚删除
- [ ] **不**引入 try/except 吞所有异常——只 catch 已知异常类型

---

## 7. 最后使用场景

### 7.1 视觉导演（ID-002）调用

```python
# 在 ID-002 视觉导演脚本里
from app.services.pexels_service import pexels_service

def get_broll_for_segment(segment_text: str, count: int = 2):
    """视觉导演生成 B-Roll 列表,本服务自动处理本地缓存/下载/quota"""
    materials = pexels_service.resolve(
        query=segment_text,
        max_results=count,
        min_duration_sec=5,
    )
    return [m.local_path or m.source_url for m in materials]
```

**调用方完全不需要知道**：
- 本地有没有
- Pexels API 长什么样
- quota 怎么算
- FHD 还是 HD

**这才是"工具"应该有的样子**。

### 7.2 外部系统接入（**不**经过本项目）

任何能发 HTTP 请求的程序，**直接调 Pexels 官方 API**：

```bash
# 拿 key
export PEXELS_API_KEY="..."

# 搜索
curl -H "Authorization: $PEXELS_API_KEY" \
     "https://api.pexels.com/v1/videos/search?query=port&per_page=5"

# 下载（拿到 mp4 link 后）
curl -o port.mp4 "https://videos.pexels.com/video-files/.../port.mp4"
```

**完全跳过 54321**。这跟 DeepSeek 调用方式一致：key 持有方直接调官方 API。

---

## 8. 边界禁止

**执行时**严格遵守：

- ❌ **不**写 Pexels API key 到任何 `.py` / `.yaml` / `.md` / commit message
- ❌ **不**修改 `config/app.yaml` 现有 19 个 key（**只**追加过 5 个 Pexels key，不要再动）
- ❌ **不**改 `docs/backlog.md` 其他 ID 条目
- ❌ **不**碰其他 router (`visual_render.py` / `digital_human_video.py` / `roles.py` 等)
- ❌ **不**自动 git commit（**永远** `git add` + `git status` 给你看，等拍板再 commit）
- ❌ **不**用 mock 替代真实下载验证（必须真下载 + ffprobe 校验）
- ❌ **不**引入关键词白名单（已删除，不要复活）
- ❌ **不**改视觉导演提示词 `config/visual_director_v2.txt`
- ❌ **不**改 A 管线音色锁定（Fish Speech s2-pro 4B，端口 7860）
- ❌ **不**新增 HTTP router / web 页面（**这是 Python 服务模块，不是 web API**）

---

## 9. 执行顺序（推荐）

1. **先**读 `app/models.py` 看清现有 ORM 风格（特别是 `VisualRenderJob` / `DigitalHumanVideo` 表的字段命名约定）
2. **再**读 `app/schemas.py` 看清 Pydantic 风格
3. **再**读 `app/database.py` 看 `Base.metadata.create_all()` 怎么调
4. **然后**按这个顺序写：
   1. `app/models.py` 加 `MaterialAsset` + `DownloadLog`（先 schema，类型/字段先定）
   2. `app/schemas.py` 加 Pydantic dataclass（**不**是 router model）
   3. `app/services/pexels_service.py`（核心服务，先实现 `_search_pexels` + `_download`，`resolve` 编排最后）
   4. `tests/test_pexels_service.py` 单元测试
   5. 跑 6.1-6.5 验收
   6. 写 `sop/Pexels素材合规.md`
   7. 跑 6.6 验证现有 pytest 不破
5. **最后**给我一个完成报告：贴 `git diff --stat` + 关键 ResolveItem 输出示例 + 跑过的验收项勾选清单

---

## 10. 不要做的事（重要提醒）

| 错的事 | 后果 | 正确做法 |
|--------|------|----------|
| 把 Pexels key 写进 `config/app.yaml` 明文 | key 泄漏 | 用 `os.environ["PEXELS_API_KEY"]` 从 .env 读 |
| 改 `config/app.yaml` 现有 key（比如 deepseek.api_key） | 改坏现有配置 | 只追加，**不**触碰 |
| 自己 git commit | 违反"不自动 commit" | `git add` + 等拍板 |
| 写 mock / fixture 替代真实下载 | 验收失真 | 必须真实下载 + ffprobe |
| 引入关键词白名单 | 复活反模式 | 已经删了，不要复活 |
| 改 ID-002 视觉导演提示词 | 越界 | 只动 `pexels_service.resolve` 接口 |
| 改其他 router | 越界 | 只新增 `pexels_service.py` |
| **新增 HTTP router / web UI** | **方向错（DeepSeek 没中间层）** | **不**做，**不**做，**不**做 |

---

**交接完成。开始干活前如果有任何歧义，先问 Hermes，不要猜。**
