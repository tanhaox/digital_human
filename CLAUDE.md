# digital_human — 项目级 CLAUDE.md

## 一、项目定位

数字人短视频生产流水线（FastAPI + SQLite + 本地 GPU）。
独立 git 仓库（2026-10-16 从外层 AI-Agent-Local 独立）。
- GitHub: `git@github.com:tanhaox/digital_human.git`（走 SSH）
- 本地根: `F:\AI-Agent-Local\digital_human\`

## 二、安全红线

1. 删除一律走回收站（send2trash）；禁 del / rm / os.remove。
   - 例外：build/cache 产物可直接删。
   - 已裁量例外：main.py 的 comp/legacy/slots/hf 四目录 rmtree（B18 拍板，见 docs/REFACTOR_PLAN.md B18 条）。
2. 数据库禁破坏性操作：禁 DROP DATABASE / DROP TABLE / TRUNCATE / 无 WHERE 的 DELETE。
3. 部署链路：本地 → GitHub → 服务器；禁止直接改服务器。
4. GPU 独享死命令：ComfyUI 与 llama 互斥独享大卡，禁双 AI 同时上卡（已机制化 gpu_mutex；违者驱动级卡死/批速 1/3 实锤 2026-10-17）。

## 三、工作流纪律

1. 先审计后修改：先只读 → 出计划 → 再执行。
2. 每批只做一个主题，独立 commit，可回滚。
3. 指令有歧义时停下报告，不擅自判断。
4. 回执 = 非技术语言结论 + 技术证据（文件:行号 / 命令输出）并列。
5. 每批结束必报：改了什么 / 为什么 / 验证命令与结果 / 风险 / 下一步。

## 四、技术操作规范

1. **禁用脚本批量替换**（历史事故：B15 误吞 raise/rollback、B16a-W5 丢缩进）。
2. 逐处手工改 + 每处 import 验证。
3. 审计报告/清单可能漂移（行号、文件数、引用数），执行前现场 grep 复核为准。
4. 不引入新依赖。
5. 不为测试通过而修改测试。
6. 结论一律给文件:行号证据；UNKNOWN 直接标注，禁止猜测。

## 五、删除类操作两步法

1. 第一步：标 DEPRECATED / 移 archive / 观察。
2. 观察期 ≥1 周。
3. 第二步：send2trash 真删（B03 与 B14 第二步均定 2026-10-23 后执行）。

## 六、读取清单（每次会话开始先读）

- 本文件
- docs/INDEX.md（文档唯一入口）
- docs/REFACTOR_PLAN.md（当前重构进度 + 冻结基线）
- docs/status/项目状态总览.md + docs/status/backlog.md（状态与挂账观察项）
- docs/AUDIT-系统地图.md / AUDIT-问题清单.md（如需）
- docs/design/ADR-001-slot-runtime-injection.md（如动 slot 族）

## 七、关键上下文

- pytest 基线：`pytest tests/ -m "not real_api"` → **6 failed / 376 passed / 6 deselected**（1021~22 看板 +13; 1026 风格栈/立意宪法/负提示词前置批 +22; 失败集不变=历史债）。
  已知失败项（历史债，与本轮重构无关）：
  - test_module_map ×3（AssertionError×2 / IndexError）
  - test_tts_chunk_semantic ×2
  - test_book_audience_map ×1
- 后端启动：`.venv\Scripts\python run_web.py`（端口 54321）。
- 数据库：data/pipeline.db（SQLite，已 gitignore 不进 git，U 盘备份）。
- .gitignore 已排除：data/materials/、.venv/、data/frames_cache/、.tmp/、logs/ 等。

## 八、进行中的重构（B 系列，详见 docs/REFACTOR_PLAN.md）

- 已完成：低风险族 + 环解族全部收官（B01-B19 中除下列待办外的全部批次）。
- 待办挂历：**2026-10-23 后** B03（垃圾真删）+ B14 第二步（cleanup.py / scripts/anim_director.py send2trash）。
- 未拍板：B20（director2 4294 行拆分）/ B21（大文件梯队）——长期挂起。
- backlog 观察项：anim_service 拆分阈值（2000 行）、library_service.py:103 ilike 同坑、scripts/web 等 ruff 长尾。
- 单独立档：docs/design/ADR-001-slot-runtime-injection.md（slot 环#5 接受现状 + 禁区清单）。

## 九、历史教训（避免重犯）

- 脚本批量替换是事故高发区（B15×2 / B16a×1），逐处手工改更稳。
- 审计报告的行号/文件数/引用数会漂移，现场 grep 为准。
- 明细清单可能严重不全（B08 清单 2 处、实测 20 处；B07 清单 5 处、实测 6 处）。
- git stash 是高风险操作，非必要不用（B16c 事故实录，见 git reflog 2026-10-16）。
- 变更涉及多文件时，先数文件数，超阈值（约 20）拆小波执行。
