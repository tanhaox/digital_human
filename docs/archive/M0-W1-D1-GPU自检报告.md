# M0 W1 D1 GPU 自检报告

**日期**: 2026-07-22
**自检人**: Hermes（nvidia-smi 实测，等效 GPU-Z 字段）

## GPU-Z 截图关键数据
- 显卡型号: RTX 4090（eGPU，CUDA runtime cuda:0）+ RTX 4060 Laptop（cuda:1）
- 显存: **49,140 MB**（4090）/ 8 GB（4060，仅备用）
- CUDA: 13.3（Toolkit 已装 v12.6 + v13.3；ComfyUI 便携版自带 torch 2.7.0+cu126）
- 驱动版本: **610.62**（≥531 ✅）
- 显存类型: GDDR6X（4090）

## 自检结论
- [x] **A 级（完美）** — ≥12 GB 显存
- [x] 决策: **本地完整 workflow**；Fish Speech / F5-TTS 本地推理；云端/API 仅作 fallback

## 后续动作
- [x] 安装 ComfyUI（便携版 v0.28.0，`E:\AI\ComfyUI_windows_portable`）
- [x] 必备节点包：Comfyroll(175) / WAS(220) / VideoHelperSuite(40) / Manager / Crystools / DD-Translation / Prompt-Assistant
- [x] 部署 Fish Speech / F5-TTS（完成，见 `TTS栈部署记录.md`，GPU 冒烟均出中文 wav）
- [ ] 注册 ElevenLabs API key（需用户本人注册）
- [x] 云端 fallback：不需要（A 级）
