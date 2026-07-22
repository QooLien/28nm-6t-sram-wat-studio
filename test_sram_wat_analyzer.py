import csv
import tempfile
import unittest
from pathlib import Path

from sram_wat_analyzer import (
    Config, DatasheetTargets, MosWat, SixTWatCell, Sram6T, ThreeTWatCell,
    WatPoint, analyze, analyze_six_mos, analyze_three_mos, read_wat_csv,
    validate_config, write_outputs,
)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(grid_points=101)
        self.targets = DatasheetTargets(MosWat(.380, 45), MosWat(.370, 80), MosWat(.360, 120))
        self.cell = ThreeTWatCell("LOT_W01", MosWat(.385, 44), MosWat(.365, 82), MosWat(.355, 124))

    def test_hold_read_snm_are_bounded(self):
        model = Sram6T(self.cell.representative(), self.cfg)
        for mode in ("hold", "read"):
            value = model.snm(self.cfg.nominal_vdd, mode)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, self.cfg.nominal_vdd / 2)

    def test_metrics_exclude_write_and_vmin(self):
        metrics = analyze(self.cell.representative(), self.cfg)["baseline_6t"]["metrics"]
        self.assertIn("hold_snm_mv", metrics)
        self.assertIn("read_snm_mv", metrics)
        self.assertNotIn("write_snm_mv", metrics)
        self.assertNotIn("read_vmin_v", metrics)
        self.assertNotIn("write_vmin_v", metrics)

    def test_target_model_and_delta(self):
        result = analyze_three_mos(self.cell, self.cfg, self.targets)
        self.assertIn("target_6t", result)
        self.assertEqual([row["mode"] for row in result["snm_target_comparison"]],
                         ["Hold SNM", "Read SNM"])
        self.assertTrue(any(abs(row["delta_mv"]) > 0 for row in result["snm_target_comparison"]))

    def test_six_independent_objects_remain_supported(self):
        cell = SixTWatCell("MISMATCH", MosWat(.38, 45), MosWat(.40, 42),
                           MosWat(.37, 80), MosWat(.39, 75),
                           MosWat(.36, 120), MosWat(.38, 110))
        result = analyze_six_mos(cell, self.cfg, self.targets)
        self.assertEqual(set(result["cell"]["mos"]), {"PUL", "PUR", "PGL", "PGR", "PDL", "PDR"})
        self.assertEqual(len(result["target_comparisons"]), 6)
        self.assertEqual(len(result["snm_target_comparison"]), 2)

    def test_html_png_and_csv_are_focused_on_hold_read(self):
        result = analyze_three_mos(self.cell, self.cfg, self.targets)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "output"
            output.mkdir()
            (output / "wt_test_0bit_vmin.csv").write_text("old", encoding="utf-8")
            (output / "sram_wat_results.csv").write_text("old vmin schema", encoding="utf-8")
            report = write_outputs(result, output)
            image_dir = output / "images"
            self.assertTrue((image_dir / "01_hold_read_snm_target_comparison.png").exists())
            self.assertTrue((image_dir / "01_hold_read_snm_target_comparison.svg").exists())
            self.assertEqual(len(list(image_dir.glob("*.png"))), 1)
            self.assertEqual(len(list(image_dir.glob("*.svg"))), 1)
            svg = (image_dir / "01_hold_read_snm_target_comparison.svg").read_text(encoding="utf-8")
            self.assertIn("Current WAT VTC", svg)
            self.assertIn("Datasheet target VTC", svg)
            self.assertNotIn("SNM squares in both butterfly lobes", svg)
            html = report.read_text(encoding="utf-8")
            self.assertIn("Hold / Read SNM Target Comparison", html)
            self.assertNotIn("Write SNM", html)
            self.assertNotIn("WT Test 0-Bit Vmin", html)
            self.assertNotIn("Vmin", html)
            self.assertFalse((output / "wt_test_0bit_vmin.csv").exists())
            self.assertFalse((output / "sram_wat_results.csv").exists())
            self.assertTrue((output / "snm_target_comparison.csv").exists())
            with open(output / "snm_target_comparison.csv", encoding="utf-8-sig") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 2)

    def test_wat_target_deltas(self):
        result = analyze_three_mos(self.cell, self.cfg, self.targets)
        pu = result["target_comparisons"][0]
        self.assertAlmostEqual(pu["delta_vt_mv"], 5.0)
        self.assertAlmostEqual(pu["delta_isat_ua"], -1.0)

    def test_csv_input_and_config(self):
        validate_config(self.cfg)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "wat.csv"
            source.write_text(
                "corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids\nTT,.42,45,.40,80,.39,120\n",
                encoding="utf-8",
            )
            self.assertEqual(read_wat_csv(source)[0].corner, "TT")


if __name__ == "__main__":
    unittest.main()
