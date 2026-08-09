import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_run_solver import SIA_SOD_CONFIG, generate_config_file
from solver_setting import (
    decode_solver_setting_snapshot,
    encode_solver_setting_snapshot,
    load_solver_setting_file,
    register_solver_setting_file,
    register_solver_setting_snapshot,
    snapshot_for_scenario,
)


class SolverSettingSnapshotTest(unittest.TestCase):
    def setting(self, setting_id="custom-setting"):
        return {
            "id": setting_id,
            "label": "Custom Setting",
            "configTemplate": SIA_SOD_CONFIG,
            "pot": 7,
            "effectiveStack": 91,
            "parameters": {"accuracy": 1},
        }

    def test_round_trips_monitor_setting_payload(self):
        encoded = encode_solver_setting_snapshot(self.setting())

        decoded = decode_solver_setting_snapshot(encoded)

        self.assertEqual(decoded["id"], "custom-setting")
        self.assertEqual(decoded["pot"], 7)
        self.assertEqual(decoded["effectiveStack"], 91)
        self.assertEqual(decoded["configTemplate"], SIA_SOD_CONFIG)

    def test_registers_snapshot_in_scenario_maps(self):
        configs = {}
        defaults = {}

        registered = register_solver_setting_snapshot(
            encode_solver_setting_snapshot(self.setting()),
            configs,
            defaults,
            expected_id="custom-setting",
        )

        self.assertEqual(registered["id"], "custom-setting")
        self.assertEqual(configs["custom-setting"], SIA_SOD_CONFIG)
        self.assertEqual(defaults["custom-setting"], {"pot": 7, "effective_stack": 91})

    def test_loads_and_registers_job_scoped_setting_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            setting_path = Path(temp_dir) / "setting.json"
            setting_path.write_text(json.dumps(self.setting()), encoding="utf-8")
            loaded = load_solver_setting_file(setting_path)
            configs = {}
            defaults = {}
            registered = register_solver_setting_file(
                setting_path,
                configs,
                defaults,
                expected_id="custom-setting",
            )

        self.assertEqual(loaded["id"], "custom-setting")
        self.assertEqual(registered, loaded)
        self.assertEqual(configs["custom-setting"], SIA_SOD_CONFIG)
        self.assertEqual(defaults["custom-setting"], {"pot": 7, "effective_stack": 91})

    def test_registered_setting_generates_the_effective_solver_config(self):
        setting = self.setting()
        setting["configTemplate"] = SIA_SOD_CONFIG.replace(
            "set_bet_sizes oop,flop,bet,33",
            "set_bet_sizes oop,flop,bet,10,25",
        )
        configs = {}
        defaults = {}
        register_solver_setting_snapshot(
            encode_solver_setting_snapshot(setting),
            configs,
            defaults,
            expected_id="custom-setting",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = generate_config_file(
                board="Ac,Kd,2h",
                output_dir=Path(temp_dir),
                config_template=configs["custom-setting"],
                range_oop="AA",
                range_ip="KK",
                pot=defaults["custom-setting"]["pot"],
                effective_stack=defaults["custom-setting"]["effective_stack"],
            )
            content = config_path.read_text(encoding="utf-8")

        self.assertIn("set_pot 7", content)
        self.assertIn("set_effective_stack 91", content)
        self.assertIn("set_bet_sizes oop,flop,bet,10,25", content)
        self.assertIn("set_range_oop AA", content)
        self.assertIn("set_range_ip KK", content)

    def test_rejects_snapshot_for_a_different_scenario(self):
        with self.assertRaisesRegex(ValueError, "does not match --scenario"):
            register_solver_setting_snapshot(
                encode_solver_setting_snapshot(self.setting()),
                {},
                {},
                expected_id="another-setting",
            )

    def test_serializes_effective_parent_scenario_for_child_process(self):
        encoded = snapshot_for_scenario(
            "custom-setting",
            {"custom-setting": SIA_SOD_CONFIG},
            {"custom-setting": {"pot": 7, "effective_stack": 91}},
        )

        self.assertEqual(decode_solver_setting_snapshot(encoded)["id"], "custom-setting")

    def test_auto_run_solver_accepts_custom_scenario_before_range_validation(self):
        encoded = encode_solver_setting_snapshot(self.setting())

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "auto_run_solver.py"),
                "1",
                "--scenario",
                "custom-setting",
                "--setting-snapshot",
                encoded,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("请提供 --range-path", result.stdout)
        self.assertNotIn("invalid choice", result.stderr)

    def test_auto_run_solver_accepts_job_scoped_setting_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            setting_path = Path(temp_dir) / "setting.json"
            setting_path.write_text(json.dumps(self.setting()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "auto_run_solver.py"),
                    "1",
                    "--scenario",
                    "custom-setting",
                    "--setting-file",
                    str(setting_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("请提供 --range-path", result.stdout)
        self.assertNotIn("Unknown Setting/scenario", result.stderr)

    def test_run_pipeline_accepts_monitor_snapshot_for_custom_setting(self):
        encoded = encode_solver_setting_snapshot(self.setting())

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_pipeline.py"),
                "1",
                "--dry-run",
                "--no-upload",
                "--scenario",
                "custom-setting",
                "--setting-snapshot",
                encoded,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("[DRY RUN]", result.stdout)
        self.assertNotIn("invalid choice", result.stderr)

    def test_run_pipeline_accepts_monitor_setting_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            setting_path = Path(temp_dir) / "setting.json"
            setting_path.write_text(json.dumps(self.setting()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "run_pipeline.py"),
                    "1",
                    "--dry-run",
                    "--no-upload",
                    "--scenario",
                    "custom-setting",
                    "--setting-file",
                    str(setting_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("[DRY RUN]", result.stdout)
        self.assertNotIn("Unknown Setting/scenario", result.stderr)


if __name__ == "__main__":
    unittest.main()
