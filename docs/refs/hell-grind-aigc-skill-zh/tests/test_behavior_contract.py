import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skill" / "hell-grind-aigc-skill"


class BehaviorContractTests(unittest.TestCase):
    def test_all_expected_references_are_directly_routed(self) -> None:
        expected = {
            "methodology-evidence.md",
            "prompt-architecture.md",
            "image-prompt-crafting.md",
            "video-prompt-contract.md",
            "reference-asset-control.md",
            "spatial-blocking.md",
            "camera-editing-language.md",
            "performance-direction.md",
            "action-physics-vfx.md",
            "lighting-color-material.md",
            "dialogue-audio.md",
            "continuity-control.md",
            "negative-constraints.md",
            "multi-shot-sequences.md",
            "iteration-selection.md",
            "failure-diagnosis.md",
            "production-workflow.md",
            "project-schemas.md",
            "project-qa-gates.md",
            "prompt-preservation.md",
            "prompt-quality-rubric.md",
            "prompt-examples.md",
        }
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        routed = set(re.findall(r"references/([a-z0-9-]+\.md)", skill))
        self.assertEqual(routed, expected)
        self.assertEqual({path.name for path in (SKILL_ROOT / "references").glob("*.md")}, expected)

    def test_package_has_one_skill_and_no_research_media_or_bulk_corpus(self) -> None:
        self.assertEqual(list((REPO_ROOT / "skill").rglob("SKILL.md")), [SKILL_ROOT / "SKILL.md"])
        banned_suffixes = {".mp4", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".webp", ".jsonl"}
        banned = [path for path in SKILL_ROOT.rglob("*") if path.is_file() and path.suffix.lower() in banned_suffixes]
        self.assertEqual(banned, [])
        for path in SKILL_ROOT.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".csv", ".py"}:
                self.assertNotRegex(path.read_text(), r"https?://", str(path))

    def test_behavior_routes_protect_scope_and_use_minimal_repairs(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text()
        image = (SKILL_ROOT / "references" / "image-prompt-crafting.md").read_text()
        preserve = (SKILL_ROOT / "references" / "prompt-preservation.md").read_text()
        failure = (SKILL_ROOT / "references" / "failure-diagnosis.md").read_text()
        architecture = (SKILL_ROOT / "references" / "prompt-architecture.md").read_text()
        self.assertIn("简单", image)
        self.assertIn("导演版", skill)
        for phrase in ["主体身份与数量", "逐字对白", "镜头时长"]:
            self.assertIn(phrase, preserve)
        self.assertLess(failure.index("资产"), failure.index("提示词"))
        self.assertIn("不要默认在 L7 继续增加否定词", architecture)
        self.assertIn("可观察", architecture)
        self.assertIn("Do not treat prompt writing as authorization to generate media", skill)
        self.assertIn("python3 scripts/audit_prompt.py", skill)
        self.assertIn("For identity drift, inspect the approved asset state and reference scope first", skill)

    def test_all_local_tools_report_zero_network_and_database_operations(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        for name in ["init_project.py", "validate_project.py", "audit_prompt.py"]:
            text = (scripts / name).read_text()
            self.assertRegex(text, r"[Nn]etwork requests")
            self.assertRegex(text, r"database operations")


if __name__ == "__main__":
    unittest.main()
