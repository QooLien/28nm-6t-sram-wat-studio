import csv
import tempfile
import unittest
from pathlib import Path

from sram_wat_analyzer import Config, DatasheetTargets, JudgmentTargets, ManualVmin, MosWat, SixTWatCell, ThreeTWatCell, Sram6T, TargetValidationSettings, WatPoint, WtZeroBitVminTest, analyze, analyze_six_mos, analyze_three_mos, evaluate_judgment, read_validation_csv, read_wat_csv, validate_config, validate_datasheet_wat_target, write_outputs


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.wat = WatPoint()
        self.cfg = Config(grid_points=101, vmin_step=.05, vmin_stop=1.0)

    def test_vtc_is_monotonic(self):
        curve = Sram6T(self.wat, self.cfg).vtc(.8, "read", 51)
        self.assertTrue(all(curve[i][1] >= curve[i+1][1] for i in range(len(curve)-1)))

    def test_snm_is_bounded(self):
        model = Sram6T(self.wat, self.cfg)
        for mode in ("hold", "read"):
            value = model.snm(.8, mode)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, .4)
        self.assertGreaterEqual(model.write_snm(.8), 0)
        self.assertLessEqual(model.write_snm(.8), .8)
        ratios = model.strength_ratios()
        self.assertGreater(ratios["cell_ratio_beta"], 0)
        self.assertGreater(ratios["pull_up_ratio_beta"], 0)

    def test_csv_and_report(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)/"wat.csv"
            source.write_text("corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids\nTT,.42,45,.40,80,.39,120\n", encoding="utf-8")
            self.assertEqual(read_wat_csv(source)[0].corner, "TT")
            report = write_outputs(analyze(self.wat, self.cfg), Path(td)/"out")
            self.assertTrue(report.exists())
            self.assertEqual(report.suffix, ".html")
            output_dir = report.parent
            image_dir = output_dir/"images"
            self.assertFalse((image_dir/"00_28nm_6t_bitcell_architecture.svg").exists())
            self.assertEqual(len(list(image_dir.glob("*.svg"))), 1)
            self.assertEqual(len(list(image_dir.glob("*.png"))), 1)
            self.assertTrue((image_dir/"01_6t_cell_snm_butterfly.png").exists())
            snm_svg = (image_dir/"01_6t_cell_snm_butterfly.svg").read_text(encoding="utf-8")
            self.assertIn("both butterfly lobes", snm_svg)
            self.assertIn("Write-noise proxy", snm_svg)
            self.assertFalse((output_dir/"pdf").exists())
            self.assertTrue((image_dir/"image_manifest.csv").exists())
            report_html = (output_dir/"sram_wat_report.html").read_text(encoding="utf-8")
            self.assertIn("HV28 SRAM Analysis", report_html)
            self.assertIn("Calibri", report_html)
            self.assertIn("WT sweep setup:", report_html)
            self.assertIn("Start=", report_html)
            self.assertIn("Cell Ratio", report_html)
            self.assertIn("Pull-up Ratio", report_html)
            self.assertIn("Hold SNM", report_html)
            self.assertIn("Write SNM", report_html)
            self.assertIn("6T Cell SNM Analysis", report_html)
            self.assertNotIn("sensitivity", report_html.lower())
            self.assertNotIn("28 nm 固定模型", report_html)
            self.assertNotIn("Generic 28 nm 6T SRAM bitcell", report_html)
            with open(output_dir/"sram_wat_results.csv", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), 1)
                self.assertIn("write_snm_mv", rows[0])
                self.assertIn("cell_ratio_beta", rows[0])

    def test_manual_wat_values(self):
        validate_config(self.cfg)
        manual = WatPoint(corner="LOT-A", pu_vt=.44, pu_ids=42, pg_vt=.41, pg_ids=77, pd_vt=.40, pd_ids=116)
        result = analyze(manual, self.cfg)
        self.assertEqual(result["wat"]["corner"], "LOT-A")
        self.assertEqual(result["groups"], {})
        self.assertIn("baseline_6t", result)

    def test_six_individual_mos_objects(self):
        cell = SixTWatCell("MISMATCH", MosWat(.38,45), MosWat(.40,42),
                           MosWat(.37,80), MosWat(.39,75),
                           MosWat(.36,120), MosWat(.38,110))
        targets = DatasheetTargets(MosWat(.39, 44), MosWat(.38, 78), MosWat(.37, 115))
        result = analyze_six_mos(cell, self.cfg, targets)
        self.assertEqual(len(result["cell"]["mos"]), 6)
        self.assertNotIn("mos_sensitivity", result)
        self.assertEqual(set(result["cell"]["mos"]), {"PUL", "PUR", "PGL", "PGR", "PDL", "PDR"})
        self.assertEqual(len(result["target_comparisons"]), 6)
        self.assertAlmostEqual(result["target_comparisons"][0]["delta_vt_mv"], -10.0)
        self.assertNotEqual(cell.side(1), cell.side(2))

    def test_wt_zero_bit_modes(self):
        cell = SixTWatCell("WT", MosWat(.38,45), MosWat(.38,45),
                           MosWat(.37,80), MosWat(.37,80),
                           MosWat(.36,120), MosWat(.36,120))
        results = WtZeroBitVminTest(cell, self.cfg).run()
        self.assertEqual([x["test"] for x in results], ["Scan4N","Select_Write","Select_Read"])
        self.assertTrue(all("vmin_v" in x for x in results))

    def test_three_t_merged_mode_and_manual_vmin(self):
        cell = ThreeTWatCell("3T", MosWat(.38,45), MosWat(.37,80), MosWat(.36,120))
        measured = ManualVmin(.51, .50, .44)
        targets = DatasheetTargets(MosWat(.40, 42), MosWat(.38, 82), MosWat(.35, 125))
        result = analyze_three_mos(cell, self.cfg, targets, measured,
                                   validation_settings=TargetValidationSettings())
        self.assertEqual(result["object_mode"], "3T Merged")
        self.assertEqual(len(result["cell"]["mos"]), 3)
        self.assertEqual(len(result["target_comparisons"]), 3)
        self.assertEqual(result["wt_test_0bit"][0]["vmin_v"], .51)
        self.assertEqual(result["vmin_source"], "manual")

        with tempfile.TemporaryDirectory() as td:
            report = write_outputs(result, td)
            self.assertTrue((report.parent/"wat_target_comparison.csv").exists())
            self.assertTrue((report.parent/"wat_target_validation_rows.csv").exists())
            self.assertTrue((report.parent/"wat_target_validation_summary.csv").exists())
            self.assertTrue((report.parent/"wat_target_parameter_evidence.csv").exists())
            html_report = report.read_text(encoding="utf-8")
            self.assertIn("Datasheet Target vs WAT Measured", html_report)
            self.assertIn("Datasheet WAT Target Validation", html_report)

    def test_parameter_judgment(self):
        metrics = {
            "cell_ratio_beta": 1.25, "pull_up_ratio_beta": 1.42,
            "hold_snm_mv": 285.0, "read_snm_mv": 210.0, "write_snm_mv": 80.0,
        }
        result = evaluate_judgment(metrics, JudgmentTargets())
        self.assertEqual(result["overall_status"], "FAIL")
        self.assertEqual([x["status"] for x in result["items"]],
                         ["PASS", "MARGINAL", "MARGINAL", "PASS", "FAIL"])

        cell = ThreeTWatCell("JUDGE", MosWat(.38,45), MosWat(.37,80), MosWat(.36,120))
        analyzed = analyze_three_mos(cell, self.cfg, judgment_targets=JudgmentTargets())
        self.assertIn(analyzed["judgment"]["overall_status"], {"PASS", "MARGINAL", "FAIL"})

    def test_target_validation_uses_paired_rows_and_does_not_overclaim(self):
        cell = ThreeTWatCell("CURRENT", MosWat(.38, 45), MosWat(.37, 80), MosWat(.36, 120))
        measured = ManualVmin(.61, .57, .59)
        targets = DatasheetTargets(MosWat(.38, 45), MosWat(.37, 80), MosWat(.36, 120))
        settings = TargetValidationSettings(minimum_statistical_n=10)
        single = validate_datasheet_wat_target(cell, measured, targets, settings)
        self.assertEqual(single["verdict"], "INSUFFICIENT DATA")
        self.assertEqual(single["current_row"]["consistency"], "TRUE ACCEPT")

        history = []
        for index in range(6):
            history.append({"lot_wafer": f"IN{index}", "pu_vt": .380 + index*.0002,
                            "pu_idsat": 45, "pg_vt": .370, "pg_idsat": 80,
                            "pd_vt": .360, "pd_idsat": 120, "scan4n_vmin": .60 + index*.002,
                            "select_write_vmin": .56 + index*.002, "select_read_vmin": .58 + index*.002})
            history.append({"lot_wafer": f"OUT{index}", "pu_vt": .410 + index*.001,
                            "pu_idsat": 35, "pg_vt": .400, "pg_idsat": 65,
                            "pd_vt": .390, "pd_idsat": 95, "scan4n_vmin": .68 + index*.002,
                            "select_write_vmin": .64 + index*.002, "select_read_vmin": .66 + index*.002})
        result = validate_datasheet_wat_target(cell, measured, targets, settings, history)
        self.assertEqual(result["verdict"], "SUPPORTED")
        self.assertGreater(result["statistics"]["pass_rate_lift_pct_points"], 50)

    def test_validation_csv_allows_missing_wat(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "history.csv"
            path.write_text("lot_wafer,pu_vt,pu_idsat,pg_vt,pg_idsat,pd_vt,pd_idsat,scan4n_vmin,select_write_vmin,select_read_vmin\nWT_ONLY,,,,,,,.61,.57,.59\n", encoding="utf-8")
            rows = read_validation_csv(path)
            self.assertIsNone(rows[0]["pu_vt"])
            self.assertEqual(rows[0]["scan4n_vmin"], .61)


if __name__ == "__main__":
    unittest.main()
