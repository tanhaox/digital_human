import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skill" / "hell-grind-aigc-skill"


class SkillContractTests(unittest.TestCase):
    def test_v2_version_and_router_cover_all_entry_modes(self) -> None:
        self.assertEqual((SKILL_ROOT / "VERSION").read_text().strip(), "2.0.0")
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        for phrase in [
            "生产管理",
            "图片从零生成",
            "图片润色扩写",
            "视频从零生成",
            "视频润色扩写",
            "失败诊断",
            "精简版",
            "标准版",
            "导演版",
        ]:
            self.assertIn(phrase, skill)

    def test_skill_frontmatter_is_trigger_focused_and_body_is_compact(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text()
        description = re.search(r"(?m)^description: (.+)$", text).group(1)
        self.assertTrue(description.startswith("Use when"))
        self.assertLessEqual(len(text.splitlines()), 500)

    def test_core_methodology_references_define_evidence_and_seven_layers(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        evidence_path = SKILL_ROOT / "references" / "methodology-evidence.md"
        architecture_path = SKILL_ROOT / "references" / "prompt-architecture.md"
        self.assertTrue(evidence_path.is_file())
        self.assertTrue(architecture_path.is_file())
        evidence = evidence_path.read_text()
        architecture = architecture_path.read_text()
        for phrase in ["115,450", "7,482", "15.41", "规则匹配", "因果"]:
            self.assertIn(phrase, evidence)
        for layer in range(1, 8):
            self.assertRegex(architecture, rf"(?m)^### L{layer} ")
        for phrase in ["必须保持", "本镜变化", "禁止出现", "冲突优先级", "平台适配层"]:
            self.assertIn(phrase, architecture)
        for relative in ["references/methodology-evidence.md", "references/prompt-architecture.md"]:
            self.assertIn(relative, skill)

    def test_image_asset_and_spatial_guides_are_actionable(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        image = (SKILL_ROOT / "references" / "image-prompt-crafting.md").read_text()
        assets = (SKILL_ROOT / "references" / "reference-asset-control.md").read_text()
        spatial = (SKILL_ROOT / "references" / "spatial-blocking.md").read_text()
        self.assertEqual(len(re.findall(r"(?m)^\d+\. ", image)), 11)
        for phrase in ["写作公式", "常见失败", "检查问题", "原创短例"]:
            self.assertGreaterEqual(image.count(phrase), 11)
        for phrase in [
            "identity invariants",
            "state variables",
            "多视图",
            "inherit",
            "exclude",
            "道具",
            "场景",
            "权利状态",
        ]:
            self.assertIn(phrase, assets)
        for phrase in ["解剖左", "屏幕左", "前景", "中景", "背景", "轴线", "唯一物体", "精确人数"]:
            self.assertIn(phrase, spatial)
        for relative in [
            "references/reference-asset-control.md",
            "references/spatial-blocking.md",
        ]:
            self.assertIn(relative, skill)

    def test_video_camera_performance_and_action_guides_are_actionable(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        video = (SKILL_ROOT / "references" / "video-prompt-contract.md").read_text()
        camera = (SKILL_ROOT / "references" / "camera-editing-language.md").read_text()
        performance = (SKILL_ROOT / "references" / "performance-direction.md").read_text()
        action = (SKILL_ROOT / "references" / "action-physics-vfx.md").read_text()
        self.assertEqual(len(re.findall(r"(?m)^\d+\. ", video)), 12)
        for phrase in [
            "open_state",
            "beat_timeline",
            "camera_start",
            "camera_path",
            "camera_end",
            "close_state",
            "continuity_in",
            "continuity_out",
            "must_hold",
            "changes_here",
            "must_not_appear",
            "audio_cues",
            "risk_focus",
        ]:
            self.assertIn(phrase, video)
        for phrase in ["起始构图", "主运动", "稳定方式", "对焦", "结束构图", "焦段视觉效果"]:
            self.assertIn(phrase, camera)
        for phrase in ["视线", "呼吸", "停顿", "面部", "身体重心", "静止"]:
            self.assertIn(phrase, performance)
        for phrase in ["准备 → 发力 → 接触 → 反作用 → 落定", "投掷", "撞击", "打斗", "爆炸", "粒子"]:
            self.assertIn(phrase, action)
        for relative in [
            "references/camera-editing-language.md",
            "references/performance-direction.md",
            "references/action-physics-vfx.md",
        ]:
            self.assertIn(relative, skill)

    def test_sensory_continuity_negative_and_multishot_guides_exist(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        expected = {
            "lighting-color-material.md": ["光源", "方向", "软硬", "对比", "曝光保护", "60:30:10", "材质响应"],
            "dialogue-audio.md": ["逐字对白", "发声时间", "静默尾拍", "对白不视觉化", "环境底床", "拟音", "混音优先级"],
            "continuity-control.md": ["身份连续性", "状态连续性", "空间连续性", "轴线", "动作连续性", "光线", "天气", "声音连续性"],
            "negative-constraints.md": ["风险驱动", "去重", "正向契约优先", "冲突压缩"],
            "multi-shot-sequences.md": ["镜头数量", "每段时长", "切点", "接点", "蒙太奇", "最终落点"],
        }
        for filename, phrases in expected.items():
            path = SKILL_ROOT / "references" / filename
            self.assertTrue(path.is_file(), filename)
            text = path.read_text()
            for phrase in phrases:
                self.assertIn(phrase, text, f"{filename}: {phrase}")
            self.assertIn(f"references/{filename}", skill)

    def test_iteration_failure_and_production_guides_are_complete(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        failure = (SKILL_ROOT / "references" / "failure-diagnosis.md").read_text()
        iteration = (SKILL_ROOT / "references" / "iteration-selection.md").read_text()
        workflow = (SKILL_ROOT / "references" / "production-workflow.md").read_text()
        examples = (SKILL_ROOT / "references" / "prompt-examples.md").read_text()
        for heading in [
            "资产与身份",
            "空间与连续性",
            "动作与表演",
            "摄影与剪辑",
            "对白与声音",
            "光色与材质",
        ]:
            self.assertIn(heading, failure)
        for code in [
            "F-ID-DRIFT",
            "F-COUNT",
            "F-PHYSICS",
            "F-CAMERA-CONFLICT",
            "F-DIALOGUE-TEXT",
            "F-MATERIAL",
        ]:
            self.assertIn(code, failure)
        for phrase in ["资产", "镜头契约", "提示词", "平台适配", "生成随机性", "后期"]:
            self.assertIn(phrase, failure)
        for phrase in ["batch_id", "changed_variables", "hypothesis", "decision", "next_action"]:
            self.assertIn(phrase, iteration)
        for phrase in ["质量阈值", "连续两个批次", "预算", "能力边界", "后期修复"]:
            self.assertIn(phrase, iteration)
        for stage in [
            "00_brief", "01_story", "02_assets", "03_scenes", "04_shots",
            "05_prompts", "06_generations", "07_review", "08_edit", "09_delivery",
        ]:
            self.assertIn(stage, workflow)
        self.assertNotRegex(examples, r"https?://")
        self.assertNotIn("Hell Grind", examples)
        for relative in ["references/iteration-selection.md", "references/failure-diagnosis.md"]:
            self.assertIn(relative, skill)

    def test_single_parent_skill_routes_both_internal_workflows(self) -> None:
        skill_files = list((REPO_ROOT / "skill").rglob("SKILL.md"))
        self.assertEqual(skill_files, [SKILL_ROOT / "SKILL.md"])
        text = skill_files[0].read_text()
        self.assertRegex(text, r"(?m)^name: hell-grind-aigc-skill$")
        self.assertIn("生产管理工作流", text)
        self.assertIn("提示词创作工作流", text)
        self.assertIn("图片从零生成", text)
        self.assertIn("图片润色扩写", text)
        self.assertIn("视频从零生成", text)
        self.assertIn("视频润色扩写", text)
        self.assertIn("不调用付费模型", text)

    def test_prompt_contracts_cover_image_11_and_video_12_modules(self) -> None:
        image = (SKILL_ROOT / "references" / "image-prompt-crafting.md").read_text()
        video = (SKILL_ROOT / "references" / "video-prompt-contract.md").read_text()
        image_items = re.findall(r"(?m)^\d+\. ", image)
        video_items = re.findall(r"(?m)^\d+\. ", video)
        self.assertEqual(len(image_items), 11)
        self.assertEqual(len(video_items), 12)

    def test_ui_metadata_and_default_prompt_are_discoverable(self) -> None:
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text()
        self.assertIn('display_name: "Hell Grind AIGC Skill"', metadata)
        self.assertIn("$hell-grind-aigc-skill", metadata)
        description = re.search(r'short_description: "([^"]+)"', metadata).group(1)
        self.assertGreaterEqual(len(description), 25)
        self.assertLessEqual(len(description), 64)

    def test_v2_readme_changelog_and_metadata_are_current(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text()
        for phrase in [
            "v2.0.0",
            "七层",
            "audit_prompt.py",
            "--strict-v2",
            "v1",
            "模型无关",
            "0 个网络请求",
            "MIT",
        ]:
            self.assertIn(phrase, readme)
        self.assertIn("## 2.0.0 - 2026-08-07", changelog)
        for relative in [
            "skill/hell-grind-aigc-skill/scripts/init_project.py",
            "skill/hell-grind-aigc-skill/scripts/validate_project.py",
            "skill/hell-grind-aigc-skill/scripts/audit_prompt.py",
        ]:
            self.assertTrue((REPO_ROOT / relative).is_file(), relative)
        self.assertIn("diagnose", metadata.lower())
        self.assertIn("$hell-grind-aigc-skill", metadata)

    def test_references_include_safety_and_fixed_outputs(self) -> None:
        preserve = (SKILL_ROOT / "references" / "prompt-preservation.md").read_text()
        rubric = (SKILL_ROOT / "references" / "prompt-quality-rubric.md").read_text()
        workflow = (SKILL_ROOT / "references" / "production-workflow.md").read_text()
        for phrase in ["硬约束", "修改摘要", "平台适配层"]:
            self.assertIn(phrase, preserve)
        for phrase in ["质量评分", "负面约束", "假设与待确认项"]:
            self.assertIn(phrase, rubric)
        self.assertIn("0 个网络请求", workflow)
        self.assertIn("0 次数据库读写", workflow)


if __name__ == "__main__":
    unittest.main()
