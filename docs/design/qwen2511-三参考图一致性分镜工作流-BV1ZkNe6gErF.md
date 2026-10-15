# Qwen-Edit-2511 三参考图一致性分镜工作流 (学习自B站)

> 来源: BV1ZkNe6gErF 「comfyui 人物+场景一致性分镜故事版」UP主 秋词秋词 (2026-07-15, 22:49)
> 学习方式: 1009 视频学习管线首单 (yt-dlp → whisper ASR → 时间轴挖掘 → glm-4v-flash 逐帧验证)
> 转写/帧证据: `.tmp/video_learn/BV1ZkNe6gErF/` (transcript.txt / frames/)；ops_timeline 与全部产物已迁至 `F:\AI-Agent-Local\comfyuitext\`

## 效果与定位

3 张参考图(人物/道具/场景) + 一段故事文本 → **一键批量生成 N 张分镜图**,人物脸/发型/服装与场景全片一致,同时产出每镜的视频提示词 → 直接喂 LTX 等视频模型。

**对我方产线的意义**: K2 宫格多视角已判死(一图多视角同空间做不到),本工作流是替代路线——不靠单图多视角,靠"参考图组 + LLM 按镜生成提示词行"批量保一致。Qwen-Edit-2511 我们已有底(三视图经验),LTX23 管线现成可接。

## 架构三段

### B. 出图腿 (Qwen-Image-Edit-2511) — 1009 JSON 定谳 (UP主分享包到手)
| 件 | 节点 (包) | JSON 成品配置 |
|---|---|---|
| UNET | `UNETLoader` | `qwen_image_edit_2511_fp8mixed.safetensors` (本地有 fp8_fix_by_FeiHou 替代) |
| CLIP | `CLIPLoader` (type=qwen_image) | `qwen_2.5_vl_7b_fp8_scaled.safetensors` ✅本地齐 |
| VAE | `VAELoader` | `qwen_image_vae.safetensors` (本地 `000_qwen_image_vae.safetensors`) |
| 提示词编码 | `TextEncodeQwenImageEditPlus` ×2 正/负 | 图片都连; 5图版=`...Pro_lrzjason`(本地缺) |
| 批量 | `easy promptLine` (easyuse) | max=32, 一镜一行拆 batch |
| LoRA×3 | `LoraLoaderModelOnly` | ①`Qwen-Image-2512-Lightning-4steps-V1.0` st=1(加速) ②`Add%20light%20and%20shadow` st=0.8(光影层次) ③`next-scene_lora-v2-3000` st=1(场景衔接) |
| 采样偏移 | `ModelSamplingAuraFlow` (非SD3!帧8 VLM读对了) | **shift=3.1** (默认1.73) |
| CFG 归一化 | `CFGNorm` | 1 |
| 批内分配 | `FluxKontextMultiReferenceLatentMethod` ×2 | ASR"脑效膜变量"实为此节点 |
| KSampler | `KSampler` | **steps=4, cfg=1**, euler+simple, denoise=1 (口播演示设7/1.5, 成品配 Lightning 4步 LoRA 故 steps=4; >8 人物崩的铁律不变) |
| 解码 | `VAEDecodeTiled` | tile=512, overlap=64 |

### A. LLM 腿 — JSON 定谳
- `llama_cpp_model_loader`: `Qwen-VL\Qwen3.5\Qwen3.5-9B-Q8_0.gguf` + `mmproj-Qwen3.5-9B-BF16.gguf`, chat_handler=`Qwen3.5` (QwenVL handler), n_ctx=16384
- `llama_cpp_parameters`: max_tokens=**12288** (口播演示4096, 成品拉满), top_k=30, top_p=0.9, min_p=0.05, temp=0.8
- 3× `llama_cpp_instruct_adv` 系统提示词全文: `F:\AI-Agent-Local\comfyuitext\llm\llm_prompts.json`
  (总控 3041字 = 商业短片导演/分镜统筹/视觉连续性监督三合一角色; ②分镜行提取 345字; ③LTX视频提示词 753字)
- 用户输入三段: 故事(每镜 画面/道具/动作/光影) + 参考图说明(图1人物/图2道具/图3背景) + 风格基调 → `JoinStrings` 拼接
- 参考图: `LoadImage`×3 → `ImageScaleToTotalPixels`(像素统一) → `ImageConcatMulti`(count=3, right, 左→右) → LLM images

### C. 视频腿 (出图后)
分镜图 + ③号 LLM 视频提示词 → LTX(帧数+图+提示词);视角不合意用 Qwen-Edit-2511 i2i 修朝向。

## 复刻缺口清单 (1009 本机 8188 盘点)
| 件 | 状态 | 动作 |
|---|---|---|
| 节点 17/21 | ✅ | llama_cpp 三件套缺 (需装 ComfyUI-llama-cpp 包, 但 Manager 未装→手动装) |
| CLIP/VAE/next-scene LoRA | ✅ 精确齐 | — |
| UNET fp8mixed | ⚠️ 有 fp8_fix_by_FeiHou | 改路径即用, 或下载原版 |
| Qwen-Image-2512-Lightning-4steps | ⚠️ 本地是 8steps-V2.0 | 配 steps=8 试跑, 或下载 4steps 版 |
| Add light and shadow LoRA | ❌ | 下载 (小文件) |
| Qwen3.5-9B-Q8_0.gguf + mmproj | ❌ ~12GB | 下载到 GGUF 目录 |
| TextEncodeQwenImageEditPlusPro_lrzjason | ❌ | 仅 5图版需要, 二期 |

## 已到手
- UP主 4 个工作流 JSON: `G:\下载\分镜故事版\分镜\` (3图/5图/自适应+视频提示词/教学演示)
- LLM 系统提示词全文: `F:\AI-Agent-Local\comfyuitext\llm\llm_prompts.json`

## 机制验证单 (1009 实弹通过 ✅)

**最小验证集判定正确**: 零下载复刻核心机制——本机 fp8_fix UNET + 已有 CLIP/VAE + 无 LoRA(steps 4→24) + LLM 环节静态替代(4 行提示词直填 easy promptLine)。
- 参考图: K2+ogipote-ep63 自造 3 张 (人物/胶片相机/夜街) → upload 进 input
- `msr_validate.json` (API 格式, 18 节点) validate 0错0警; promptLine 列表机制证实: **4 行→4 次执行→4 张独立分镜**, latent batch=1 与 UP 文档完全吻合
- 4090 单镜 24 步 fp8; 输出 4 张 1344×768
- **对抗式 VLM 一致性评分 8/10**: 发型/夹克/相机四格全一致; 镜4 为背影+暗光先天难判 (提示词设计如此); 场景按镜变而基底街景延续
- 证据: `comfyuitext/records/BV1ZkNe6gErF/result_shots/` (shot1-4.png + grid2x2.png), workflow: `comfyuitext/workflows/msr_validate.json` + `ref_gen.json`

### 人检终审 + 道具锁补丁 (1009 第二轮)
人检首单判词: **人物一致性✅ / 道具一致性弱(相机有黑有银) / 场景=风格继承✅但无布局锁**。
归因: ① **cfg=1 → 负面通道物理失效** (负面词"道具变形"是摆设; UP 成品配置 steps4/cfg1 同样如此) ② 提示词"胶片相机"是泛词, 正向无定锚 ③ 小物件画面占比低, 条件权重偏向人脸。

补丁 (`msr_prop_lock.json`, A/B 仅改两项): **cfg 1→3** (激活负面通道, CFGNorm 兜归一化) + **每行钉死相机属性** ("黑色皮革机身配银色顶盖")。
结果: 对抗式逐格比对——机身四格全黑/顶盖四格银白/镜头四格全黑, 人物依旧稳, 无畸形 ✅

**教训**: VLM 一致性审讯必须"逐格列值+跨格比对"格式——首轮它已说出"银色或黑色"信号却未判为缺陷; 人检终审抓到了。

### 扩镜8+多人双探 (1009 第三轮, 用户点单)
- **8镜单人物 ✅** (`msr_8shot.json`): promptLine max_rows=8 即可扩, 8张全出; 逐格探测=相机恒一台(佩戴式设计)/发色外套全锁/无畸形。附带战果=**道具堆叠根治: 相机全程佩戴式(肩/胸挂), 零手持交互** — 验证"分镜设计规避 > 生成层硬修"。代价: 8镜×24步≈25min (Lightning 4步LoRA可砍70%, 效果件回归时验)
- **4镜双人物 ⚠️半通** (`msr_multi.json`, image2=第二角色): 两人**同框时身份全锁**(B白发围裙/A发型外套), 但**出场纪律不保**——镜3/镜4提示词写了双人, 模型只画主角(2/4缺席); 镜2混入模糊第三人。归因=提示词遵循度问题非机制问题: 主角参考权重占优, 次要角色需正向强化("双人镜头,老年摊主站在摊位后…"前置)。
- **人检补判 (1009第四轮)**: ①镜2**风格明显漂移**(偏绘本插画) + **相机挂在老人身上**——根因=**参考图槽位挤占**: TextEncodeQwenImageEditPlus 标准版仅 image1/2/3 三槽, 双人物运行中 image2 被角色B占用, **相机道具参考被整个挤出** → 道具失去锚点自由归属(挂到老人胸前) + 该镜失锚风格漂移。镜2提示词含糖葫芦手持交互(2511弱区)是叠加因素。②"镜4尺寸不同"=拼图脚本锅(非均匀单元格), 源图4张全 1344×768, 已重拼修正。
- **多人模式道具锁解法 (按优先级)**: ①零下载=**道具P进人物参考图**(佩戴式合成ref, 构图即绑定, 归属天然锁) ②道具并入场景ref ③二期5图Pro节点(`TextEncodeQwenImageEditPlusPro_lrzjason`, 本地缺) ④纯文字锚(最弱, shot2已证)。
- **人检五判 (1009第五轮, 8镜终审)**: ①**技术跑通≠分镜成立**——8镜全是人物为主体中近景, 无景别梯度/无空镜/人物占比恒定=朋友圈照片墙。归因=**静态替代行只写了"画面"没写"导演"**; promptLine行=完整构图指令, 写什么出什么, 机制无罪。分镜纪律需求: 景别阶梯(远全中近特)+≥2空镜+主角占比30%↔100%摆动+三幕微结构——UP主总控提示词(3041字商业短片导演角色)正是干此, kimi闭环或按纪律手写行二选一。②**服装分层漂移**: 参考图=卡其工装夹克+黑色连帽内搭(敞怀双袋), shot2黑色内搭丢失+外套版型变(扣领无袋)——2511对参考图次要服装层权重低, 主线条(脸/发色/大件色块)优先。修法=每行前置全装束文字锚(cfg3下文字锚已实证有效)。③VLM探测问题单未列服装分层项→漏判, **探测跟着问题走, 问题单须覆盖服装分层/配饰层**。
- 证据: `comfyuitext/records/BV1ZkNe6gErF/result_8shot/grid2x4.png`, `result_multi/grid2x2.png`

## 待办
- [ ] 装 ComfyUI-llama-cpp 节点包 (手动, Manager 缺失) — 仅自包含化需要
- [ ] 效果件回归: ① 2512-Lightning (steps 24→4, 验速度/质量权衡) ② 光影 LoRA 0.8 ③ next-scene LoRA ④ 5图 Pro
- [ ] LLM 环节接 kimi 实弹 (本次静态替代已证机制, 动态生成待接)
- [ ] 消融实验: 固定种子 vs 参考图 各自贡献度


---
## 后续走向 (1009-1009晚 归档)

- **动起来已判**: 关键帧出片后 H3 两条路 — 钉子链(AddGuide)=融变过渡**不被允许**(人检); **分段独立i2v+拼接=产线正路**(硬切/锚兑现/单段重roll)。全档: `comfyuitext/workflows/h3-multiguide/`
- **多视图/三视图去向**: 骨骼图驱动线开线 — jasonbai本地骨架(已验证) + 姿态库3220张 + 本线导演层行文, 见 `comfyuitext/workflows/qwen-pose-multiview/` 与 `qwen2509-骨骼图多视图一致性-BV1dUqhB6ExX.md`
- **总纲**: 全部可控>效果, 每版自检自由度清单 (用户1009晚定调)
