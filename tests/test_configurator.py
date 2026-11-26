"""Run with python -m unittest discover -s tests (requires Tk and pyserial)."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "configurator"))
from pedal_configurator import PedalSerial, map_force, noise_readout, read_config, write_settings


class ConfiguratorTests(unittest.TestCase):
    def test_malformed_config_is_reported(self):
        link = PedalSerial()
        for invalid in ([], dict(link._demo_cfg, filter_type="five"), dict(link._demo_cfg, adc_ok=2)):
            with self.subTest(invalid=invalid), patch.object(link, "send_command",
                    return_value="RSP:CONFIG:" + json.dumps(invalid)), self.assertRaises(ValueError):
                read_config(link)

    def test_telemetry_without_raw(self):
        link = PedalSerial()
        link._parse_data_line("DAT:210,45")
        link._parse_data_line("DAT:220,46,230")
        self.assertEqual([s[1:] for s in link.get_data()], [(210, 45, None), (220, 46, 230)])
        self.assertEqual(noise_readout([(None, n) for n in range(40)], 500000), ("—", "39", "Нет канала"))

    def test_noise_at_hold_movement_and_flat_input(self):
        raw = [1000 + (-1)**i * 50 for i in range(40)]
        self.assertEqual(noise_readout(list(zip(raw, raw)), 500000)[2], "0.0%")
        self.assertEqual(noise_readout(list(zip(raw, [1000]*40)), 500000)[2], "100.0%")
        self.assertEqual(noise_readout([(10, 10)]*40, 500000)[2], "Нет разброса")
        self.assertEqual(noise_readout([(1000*i, 1000*i-5000) for i in range(40)], 500000)[2], "Движение")

    def test_demo_custom_curve_save_and_reload(self):
        link = PedalSerial()
        link.demo_mode = True
        base = read_config(link)
        draft = dict(curve_type=3, curve_points=(5, 30, 90), deadzone=0, inverted=0,
                     filter_size=1, filter_type=0, spike_filter=0)
        cfg = write_settings(link, base, draft, save=True)
        self.assertEqual(map_force(250, 0, 500, 0, 3, False, cfg["curve_points"]), 19661)
        self.assertEqual(link.send_command("CMD:SET_CURVE:3,20,5,40"), "RSP:ERR:CURVE_POINTS")
        self.assertEqual(read_config(link), cfg)
        inactive = dict(draft, curve_type=1, curve_points=(10, 20, 90))
        edited = write_settings(link, cfg, inactive, save=False)
        self.assertEqual((edited["curve_type"], edited["curve_points"]), (1, (10, 20, 90)))
        link.send_command("CMD:LOAD")
        self.assertEqual(read_config(link), cfg)

    def test_failed_ack_prevents_flash_write(self):
        link = PedalSerial()
        link.demo_mode = True
        cfg = read_config(link)
        draft = {k: cfg[k] for k in ("deadzone", "filter_type", "spike_filter", "filter_size",
                                     "inverted", "curve_type", "curve_points")}
        draft["filter_size"] = 32
        with patch.object(link, "send_command", return_value="RSP:ERR:UNKNOWN_CMD") as send:
            with self.assertRaises(ValueError):
                write_settings(link, cfg, draft, save=True)
        send.assert_called_once_with("CMD:SET_FILTER:32")

    def test_legacy_firmware_keeps_presets(self):
        link = PedalSerial()
        link.demo_mode = True
        link._demo_cfg.pop("curve_points")
        current = read_config(link)
        draft = {k: current[k] for k in ("deadzone", "filter_type", "spike_filter", "filter_size", "inverted")}
        draft.update(curve_type=2, curve_points=(25, 50, 75))
        self.assertEqual(write_settings(link, current, draft, False)["curve_type"], 2)
        draft["curve_type"] = 3
        with self.assertRaises(ValueError):
            write_settings(link, current, draft, False)


if __name__ == "__main__":
    unittest.main()
