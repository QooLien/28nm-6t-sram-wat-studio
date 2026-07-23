import csv
import tempfile
import unittest
from pathlib import Path

from sram_wat_analyzer import (
    Config, DatasheetTargets, MosWat, SixTWatCell, Sram6T, ThreeTWatCell,
    WatPoint, analyze, analyze_six_mos, analyze_three_mos,
    generic_28nm_assumption_rows, read_wat_csv,
    validate_config, wat_electrical_snm_rows, write_outputs,
)


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(grid_points=101)
        self.targets = DatasheetTargets(MosWat(.380, 45), MosWat(.370, 80), MosWat(.360, 120))
        self.cell = ThreeTWatCell("LOT_W01", MosWat(.385, 44), MosWat(.365, 82), MosWat(.355, 124))

    def test_hold_read_snm_are_bounded(self):
        model = Sram6T(self.cell.representative(), self.cfg)
        for mode in ("hold", "read"):
            result = model.butterfly_squares(self.cfg.nominal_vdd, mode, points=601)
            self.assertTrue(result["valid"])
            self.assertEqual(len(result["squares"]), 2)
            value = result["snm_v"]
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, self.cfg.nominal_vdd / 2)

    def test_figure_3_15_geometric_read_snm(self):
        wat = WatPoint("CURRENT", .35, 29.2, .27, 40.3, .27, 47.6)
        result = Sram6T(wat, self.cfg).butterfly_squares(.9, "read", points=1201)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["snm_mv"], 156.0, delta=1.0)
        self.assertAlmostEqual(result["squares"][0]["side_mv"],
                               result["squares"][1]["side_mv"], delta=1.0)

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

    def test_pdf_equation_3_36_with_given_wat_values(self):
        current = WatPoint("CURRENT", .35, 29.2, .27, 40.3, .27, 47.6)
        target = WatPoint("TARGET", .33, 19.5, .28, 39.0, .29, 44.9)
        current_eq = Sram6T(current, self.cfg).analytical_read_snm_eq_3_36(.9)
        target_eq = Sram6T(target, self.cfg).analytical_read_snm_eq_3_36(.9)
        self.assertTrue(current_eq["valid"])
        self.assertTrue(target_eq["valid"])
        self.assertAlmostEqual(current_eq["vth_eff_v"], (.35 + .27 + .27) / 3)
        self.assertAlmostEqual(current_eq["snm_mv"], 189.7967615, places=5)
        self.assertAlmostEqual(target_eq["snm_mv"], 183.5971209, places=5)

    def test_pdf_equation_3_36_reports_domain_failure(self):
        equation = Sram6T(WatPoint(), self.cfg).analytical_read_snm_eq_3_36(.9)
        self.assertFalse(equation["valid"])
        self.assertIsNone(equation["snm_mv"])
        self.assertIn("square-root domain", equation["reason"])

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
            self.assertTrue((image_dir / "02_read_snm_fig_3_15_style.png").exists())
            self.assertTrue((image_dir / "02_read_snm_fig_3_15_style.svg").exists())
            self.assertEqual(len(list(image_dir.glob("*.png"))), 2)
            self.assertEqual(len(list(image_dir.glob("*.svg"))), 2)
            svg = (image_dir / "01_hold_read_snm_target_comparison.svg").read_text(encoding="utf-8")
            self.assertIn("Current WAT VTC", svg)
            self.assertIn("Datasheet target VTC", svg)
            self.assertNotIn("SNM squares in both butterfly lobes", svg)
            fig315_svg = (image_dir / "02_read_snm_fig_3_15_style.svg").read_text(encoding="utf-8")
            self.assertIn("Maximum squares 1 and 2", fig315_svg)
            self.assertIn("smaller side of squares 1 and 2", fig315_svg)
            html = report.read_text(encoding="utf-8")
            self.assertIn("Hold / Read SNM Target Comparison", html)
            self.assertNotIn("Write SNM", html)
            self.assertNotIn("WT Test 0-Bit Vmin", html)
            self.assertNotIn("Vmin", html)
            self.assertFalse((output / "wt_test_0bit_vmin.csv").exists())
            self.assertFalse((output / "sram_wat_results.csv").exists())
            self.assertTrue((output / "snm_target_comparison.csv").exists())
            self.assertTrue((output / "analytical_read_snm_eq_3_36.csv").exists())
            self.assertTrue((output / "wat_electrical_snm_table.csv").exists())
            self.assertTrue((output / "generic_28nm_assumptions.csv").exists())
            self.assertIn("PDF Equation 3.36 Analytical Read SNM", html)
            self.assertIn("WAT Electrical Parameters", html)
            self.assertIn("No W/L, Cox, mobility", html)
            self.assertIn("Generic 28 nm Default Assumptions", html)
            self.assertIn("VTH,eff", html)
            with open(output / "snm_target_comparison.csv", encoding="utf-8-sig") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 2)

    def test_wat_electrical_snm_table_uses_measured_inputs(self):
        result = analyze_three_mos(self.cell, self.cfg, self.targets)
        rows = wat_electrical_snm_rows(result)
        self.assertEqual([row["dataset"] for row in rows],
                         ["Current WAT", "Datasheet Target"])
        current = rows[0]
        self.assertAlmostEqual(current["pu_vt_v"], .385)
        self.assertAlmostEqual(current["pg_idsat_ua"], 82)
        self.assertAlmostEqual(current["idsat_pd_over_pg"], 124 / 82)
        self.assertGreater(current["q_beta_pu_over_pg"], 0)
        self.assertGreater(current["r_beta_pd_over_pg"], 0)
        self.assertIn("hold_snm_geometric_mv", current)
        self.assertIn("read_snm_geometric_mv", current)
        self.assertEqual(
            current["evidence_scope"],
            "WAT Vt + Idsat; no PDK/model-card-only parameters",
        )

    def test_generic_defaults_are_explicit_and_do_not_override_wat(self):
        result = analyze_three_mos(self.cell, self.cfg, self.targets)
        rows = {row["parameter"]: row for row in generic_28nm_assumption_rows(result)}
        self.assertEqual(rows["Technology node"]["value"], 28)
        self.assertEqual(rows["Channel length L"]["value"], 28.0)
        self.assertEqual(rows["Channel length L"]["active"], "NO")
        self.assertEqual(rows["Beta"]["active"], "YES")
        self.assertIn("WAT Vt and Idsat", rows["Beta"]["source"])
        self.assertEqual(rows["Cox / mobility / tox / lambda"]["value"], "Not required")

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
