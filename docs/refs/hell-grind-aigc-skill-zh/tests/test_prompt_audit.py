import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "skill" / "hell-grind-aigc-skill" / "scripts" / "audit_prompt.py"


def run_auditor(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def issue_codes(payload: dict) -> set[str]:
    return {entry["code"] for entry in payload["issues"]}


class PromptAuditTests(unittest.TestCase):
    def audit_json(self, text: str, medium: str = "video") -> dict:
        result = run_auditor("-", "--medium", medium, "--json", input_text=text)
        self.assertIn(result.returncode, {0, 1}, result.stderr)
        return json.loads(result.stdout)

    def test_json_contract_and_empty_prompt(self) -> None:
        result = run_auditor("-", "--medium", "video", "--json", input_text="")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        for key in [
            "valid_for_review",
            "score",
            "issues",
            "detected_modules",
            "assumptions",
            "network_requests",
            "database_operations",
        ]:
            self.assertIn(key, payload)
        self.assertFalse(payload["valid_for_review"])
        self.assertIn("P-EMPTY", issue_codes(payload))
        self.assertEqual(payload["network_requests"], 0)
        self.assertEqual(payload["database_operations"], 0)

    def test_medium_is_required_and_restricted(self) -> None:
        missing = run_auditor("-", input_text="一个人物")
        invalid = run_auditor("-", "--medium", "audio", input_text="一个人物")
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(invalid.returncode, 2)

    def test_image_requires_a_visible_subject(self) -> None:
        result = run_auditor("-", "--medium", "image", "--json", input_text="高级，震撼，电影感。")
        self.assertEqual(result.returncode, 1)
        self.assertIn("P-MISSING-SUBJECT", issue_codes(json.loads(result.stdout)))

    def test_video_requires_duration_camera_end_and_audio_boundary(self) -> None:
        result = run_auditor(
            "-",
            "--medium",
            "video",
            "--json",
            input_text="画面中一名女人站在门前。中景摄影机缓慢推进。",
        )
        self.assertEqual(result.returncode, 1)
        codes = issue_codes(json.loads(result.stdout))
        self.assertTrue({"P-MISSING-DURATION", "P-MISSING-CAMERA-END", "P-MISSING-AUDIO"}.issubset(codes))

    def test_file_input_is_read_only_and_text_output_reports_io_counts(self) -> None:
        prompt = (
            "画面中恰好一名女人站在门前。5 秒。"
            "摄影机从眼平中景缓慢推进，最终停在她的右手与门把同框的近景。"
            "只有室内环境音，无配乐、无字幕。"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prompt.md"
            path.write_text(prompt)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = run_auditor(str(path), "--medium", "video")
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertIn("Network requests: 0; database operations: 0", result.stdout)

    def test_detects_stillness_and_motion_conflict(self) -> None:
        payload = self.audit_json(
            "总时长 5 秒。画面中一名女人全程完全静止，同时持续奔跑。"
            "摄影机最终停在她的全身。只有环境音，无配乐。"
        )
        self.assertIn("P-CONFLICT-MOTION", issue_codes(payload))

    def test_detects_multiple_primary_camera_moves(self) -> None:
        payload = self.audit_json(
            "总时长 5 秒。画面中一名女人。摄影机锁定机位，同时环绕并快速推进，"
            "最终停在她的脸部特写。只有环境音。"
        )
        self.assertIn("P-CAMERA-CONFLICT", issue_codes(payload))

    def test_detects_timeline_overflow(self) -> None:
        payload = self.audit_json(
            "总时长 5 秒。画面中一名女人。0–3 秒抬头，3–6 秒跑向门口。"
            "摄影机最终停在关闭的门。只有脚步和环境音。"
        )
        self.assertIn("P-TIMELINE-OVERFLOW", issue_codes(payload))

    def test_reference_scope_and_duplicate_negatives_are_warnings(self) -> None:
        payload = self.audit_json(
            "总时长 5 秒。画面中一名女人，参考这张图。摄影机最终停在近景。"
            "只有环境音。无配乐，不要音乐，no score。"
        )
        codes = issue_codes(payload)
        self.assertIn("P-REFERENCE-SCOPE", codes)
        self.assertIn("P-NEGATIVE-DUPLICATE", codes)

    def test_detects_provider_parameters_mixed_into_master_prompt(self) -> None:
        payload = self.audit_json(
            "画面中恰好一名女人，5 秒。摄影机最终停在近景。只有环境音。"
            "seed 42, steps 30, CFG 7。"
        )
        self.assertIn("P-PLATFORM-MIXED", issue_codes(payload))

    def test_exact_dialogue_without_visual_boundary_is_warning(self) -> None:
        payload = self.audit_json(
            "总时长 5 秒。画面中一名女人。她逐字说“别开门”。"
            "摄影机最终停在她的右手。只有对白和室内环境音，无配乐。"
        )
        self.assertIn("P-DIALOGUE-VISUALIZATION", issue_codes(payload))

    def test_abstract_only_prompt_is_flagged(self) -> None:
        payload = self.audit_json("高级、震撼、电影感、史诗感。", medium="image")
        self.assertIn("P-ABSTRACT-ONLY", issue_codes(payload))

    def test_complete_original_image_and_video_prompts_pass_error_gate(self) -> None:
        image = self.audit_json(
            "画面中恰好一名女维修员，湿透灰雨衣，左手托住唯一红色工具箱。"
            "眼平中近景，冷天光从左后方进入，雨衣半哑光，16:9。",
            medium="image",
        )
        video = self.audit_json(
            "总时长 5 秒。画面中恰好一名女维修员。0–2 秒她看向门缝，2–4 秒后退半步，"
            "4–5 秒落定。摄影机从眼平中景缓慢推进，最终停在她与门把同框的近景。"
            "只有室内环境音和衣料声，无对白、无配乐、无字幕。"
        )
        self.assertTrue(image["valid_for_review"], image["issues"])
        self.assertTrue(video["valid_for_review"], video["issues"])


if __name__ == "__main__":
    unittest.main()
