import csv
import tempfile
import unittest
from pathlib import Path

from sram_wat_analyzer import Config, ManualVmin, MosWat, SixTWatCell, ThreeTWatCell, Sram6T, WatPoint, WtZeroBitVminTest, analyze, analyze_six_mos, analyze_three_mos, read_wat_csv, validate_config, write_outputs


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

    def test_csv_and_report(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)/"wat.csv"
            source.write_text("corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids\nTT,.42,45,.40,80,.39,120\n", encoding="utf-8")
            self.assertEqual(read_wat_csv(source)[0].corner, "TT")
            report = write_outputs(analyze(self.wat, self.cfg), Path(td)/"out")
            self.assertTrue(report.exists())
            image_dir = report.parent/"images"
            self.assertTrue((image_dir/"00_28nm_6t_bitcell_architecture.svg").exists())
            self.assertEqual(len(list(image_dir.glob("*.svg"))), 13)
            self.assertTrue((image_dir/"image_manifest.csv").exists())
            with open(report.parent/"sram_wat_results.csv", encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 15)

    def test_manual_wat_values(self):
        validate_config(self.cfg)
        manual = WatPoint(corner="LOT-A", pu_vt=.44, pu_ids=42, pg_vt=.41, pg_ids=77, pd_vt=.40, pd_ids=116)
        result = analyze(manual, self.cfg)
        self.assertEqual(result["wat"]["corner"], "LOT-A")
        self.assertEqual(set(result["groups"]), {"PU", "PG", "PD"})

    def test_six_individual_mos_objects(self):
        cell = SixTWatCell("MISMATCH", MosWat(.38,45), MosWat(.40,42),
                           MosWat(.37,80), MosWat(.39,75),
                           MosWat(.36,120), MosWat(.38,110))
        result = analyze_six_mos(cell, self.cfg)
        self.assertEqual(len(result["cell"]["mos"]), 6)
        self.assertEqual(len(result["mos_sensitivity"]), 6)
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
        result = analyze_three_mos(cell, self.cfg, "PD", measured)
        self.assertEqual(result["object_mode"], "3T Merged")
        self.assertEqual(result["analysis_target"], "PD")
        self.assertEqual(len(result["cell"]["mos"]), 3)
        self.assertEqual(result["wt_test_0bit"][0]["vmin_v"], .51)
        self.assertEqual(result["vmin_source"], "manual")


if __name__ == "__main__":
    unittest.main()
