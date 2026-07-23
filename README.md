# HV28 SRAM Analysis

Python-only generic 28 nm 6T SRAM WAT compact-model analysis. No SPICE or foundry model card is required.

The active workflow is focused on:

- Read SNM and write-condition SNM proxy
- Lot/Wafer WAT versus WAT Target VTC comparison
- Read butterfly maximum-square extraction
- Independent analytical Read SNM cross-check
- PU / PG / PD Vt and Idsat comparison

Hold SNM and Vmin comparison are not included in the active report.

## Run

On Windows, double-click `open_sram_wat_analyzer.cmd`.

1. Enter the six independent PUL / PUR / PGL / PGR / PDL / PDR WAT Vt and Idsat values.
2. Enter the corresponding PU / PG / PD WAT Target values.
3. Set SRAM VDD and WAT calibration VDD.
4. Review or replace the editable `Generic 28 nm Default Assumptions` values. A blank field automatically uses its generic default.
5. Select `Analyze & Open HTML`.

## Read SNM chart convention

All active SNM charts use standard inverter VTC coordinates:

- X-axis: `Vin (V)`
- Y-axis: `Vout (V)`
- Scale: actual voltage from 0 V to SRAM VDD

The axes are not normalized ratios. At SRAM VDD = 0.90 V, the center tick is 0.45 V.

The direct inverter VTC is plotted as `(Vin, Vout)`. The mirrored VTC exchanges the coordinates:

```text
(Vin, Vout) -> (Vout, Vin)
```

The generic Read condition defaults to `WL = BL = BLB = VDD`; the WL/VDD and BL/VDD ratios can be edited in the interface.

## Write SNM proxy

Write condition defaults to `WL = VDD`, `BL = 0 V`, and `BLB = VDD`. The write VTC pair is intentionally asymmetric: the direct curve uses the low write bitline and the mirrored curve uses the high complementary bitline.

The reported Write SNM is a bitline-noise-margin proxy: the maximum rise allowed on the nominally-low write bitline while PG can still overcome PU at the hold inverter trip point. It supports WAT Target comparison and trend analysis; it is not a geometric butterfly-square metric or foundry sign-off WSNM.

## WAT-calibrated device model

For every PU, PG and PD device:

```text
VOV,WAT = max(WAT_VDD - |Vt|, 0.05)
beta_proxy = 2 * Idsat / VOV,WAT^2
```

The square-law device current is then evaluated with the WAT-calibrated beta proxy. This avoids guessing Cox, mobility, oxide thickness or channel-length modulation.

## Geometric Read SNM

The tool numerically constructs the direct and mirrored Read VTC curves, separates the two butterfly lobes, and fits the largest axis-aligned square in each lobe.

```text
Geometric RSNM = min(square 1 side, square 2 side)
```

The butterfly SVG uses equal X/Y pixel scale so a voltage square is displayed as a true square.

## Analytical Read SNM

The independent analytical cross-check uses:

```text
q = beta_PU / beta_PG
r = beta_PD / beta_PG
VTH,eff = mean(|Vt_PU|, Vt_PG, Vt_PD)
```

It is reported separately from the geometric VTC result. If its real-valued mathematical domain is not satisfied, the result is shown as N/A.

## Generic 28 nm editable defaults

Measured WAT values always take priority. These interface fields may be replaced when verified process information becomes available; leaving one blank restores its generic default:

- SRAM VDD: 0.90 V
- WAT calibration VDD: 0.90 V
- Reference temperature: 25 °C
- Reference channel length: 28 nm
- Reference WPU / WPG / WPD: 70 / 100 / 140 nm
- Read WL and precharged bitlines: VDD
- Write WL / BL / BLB: VDD / 0 V / VDD

Geometry and temperature are documented reference values. They do not override beta calibrated from WAT Vt and Idsat.

## Outputs

- `sram_wat_report.html`: main interactive report
- `snm_target_comparison.csv`: Read SNM and Write SNM proxy, Lot/Wafer versus WAT Target
- `wat_electrical_snm_table.csv`: WAT inputs, derived ratios, Read SNM and Write SNM proxy
- `analytical_read_snm.csv`: analytical Read SNM parameters and result
- `generic_28nm_assumptions.csv`: default parameter policy and active status
- `wat_target_comparison.csv`: PU / PG / PD Vt and Idsat deltas
- `sram_wat_results.json`: detailed model data
- `images/01_read_snm_target_comparison.png`: Read VTC target comparison
- `images/01_read_snm_target_comparison.svg`: scalable chart source
- `images/02_read_snm_butterfly.png`: Read butterfly maximum-square chart
- `images/02_read_snm_butterfly.svg`: scalable butterfly source
- `images/03_write_snm_target_comparison.png`: write-condition VTC and WSNM proxy comparison
- `images/03_write_snm_target_comparison.svg`: scalable chart source
- `images/image_manifest.csv`: image manifest

## WAT CSV

```csv
corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids
TT,0.385,44,0.365,82,0.355,124
```

## Engineering limitation

This is a generic WAT-calibrated 28 nm compact model. It is intended for Lot/Wafer-versus-Target correlation, trend analysis and engineering screening. It is not a replacement for foundry PDK simulation or silicon sign-off.
