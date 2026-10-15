# ComfyUI 出图全局约定 (SSOT)

> 适用所有 ComfyUI 出图流程, 不限于 Krea2 / Wan2.2 / Z-Image / Boogu 等任意模型或工作流.
> 创建日期: 2026-07-22

## 铁律

### 输出路径

```
G:\图片\<项目子文件夹>\<文件>
```

- **根目录固定** `G:\图片\` (G 盘图片分区)
- **必须按项目分子文件夹** (例: `pets/`, `wedding_2026/`, `digital_human_avatar/`, `concept_art/`)
- **项目名只允许** 字母数字 + `-` + `_`, 其它字符自动转 `_`
- **绝对路径优先**, 任何"相对路径/用户主目录/comfy_outputs/"都不要用

### 文件命名

```
krea2_<YYYYMMDD_HHMMSS>_<prompt头40字符>_i.png
```

或按模型前缀:
- `krea2_*` Krea2 Turbo FP8
- `z_image_*` Z-Image Turbo
- `flux2_*` Flux2-Klein
- `wan_*` Wan2.2
- `boogu_*` Boogu Image Edit

### 浏览器显示

- 用 **ComfyUI 内置 `/view` URL** 在浏览器里给用户本地查看
- **不要** `file:///` (会触发 Windows 照片 app)
- **不要** 试图嵌入第三方看图器
- 浏览器显示仅用于用户查看，**不代表 Agent 获得了视觉验收授权**

### Agent 看图隔离（默认铁律）

- 凡由 **ComfyUI / 本地生图工作流**生成的图片，默认视为仅留在本机的测试产物；无论提示词普通或敏感，Agent 都**不判断敏感度，统一不看图**。
- 生成后禁止自动调用 `vision_analyze`、浏览器截图/视觉识别、桌面截图识别，或以其他方式把输出像素送入模型上下文。
- 自动验收只检查：ComfyUI 执行状态与日志、`prompt_id`、文件存在性、数量、大小、格式、分辨率、元数据和校验值。
- 交付可返回绝对路径或在本机打开 ComfyUI `/view` 给用户自看；不得把“已生成”表述成“画面已验收”。
- 仅当用户针对某张生成结果**明确要求看图/检查画面**时，才临时启用该图的视觉分析；授权不自动延续到下一张图。
- 本规则只关闭“生图结果的自动看图回路”；生图以外的 UI、网页、文档及用户提供图片任务，视觉能力照常使用。

### 归档 (仅跑通的工作流)

- 跑通的 workflow JSON → `E:\AI\comfyui_workflows\`
- 跑通的测试图 → `E:\AI\comfyui_workflows\` 同步存一份 (便于复盘)

## 一键脚本

`C:\Users\tanha\AppData\Local\hermes\scripts\run_krea2.py`

```bash
"C:/Program Files/Python311/python.exe" "C:/Users/tanha/AppData/Local/hermes/scripts/run_krea2.py" "prompt" [width] [height] [seed] [project]
```

参数:
- `prompt` (必填)
- `width` 默认 1024
- `height` 默认 1024
- `seed` 默认 `int(time.time()) % 999999`
- `project` 默认 `krea2` (决定 `G:\图片\<project>\`)

例:
```bash
# 默认项目目录 G:\图片\krea2\
python run_krea2.py "a fluffy cat on a windowsill"

# 项目目录 G:\图片\pets\
python run_krea2.py "golden retriever puppy in garden" 1280 720 42 pets

# 项目目录 G:\图片\wedding_2026\
python run_krea2.py "wedding couple silhouette at sunset" 1280 720 7 wedding_2026
```

## 推广

所有 ComfyUI 出图脚本 (Krea2 / Wan2.2 / Z-Image / Boogu / Flux2 ...) 都按这个规范.
新脚本直接复用 `run_krea2.py` 的 `submit_and_fetch + download_view + browser view-url` 三段.

## 例外

- 临时调试 / 一次性测试可以走 `E:\tmp_*.png` (项目目录除外)
- 数字人 M0 出图场景: `G:\图片\digital_human\` (项目名固定, 因为是常驻流水线)