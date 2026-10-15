# Pexels 素材合规 SOP

> **用途**: 规范 Pexels 视频素材在数字人视频 A 管线中的使用方式,确保符合 Pexels 授权协议(Pexels License)。  
> **适用对象**: 视觉导演、剪辑交付、任何使用 `app.services.pexels_service` 产出素材的下游工序。  
> **前置依赖**: 已配置 `PEXELS_API_KEY` 环境变量;已阅读 `docs/claude-code-handoffs/ID-003-pexels-resolve.md`。  
> **更新日期**: 2026-07-27

---

## 1. Pexels 授权核心要求

根据 [Pexels License](https://www.pexels.com/license/):

- ✅ **免费用于商业和非商业用途**。
- ✅ **允许修改、剪辑、合成**。
- ❌ **不得再次出售**未做实质性修改的原片(如直接上传 Pexels 视频到素材市场销售)。
- ❌ **不得将素材用于商标**或可能使素材中可识别人物名誉受损的场景。
- ⚠️ **必须展示署名**: "Photos/Videos provided by Pexels" 或类似表述,并标注摄影师。

> 中文展示示例:  
> "本视频部分素材由 Pexels 提供"  
> "Footage by {photographer} on Pexels"

---

## 2. 数据库强制字段(技术合规)

`MaterialAsset` 表必须 100% 包含以下字段,**NOT NULL**:

| 字段 | 含义 | 展示用途 |
|------|------|----------|
| `photographer` | 摄影师名 | 片尾/素材清单署名 |
| `photographer_url` | 摄影师主页 | 点击跳转 |
| `pexels_url` | 原片链接 | 合规溯源 |

**检查 SQL**:

```sql
SELECT COUNT(*) FROM material_assets
WHERE photographer IS NULL OR photographer_url IS NULL OR pexels_url IS NULL;
-- 必须为 0
```

---

## 3. 视觉导演调用规范

```python
from app.services.pexels_service import pexels_service

materials = pexels_service.resolve(
    query="port cranes",
    max_results=3,
    min_duration_sec=5,
)
```

返回的 `ResolveItem` 必须向下游传递以下字段:

- `photographer`
- `photographer_url`
- `pexels_url`
- `local_path`(如本地已下载)或 `source_url`(降级时)

**禁止**:

- 丢弃摄影师信息。
- 将 `degraded=True` 的远程 URL 直接作为最终成片输出而不在工程文件里替换成本地文件。

---

## 4. 成片展示要求

### 4.1 片尾/素材清单

每个使用 Pexels 素材的视频,片尾或描述中必须列出:

```
Videos provided by Pexels
- "{video_title}" by {photographer} on Pexels ({pexels_url})
```

若素材数量多,可合并为:

```
Additional footage provided by Pexels.
See full attribution list: {project_dir}/pexels_attribution.json
```

### 4.2 JSON 清单模板

服务层不提供自动生成清单功能,但推荐下游生成如下 JSON:

```json
[
  {
    "photographer": "John Doe",
    "photographer_url": "https://www.pexels.com/@johndoe",
    "pexels_url": "https://www.pexels.com/video/12345/",
    "local_path": "E:/数字人计划/materials/pexels_12345.mp4"
  }
]
```

---

## 5. Quota 与降级

- Pexels free tier 限流 **200 req/hour**、**20000 req/month**。
- 本服务每日下载上限由 `pexels_daily_download_quota` 控制(默认 200)。
- 超限后 `resolve()` 返回 `degraded=True` 的元数据,**不下本地**。
- 降级素材不得在最终成片里直接使用,必须在 quota 恢复后重新 `resolve()` 下载替换。

---

## 6. 验收清单

- [ ] 所有 `MaterialAsset` 记录 photographer / photographer_url / pexels_url 均非空。
- [ ] 成片片尾或描述包含 "Videos provided by Pexels" 或同等表述。
- [ ] 每条 Pexels 素材在片尾/清单中有独立摄影师署名。
- [ ] 未将降级(remote-only)素材直接交付。
- [ ] `PEXELS_API_KEY` 未写入任何 `.py` / `.yaml` / `.md` / commit message。

---

## 7. 违规后果

未按要求署名可能导致:

- 平台投诉或版权下架。
- Pexels API key 被封禁。
- 项目合规审计不通过。

---

**返回**: [SOP 索引](README.md)
