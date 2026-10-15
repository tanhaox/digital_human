# 已完成-20260912-四视图角色定妆照 + Krea2EncodeRebalance 考据

> 一次会话闭环: 从 Krea2EncodeRebalance 四参考图节点考据 → 四视图角色参考表配方 → 老谭读书男女配对角色定稿 → 定妆照入库。
> 产出即用: 后续任意场景"图中人"出镜 (特写挂 image1) / 接 H3 图生。

---

## 一、Krea2EncodeRebalance 考据 (本机节点源码级)

节点包: `E:\AI\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-ConditioningKrea2Rebalance\` (同包另有 `Krea2EditRebalance`/`ConditioningKrea2Rebalance`)

| 项 | 结论 |
|---|---|
| 输入 | `text + clip + image1-4` (每个 imageN 配 `imageN_tokens` 档位) |
| token 档位 | `low=256 / normal=512 / high=1024 / max=1280` px (视觉编码分辨率, conditioning_rebalance.py `RESOLUTIONS`) |
| Picture 机制 | 节点**自动**在正文前拼 `Picture N: <|vision_start|><|image_pad|><|vision_end|>`, 正文用 `Picture N` 寻址; **用户不写 Picture N: 前缀** |
| 编号规则 | 按**已连线槽位顺序**编号, 非槽位号 — 只连 image2+image4 → 它们是 Picture 1/2 |
| 模板 | 走 KREA2_SYS_TEMPLATE (Qwen3-VL chat 格式, edit 系统提示), 自然语言指令风格 |
| 提示词写法 | `the character from Picture 1 ... in the style of Picture 2` 类寻址; 多人严禁代词; 补 `one figure each, no duplicates` |
| tokens 选择 | 人物身份/服装锚=high; 风格/姿势/氛围=low |
| 生态佐证 | KonokoAz/ComfyUI-Krea2-Reference 同机制 (Qwen3-VL 原生 vision token, 非img2img); "参考图的颜色=身份" |

## 二、四视图角色参考表配方 (从三视图工坊模板改造)

源: `web/tools/threeview_prompt_builder.html` 主模板 (左40/右60 三视图) → 四视图版改造点:

1. **布局**: 左 35% 面部特写 (锁骨裁切, 禁肩带) / 右 65% 四身 A-pose (正/左/右/背, 等高同比例同地平线)
2. **身份锁定段** (全图最高优先级): GROUND TRUTH + 固定面部锚点 + **位置固定型唯一标识** (泪痣/眉疤, 比泛红脸颊锚定强) + 侧脸 90° 解剖学旋转 + 左右镜像反向条款 + 禁止漂移五连
3. **四视图专属负面条款** (正向写, 因 K2 负面通道无效): `both profiles facing the same direction` / 第五人形 / extra figures
4. 跑法: K2 2ST 生产档 (`sandbox/anim_pipeline/k2.py.build_workflow`, 白鲸记NSW LoRA@1.0, 1280×720→1080p, 16-20s/张); 工具脚本 `.tmp/fourview_test.py`(女) `.tmp/fourview_male.py`(男)

## 三、角色定稿 (老谭读书配对, 用户画检"行差不多")

| | 女主 | 男主 |
|---|---|---|
| 气质 | 25岁干练精明御姐 | 35岁沉稳睿智 |
| 面部锚 | 狐狸眼上挑+蓝眸+唇珠 | 深邃灰蓝眼+浓密剑眉+下颌线 |
| 唯一标识 | **右眼下泪痣** | **左眉尾浅旧疤** |
| 发型 | 银灰白侧分短发微蓬 | 银灰白利落短发侧分 |
| 体型 | 8头身微丰满沙漏肉感 (胸撑衬衫/极细腰/宽臀/大腿根饱满) | 8头身精瘦结实倒三角 (宽肩窄腰/含蓄肌肉) |
| 服装 | 白长袖衬衫卷袖口+黑包臀超短裙(低腰线)+黑细高跟 | 白长袖衬衫卷袖口敞领+黑修身西裤+黑皮鞋 |

定稿 seed: 女 777010, 男 777022。

## 四、定妆照入库 (`outputs/动画/_资产/`)

> 0912 用户纠偏后定稿: **母本 = 原图整张 1920×1080, 不做任何裁切** (曾误存切边版+特写裁片, 已复原)。

| 文件 | 说明 |
|---|---|
| 定妆照_女主_四视图_20260912.png | 母本 = 原图整张 (1920×1080, 左特写+右四身完整) |
| 定妆照_男主_四视图_20260912.png | 母本 = 原图整张 |
| 定妆照_{女,男}主_四视图_提示词.txt | 完整规格+seed, 改妆即改即跑 |

特写裁片 (参考图实验用, 白隙列扫描自动分割所得) 移至 `.tmp/定妆特写裁片_*.png`, 不算定妆资产。

## 五、迭代史与路线分叉 (关键决策)

| 版本 | 改动 | 结果 |
|---|---|---|
| v1 | 棕短发女孩(套1骨架+套2角色合并) | 结构全过 |
| v2 | 灰白发御姐+泪痣+超短裙+高跟 | 全过 |
| v3 | 瘦+低腰线 | 侧脸同向翻车, 重roll 修正 |
| v4 | 长袖卷袖+干练+长腿 | 全过 |
| v5 | 九头身极瘦路线 | 比例卡 6-8 头身抽卡 |
| ref1/2 | **参考图路线** (image1 挂 9头身参考) | ❌ 餐厅背景渗漏压不住 |
| v6 | 用户给参考图**原始提示词** → 8头身微丰满肉感纯文字灌 | ✅ 一次全绿 (定稿) |
| 男 1+3roll | 配对设计 | 777022 定稿 (正/左/右/背标准序) |

## 六、教训 (五条在册)

1. **身体规格纯文字灌 >> 参考图**: 参考 vision token 带整场景渗漏 (吊灯/桌椅/墙), 文字锁不住; 精确身体规格文字一次到位。LLM公式=初始假设路线再次实证。
2. **头身比是抽卡项**: 同 prompt 波动 6-8 头身, 文字 ("头高严格为九分之一") 推不动; 批 roll 3 seed 择优。
3. **侧脸同向 = 四视图高频翻车点**, 每张必查。
4. **glm-4v-flash 分工**: 结构项 QC 可用; bbox/坐标不可信 (VLM 引导裁剪两版全废, 且对同一图前后矛盾)。
5. **节点损坏在册**: RMBG-2.0 缺模型文件 (`process_res` KeyError); InspyrenetRembg CPU 卡死 180s+ (interrupt 才清)。抠图需求走确定性算法或换节点。

杂项: ComfyUI 中途掉线一次 (run_nvidia_gpu.bat 重启 ~60s 起); 重 roll 必换 seed (节点缓存回旧图); 废片 `.tmp/fourview_*` 留待 ID-058 保留策略清理 (回收站红线)。

## 七、后续接口

- 特写 → Krea2EncodeRebalance image1 (tokens=high, `Picture 1` 寻址) = 任意场景出镜
- 四视图母本 → H3 图生 (动画链 v1)
- 工坊四视图切换档 + 出镜链封装 → backlog ID-060
