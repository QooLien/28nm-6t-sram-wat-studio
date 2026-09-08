# 64-Chip Wafer Vmin P90 Tool

This folder is the canonical package for the single-chip BLK Vmin P90 workbook and the fixed 64-chip wafer map.

## Fixed wafer coordinates

- Y10: X10-X13 (4 chips)
- Y11: X8-X15 (8 chips)
- Y12-Y15: X7-X16 (10 chips per row)
- Y16: X8-X15 (8 chips)
- Y17: X10-X13 (4 chips)
- Total: 64 chips; the top-left chip is X10Y10.

The complete coordinate list is stored in `data/wafer_map_coordinates.csv`.

## Workbook

Open `SRAM_BLK_Vmin_P90_Tool.xlsm` and enable macros.

1. Enter each chip's BLK counts in `Vmin Input`; the 64 coordinate names are prefilled.
2. Each populated row must sum to 256 BLKs.
3. Select a chip row and click **Calculate P90 / Update Chart**.
4. `P90 Chart` shows the selected chip's cumulative Vmin distribution.
5. `Wafer Map` places each P90 Vmin at its X/Y coordinate and applies a Vmin color band.

The input bin order is descending from `Hard Fail` through `≤0.56 V`. The cumulative chart remains low-to-high, and lower P90 Vmin represents better low-voltage operation.

## Folder contents

- `SRAM_BLK_Vmin_P90_Tool.xlsm`: ready-to-use macro-enabled workbook.
- `data/wafer_map_coordinates.csv`: fixed 64-chip coordinate reference.
- `preview/wafer_map_preview.png`: wafer map layout preview.
- `source/`: VBA and reproducible workbook build/test scripts.
- `build/`: local generated verification outputs; contents are not required for normal workbook use.

## Rebuild

Run `source/build.mjs` from the repository root, then run `source/finalize_xlsm.ps1`. Use `source/test_xlsm.ps1` for P90 boundary and coordinate-map verification.
