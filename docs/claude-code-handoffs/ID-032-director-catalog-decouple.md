# Claude Code 交接包 — ID-032 导演素材库解耦 + 本地碰撞（已由 ID-034 落地）

> **状态**：✅ **已由 ID-034 落地**（2026-08-09）— 方案核心目标达成，实现路径与本节原始规划有偏差
> **来源**：用户在排查"C 线关闭不起作用 / 规划 >260s 慢"问题时提出架构批评
> **触发背景**：见下"背景"节

> 2026-08-09 注记：**方案核心目标已由 ID-034 落地**（见 [已完成-20260809-P线本地碰撞-ID034匹配修复.md](../improvements/已完成-20260809-P线本地碰撞-ID034匹配修复.md)）。
> 实现路径与本节原始规划的**差异**：
> - **素材库解耦** ✅ 已达成——prompt 注入改为词表包 `data/vocabulary_pack.json`（~3KB / ~385 词，比 385KB 全量清单小 8 倍），导演不再拿全量素材清单。
> - **配对时机**：本节规划"规划完成后独立配对阶段（`pair_broll_slots_with_local_assets`）写死 file 落库"；**实际实现改为执行期碰撞**（`broll_pexels.py::_resolve_pexels` 内 `_try_local_collision`，在下载之前）——用户拍板"碰撞发生在用户点击 slots 执行之前即可"，且 P 线 keywords 保持自由搜索词（不受词表约束），本地/在线决策完全与 LLM 解耦。
> - **未命中兜底**：与规划一致——未命中走 Pexels 在线下载（P 线）/降级 broll_pexels（L 线）。
> 原始"配对阶段写死 file_path 落库 + 执行期零搜索"方案保留为参考，未采用。

---

## 0. 一句话

把 385KB 素材库 catalog 从导演 LLM prompt 里**解耦**出来：导演只做"逐句核对画面需求 → 切片"；规划完成后新增**独立配对阶段**（切片素材 ↔ 本地素材库），命中→用本地，未命中→走 P 线（Pexels）下载。

---

## 1. 背景（为什么立项）

用户在排查 `http://127.0.0.1:54321/web/director.html` 规划慢（>260s）问题时提出：

> "导演应该是拿到稿件后，逐句去核对一下他需要什么样的画面和素材。之后切片就已经是完成了的，而不需要把素材库提供进去。拿到了导演的切片素材和素材库之间，其实还要再进行一次比对，这次比对其实是缺失的一个环节。……配对结束后，肯定还有空着的、没有用到的素材，再走 P 线去做下载。完成这几步之后，导演那儿应该就快了。"

**已实测根因**：`build_real_material_catalog` 把 **1387 个 VideoAsset 全量序列化 = 385,893 bytes JSON**（其中 1051 个未分类）塞进 prompt → deepseek-v4-pro 处理巨量输入 + 输出 50-60 slot 工序单 → 数分钟。

---

## 2. 现状 vs 提案

| 环节 | 现状 | 问题 |
|------|------|------|
| 规划期 | 385KB catalog 全量塞 prompt，LLM 逐条权衡 | **慢的根源** |
| 配对 | 无独立配对阶段；broll_local 在**执行期**现场搜索，broll_pexels 直接按 keywords 下载 | 配对被塞进执行期 |
| 未命中 | broll_pexels 是 LLM 主观指定，不是"配对未命中"的兜底 | 本地 1051 未分类素材可能闲置，P 线却重复下载 |

---

## 3. 方案（5 处改动）

1. **prompt 完全解耦素材库**
   - `app/services/director_prompt.py` L124-127：不再 `prompt.replace("[在此处粘贴素材库清单JSON]", ...)`；改为注入**最小场景清单**（仅场景名列表，几 KB）或完全移除。
   - `config/visual_director_v2.txt`：删掉"输入2:素材库清单"章节与规则 4.1"清单中存在的 category → 直接指定具体文件"；改为"输出画面**语义需求**，不指定具体文件名"（broll_local 从 `file` 精确指定改为纯语义字段 keywords/scenes/shot_types/tone/…）。

2. **新增独立配对阶段**
   - 新增 `pair_broll_slots_with_local_assets(db, plan, enabled_pipelines)`。
   - 在 `create_director_plan` 中 `parse_llm_plan` 之后、落库之前调用。
   - 对每个 broll 需求调现有 `app/services/asset_matcher.py::match_local_assets`（加权打分：keywords 命中 +1 / scenes +2 / AI 标签 +1）打分。
   - 命中（score ≥ 阈值，建议 **2**）→ `workflow=broll_local`，params 写死 `file` + `file_path` → 执行期策略 1 直接命中，**不再现场搜索**。
   - 未命中 → `workflow=broll_pexels`，走 P 线下载。
   - `mixed_host_broll` 同样配对写 file；P 禁用时未命中直接落 broll_local 随机兜底（`asset_matcher.pick_local_fallback`）。

3. **配对结果落库**：写回 `slot.workflow` + `params_json`，`plan_json` 一并持久化 → 执行期零搜索。

4. **移除 catalog 构建**：`app/routers/director.py::_plan_in_background` L126 不再调 `build_real_material_catalog`。

5. **执行期零改动**：`broll_local` 策略 1（params.file 精确）与 `broll_pexels::resolve_descending` 已就位，配对阶段喂饱参数即可。

---

## 4. 涉及文件

| 文件 | 改动 |
|------|------|
| `app/services/director_prompt.py` | 不注入 catalog（或注入精简场景清单）；删/改 `build_real_material_catalog` |
| `config/visual_director_v2.txt` | 删"输入2 素材库清单"，规则 4.1 改语义需求 |
| `app/services/director_service.py` | `create_director_plan` 调新增配对阶段 |
| `app/services/*`（新） | 新增配对函数 `pair_broll_slots_with_local_assets` |
| `app/routers/director.py` | `_plan_in_background` 不再构建 catalog |

---

## 5. 待定项

> ✅ 已全部由 ID-034 落地（2026-08-09）：
> - 配对命中阈值：实际采用"硬维度全中 + 软维度命中率 ≥75%（MIN_HIT_RATIO=0.75）+ keywords 精确打分"，而非规划中建议的简单 score≥2。
> - 未配对需求预取：与规划一致，保持**执行期惰性下载**（`resolve_descending` 降维），不做规划后预取。

---

## 6. 验收目标（落地后跑）

> ✅ ID-034 实际落地后的对应结论（2026-08-09）：
> - [x] prompt 体积从 ~385KB 降到 ~3KB（词表包，小 8 倍）——规划耗时缓解
> - [x] 配对/碰撞在**执行期**（点击 slots 执行后、下载前）毫秒级完成，`used_count` 登记落库
> - [x] 本地命中素材优先复用；未命中才走 Pexels 下载
> - [x] C 线关闭时 host/mixed_host_broll 仍被降级（管线开关逻辑不受影响）

---

## 7. 关联

- 慢根因调查：385KB catalog + deepseek-v4-pro 长输出
- C 线关闭链路（director.html 开关 → localStorage `dh_pipeline_flags` → getEnabledPipelines → POST pipelines → `enforce_host_rules`）
- 配对函数：`app/services/asset_matcher.py`
