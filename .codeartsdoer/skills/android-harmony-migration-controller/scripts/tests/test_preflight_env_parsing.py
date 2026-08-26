from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import preflight_env  # noqa: E402


class PreflightParsingTest(unittest.TestCase):
    def test_android_override_values_win_over_physical_values(self) -> None:
        with patch.object(preflight_env, "adb") as adb:
            adb.side_effect = [
                "Physical size: 1080x2424\nOverride size: 1080x2400\n",
                "Physical density: 420\nOverride density: 440\n",
            ]
            self.assertEqual(
                ("1080x2400", "440"),
                preflight_env.current_size_density("android", "emulator-5554"),
            )

    def test_harmony_density_falls_back_to_explicit_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harmony-config-") as temp_name:
            config = Path(temp_name) / "config.ini"
            config.write_text(
                "hw.lcd.single.width=1080\n"
                "hw.lcd.single.height=2400\n"
                "hw.lcd.density=440\n",
                encoding="utf-8",
            )
            with patch.object(preflight_env, "hdc") as hdc:
                hdc.side_effect = ["render resolution: 1080x2400", ""]
                self.assertEqual(
                    ("1080x2400", "440"),
                    preflight_env._harmony_size_density("127.0.0.1:5557", str(config)),
                )

    def test_harmony_param_failure_errnum_never_becomes_density(self) -> None:
        """模拟真实镜像：param get 失败输出含 'errNum is:106!'，
        不得把 106 当密度（旧版 bug），应走 config.ini 得到 440。"""
        with tempfile.TemporaryDirectory(prefix="harmony-config-") as temp_name:
            config = Path(temp_name) / "config.ini"
            config.write_text(
                "hw.lcd.single.width=1080\n"
                "hw.lcd.single.height=2400\n"
                "hw.lcd.density=440\n",
                encoding="utf-8",
            )
            hidumper_out = (
                "--- ScreenInfo\n"
                "screen[0]: id=0, powerStatus=POWER_STATUS_ON, backlight=1, "
                "screenType=EXTERNAL_TYPE, render resolution=1080x2400, "
                "physical resolution=1080x2400, isVirtual=false"
            )
            with patch.object(preflight_env, "hdc") as hdc:
                hdc.side_effect = [
                    hidumper_out,
                    'Get parameter "const.product.density" fail! errNum is:106!\n',
                ]
                self.assertEqual(
                    ("1080x2400", "440"),
                    preflight_env._harmony_size_density("127.0.0.1:5557", str(config)),
                )

    def test_harmony_param_success_value_is_used(self) -> None:
        """真实成功响应（纯数字）应优先于 config.ini 被采信。"""
        with tempfile.TemporaryDirectory(prefix="harmony-config-") as temp_name:
            config = Path(temp_name) / "config.ini"
            config.write_text("hw.lcd.density=440\n", encoding="utf-8")
            with patch.object(preflight_env, "hdc") as hdc:
                hdc.side_effect = ["render resolution=1080x2400", "460\n"]
                self.assertEqual(
                    ("1080x2400", "460"),
                    preflight_env._harmony_size_density("127.0.0.1:5557", str(config)),
                )

    def test_non_ascii_path_is_detected_before_hvigor(self) -> None:
        self.assertFalse(preflight_env._is_ascii_path(Path("/tmp/测试/run")))
        self.assertTrue(preflight_env._is_ascii_path(Path("/tmp/migration/run")))
        with patch.object(sys, "argv", ["preflight_env.py", "--scope", "/tmp/测试/run/controller/scope.json"]), patch.object(preflight_env, "run") as run:
            self.assertEqual(1, preflight_env.main())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
