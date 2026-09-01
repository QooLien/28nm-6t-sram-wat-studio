# HV28 SRAM Analysis

Python-only generic 28 nm 6T SRAM WAT compact-model analysis. No SPICE or foundry model card is required.

The active workflow is focused on:

- Read SNM and W=1/W=0 Write SNM butterfly analysis
- Lot/Wafer WAT versus WAT Target VTC comparison
- Read butterfly maximum-square extraction
- Independent upper-left / lower-right Read-SNM mismatch analysis
- Independent analytical Read SNM cross-check
- PU / PG / PD Vt and Idsat comparison
- Manual grouped-PU/PG/PD RSNM-versus-VDD curve analysis and eye-closure estimation
- Write Trip Margin versus VDD trend analysis and estimated write boundary
- Interactive 6T Drive Monitor for PU/PG/PD Vt, Idsat, CR/PR and margin trends
- Lot/Wafer grouped Read, Write and balanced-drive distribution analysis

Hold SNM and Vmin comparison are not included in the active report.

## Run

On Windows, double-click `launchers/windows/open_sram_wat_analyzer.cmd`. The launcher checks Python, Tkinter, Excel and chart dependencies, then calls `launchers/windows/install_dependencies.cmd` when packages are missing.

For a company intranet/offline PC, follow [INTRANET_PC_SETUP.md](docs/INTRANET_PC_SETUP.md). Use `launchers/windows/prepare_offline_packages.cmd` on an internet-connected Windows PC with the same Python version, copy the generated `wheelhouse` with the project, then run `launchers/windows/install_dependencies.cmd` on the intranet PC.

## Check and synchronize the GitHub version

On an internet-connected Windows PC, double-click `launchers/windows/sync_project.cmd`. On macOS,
double-click `launchers/macos/sync_project.command`. The tool fetches `origin/main`, displays the
local and GitHub commit IDs, and reports whether the checkout is current. If an
update is available, it asks before applying a fast-forward update.

The synchronizer updates only when the current branch is `main`, tracked files
have no uncommitted changes, and Git can perform a fast-forward. It never deletes
or overwrites untracked local files. If the checkout has local commits, diverged
history, or tracked edits, it stops and requests manual review. The company
intranet/offline PC should continue to use a tested GitHub Release instead of
this network synchronizer.

Command-line alternatives:

```bash
python tools/sync_project.py --check
python tools/sync_project.py --sync
```

1. Enter the six independent PUL / PUR / PGL / PGR / PDL / PDR WAT Vt and Idsat values.
2. Enter the corresponding PU / PG / PD WAT Target values.
3. Set SRAM VDD and WAT calibration VDD.
4. Review or replace the four `6T Cell Geometry Reference` values: channel length and PU/PG/PD widths. A blank field automatically uses its generic default.
5. Select `Analyze & Open HTML`.

Use `Open Folder` beside `Report destination` to open the selected output directory. If the directory does not exist yet, the application creates it first.

The **RSNM vs VDD Curve** tab is an independent workflow:

1. Enter at least two VDD rows with grouped PU / PG / PD Vt and Idsat values.
2. Include points below and above the expected stability boundary if eye-closure VDD is required.
3. Select `Analyze RSNM vs VDD` to update the embedded chart and generate HTML, PNG, SVG, CSV and JSON results.

Each row's Idsat is treated as measured at that row's VDD. The program recalibrates the compact device strengths for every row. If an invalid-eye row and the next valid-eye row bracket the boundary, Vt and Idsat are linearly interpolated and a bisection search estimates the eye-closure VDD. This model boundary is not measured WT Vmin.

The **Write Trip Margin** tab reuses the same manual VDD / PU / PG / PD rows so Read and Write trends are based on identical WAT inputs. It calculates the maximum rise allowed on the nominally-low write bitline while PG can still overcome PU, then estimates the boundary between zero and positive write margin. The tab independently generates HTML, PNG, SVG, CSV and JSON results. This boundary is a compact-model reference and is not measured `Select_Write Vmin`.

The **6T Drive Monitor** tab provides immediate PU/PG/PD slider feedback. It shows effective beta, CR, PR, Read SNM and Write Margin directionally so WAT drive-balance changes can be explained without running a full report. Its statistical CR/PR Shmoo compares the current slider point with a deterministic 729-point nominal-reference population at the selected VDD: green requires both CR and PR to reach the population median, yellow requires both to reach Q1, and red means either ratio is below Q1. Changing VDD recalculates this reference; changing an individual PU/PG/PD slider moves only the current point so the target does not chase the sample. It remains a compact-model trend monitor rather than a PDK or silicon sign-off tool.

### Wafer multi-cell 6T analysis

Prepare an Excel workbook whose `6T Multi-Cell` sheet contains one measured 6T cell/chip per row. The import needs only Lot/Wafer, Chip ID, and PUL/PUR/PGL/PGR/PDL/PDR `Vt` and `Idsat` columns; use V for Vt and µA for Idsat, with no unit columns. Select **Import Multi-Cell...** to analyze all rows. The common Model VDD is taken from the active GUI setting, ensuring all VTC axes are comparable. The report overlays every cell's Read direct/mirrored VTC and Write W=1/W=0 VTC, highlights the cell with the lowest margin, and reports the minimum RSNM and minimum WSNM as conservative wafer references. Template-generation controls are intentionally not shown in the application.

The same import automatically builds a synthetic per-device median cell, identifies the minimum RSNM cell and minimum Write Margin cell separately, then runs a one-factor 10% step screening from each worst cell toward the median. The HTML report lists the shortest PU/PG/PD Vt or Idsat moves that reach the median Read or Write target; detailed data is exported as `median_target_read_shmoo.csv` and `median_target_write_shmoo.csv`. A directly importable example is retained as `input/multi_cell_example.xlsx`.

New Multi-Cell summaries also retain each cell's family-average PU/PG/PD Vt and Idsat. The fixed-VDD Multi-Cell report and **Estimate Vmin Curve** now create a wafer-relative drive-balance shmoo automatically. Visible charts call the internal effective-beta drive proxy **MOSdrive**: the X axis is `MOSdrive(PG)/MOSdrive(PU) = PR` (higher/right improves write ability), while Y is `MOSdrive(PD)/MOSdrive(PG) = CR` (higher/up improves read drive). The underlying CSV field names remain backward compatible. Each cell is ranked independently by its CR percentile and PR percentile within the same wafer/VDD population; the weaker drive-ratio percentile controls the grade. Green means both ratios are at or above P50, yellow means the weaker ratio is P25-P50, and red means it is below P25. This prevents a write-heavy/read-weak or read-heavy/write-weak cell from being labeled preferred solely because one axis is strong. RSNM and BL Write Trip Margin percentiles are retained separately to verify that the drive-ratio proxy agrees with modeled performance. The whole-wafer CR/PR median is shown separately from the best measured cell. Hovering a measured cell shows its PU/PG/PD Vt and Idsat, same-VDD median shifts, percentiles and grade. Per-cell results are exported in `multi_cell_wafer_relative_grades.csv` or `estimate_vmin_cr_pr_shmoo.csv`; P5/Q1/median/Q3/P95/MAD are exported in the matching distribution-statistics CSV. These are intra-wafer screening references, not absolute silicon Pass/Fail. Residual WSNM remains a separate write-eye diagnostic.

Each Estimate Vmin Shmoo is followed by an interactive **Drive-to-Preferred Advisor**. Select a Cell/Chip to compare its CR/PR percentiles with a same-VDD P55 guardband. The first-pass recommendation holds PG as the shared denominator, strengthens PD only when CR is short, and weakens PU only when PR is short. It also reports fixed-Vt equivalent Idsat values and exports all rows to `estimate_vmin_drive_to_preferred_advisor.csv`. This is a relative sensitivity screen; Vt and Idsat correlation plus final process feasibility still require Device/PDK and measured-WT review.

The dedicated **Lot/Wafer Advisor** includes a **Low / Monitor Batch Adjustment**. It sizes one common PD/PG/PU MOSdrive multiplier from the limiting Low/Monitor cells and reports coverage against the frozen pre-adjustment P55 target in `lot_wafer_batch_drive_advisor.csv`. Because a uniform shift preserves the population's rank order, recalculating the percentile target after the shift would leave relative grades unchanged; the batch result is sensitivity guidance, not a process recipe.

The dedicated **Lot/Wafer Advisor** tab accepts one or more generated `multi_chip_snm_summary.csv` files. Rows with an identical `lot_wafer` value are merged as one group at each Model VDD, even when they come from different selected files. It exports four complementary views: an all-Cell CR–PR Shmoo that grades Preferred / Monitor / Low from the weaker same-VDD CR/PR percentile, a CR–PR scatter using a stable color/marker and central-50% window for each Lot/Wafer, Tukey box plots for Read SNM, BL Write Trip Margin and `100 × min(CR percentile, PR percentile)` balanced drive score, and a labeled grouped bar chart showing the three grade counts for every Lot/Wafer. The report also compares each group with the same frozen all-population P55 CR/PR target and exports `lot_wafer_distribution_statistics.csv`, `lot_wafer_cell_drive_scores.csv` and `lot_wafer_batch_drive_advisor.csv`. Model VDDs are never pooled into the same distribution chart.

The Estimate Vmin import accepts generated `multi_chip_snm_summary.csv` files and raw 6T Multi-Cell Excel workbooks (`.xlsx` / `.xlsm`). For a one-step multi-voltage run, name the data worksheets by Model VDD, for example `0.90V`, `0.80V`, or `VDD_0.70V`; the sheet-name voltage overrides any copied Model VDD column. The program saves each sheet under `multi_cell_by_vdd/Model_VDD_x.xxxV/`, then combines every VDD population into the Read SNM, Write SNM and BL Write Margin Estimate Vmin curves under `estimate_vmin/`. **Run Shmoo analysis (slower)** is optional and defaults to off: fast mode still exports each VDD's input backup, per-cell summary and Estimate Vmin charts, but skips CR/PR Shmoo, Drive-to-Preferred Advisor and worst-to-median screening. Enable it only when those drive-balance diagnostics are required. A conventional single-VDD source produces a fast summary when unchecked or Shmoo-only output when checked. Multi-Cell run folders and imported Excel backup names include the analyzed VDD for easier identification.

To combine a completed Multi-VDD run, use **Import Multi-VDD Output Folder...** in the Estimate Vmin tab and select the run folder created by the multi-sheet Excel workflow, then choose **Create Comparison View**. The tool reads every `multi_cell_by_vdd/Model_VDD_*/multi_chip_snm_summary.csv` in that selected folder and combines the measured voltage points into one Read/Write SNM plus BL Write Margin Trip chart. One folder is sufficient; additional folders can be added for overlay comparison. **Import Combined Summaries...** remains available for legacy `multi_chip_snm_summary_combined.csv` exports. The report exports responsive HTML plus PNG, SVG and CSV files. The X axis automatically spans the lowest through highest imported Model VDD, so the curves do not retain an empty 0 V region. Only each curve source's lowest-VDD point is labelled; multi-source labels use `Lot/Wafer · VDD · margin`. Higher-VDD points remain visible without data boxes. Labels use centered text on a light background, and point-to-axis guide lines are omitted. Read/Write SNM share the upper panel, and the extrapolated eye-closure segment is intentionally omitted.

For a quick multi-voltage check, select `input/multi_vdd_summary_example.csv` with **Import Multi-Cell CSV / Excel...**. It contains ten Model VDD points from 0.40 V to 1.20 V in the same schema produced by Multi-Cell analysis. Each row represents one chip result at one VDD; additional chips can be appended with the same VDD and a unique Chip ID. The Estimate Vmin workflow conservatively uses the minimum RSNM, WSNM and Write Margin among all rows at each VDD.

## Read SNM chart convention

All active SNM charts use standard inverter VTC coordinates:

- X-axis: `Vin (V)`
- Y-axis: `Vout (V)`
- Scale: actual voltage from 0 V to SRAM VDD

The axes are not normalized ratios. At SRAM VDD = 0.90 V, the center tick is 0.45 V.

For a symmetric cell, the direct inverter VTC is plotted as `(Vin, Vout)` and the second curve is its inverse. For six independent MOS inputs, the two curves are built separately:

```text
direct:       right inverter, Vright = f_right(Vleft)
second curve: inverse left inverter, Vright = f_left^-1(Vleft)
```

This preserves PUL/PGL/PDL versus PUR/PGR/PDR mismatch instead of averaging the two sides before drawing the butterfly.

The generic Read condition defaults to `WL = BL = BLB = VDD`; the WL/VDD and BL/VDD ratios can be edited in the interface.

## W=1 / W=0 Write SNM Butterfly

The main 6T report evaluates both write polarities separately using the same actual-voltage coordinates as Read SNM:

```text
X-axis = Vin (V)
Y-axis = Vout (V)
```

```text
W=1 upper VTC: BLB = VDD, WL = VDD
W=0 lower VTC: BL = 0,   WL = VDD
```

The W=1 and W=0 curves are plotted together on one Vin/Vout graph. `WSNM` is the largest valid square whose lower-left and upper-right corners lie on `Vin = Vout`; the diagonal is used for fitting but is intentionally not drawn in the report.

```text
WSNM = maximum square side in the W=1/W=0 write window
```

This captures the write window set by PUL/PGL/PDL and PUR/PGR/PDR. It is a WAT-calibrated compact-model comparison, not measured WT sign-off.

## WAT-calibrated device model

For every PU, PG and PD device:

```text
VOV,WAT = max(WAT_VDD - |Vt|, 0.05)
beta_proxy = 2 * Idsat / VOV,WAT^2
```

The square-law device current uses the same WAT-calibrated beta proxy. For DC VTC solving, the hard threshold is replaced by a 25 mV softplus transition:

```text
VOV,eff = 0.035 · ln(1 + exp((VGS - |Vt|) / 0.035))
```

This keeps the VTC continuous as VDD approaches Vt and reduces artificial right-angle corners in low-VDD RSNM plots. It is a numerical smoothing approximation, not a foundry subthreshold/BSIM model.

## Geometric Read SNM

The tool numerically constructs the two cross-coupled Read VTC curves, separates the butterfly eyes, and fits the largest axis-aligned square in each eye. The upper-left and lower-right squares correspond to opposite stored states.

```text
RSNM_upper = upper-left square side
RSNM_lower = lower-right square side
Cell RSNM  = min(RSNM_upper, RSNM_lower)
State delta = RSNM_upper - RSNM_lower
Mismatch index = |State delta| / mean(RSNM_upper, RSNM_lower) * 100%
```

An ideal symmetric cell produces nearly equal state margins. A left/right device mismatch can enlarge one eye and shrink the other; the smaller value remains the conservative cell margin. Swapping all left and right MOS inputs swaps the two state margins while preserving Cell RSNM. The butterfly SVG uses equal X/Y pixel scale so a voltage square is displayed as a true square.

## Analytical Read SNM

The independent analytical cross-check uses:

```text
q = beta_PU / beta_PG
r = beta_PD / beta_PG
VTH,eff = mean(|Vt_PU|, Vt_PG, Vt_PD)
```

It is reported separately from the geometric VTC result. If its real-valued mathematical domain is not satisfied, the result is shown as N/A.

## 6T cell geometry reference

Measured WAT values always take priority. The interface keeps only these known geometry fields; leaving one blank restores its generic default:

- Channel length L: 28 nm
- PU width: 70 nm
- PG width: 100 nm
- PD width: 140 nm

The report also derives `Geometry Cell Ratio = WPD/WPG` and `Geometry Pull-up Ratio = WPG/WPU`. These are architecture references beside the electrical beta ratios. They do not override beta calibrated from WAT Vt and Idsat, because multiplying W/L into measured Idsat again would double-count device strength.

## Outputs

Every GUI and command-line analysis creates a separate archive directory so an older result is never overwritten:

```text
output/YYYY-MM-DD/WaferID/HHMMSS_analysis-name/
```

If two runs start during the same second, `_02`, `_03`, and so on are appended automatically. Windows-invalid characters in the Lot/Wafer ID are replaced with underscores. Each run also includes `run_info.json` with the original Wafer ID, local creation time, analysis type and absolute output directory.

- `sram_wat_report.html`: main interactive report
- `snm_target_comparison.csv`: Read SNM, Lot/Wafer versus WAT Target
- `w0_w1_wsnm_analysis.csv`: W0/W1 write VTC data and state-specific WSNM
- `read_snm_state_mismatch.csv`: upper-left/lower-right state margins, conservative Cell RSNM, signed delta and mismatch index
- `wat_electrical_snm_table.csv`: WAT inputs, derived ratios, Read SNM and analytical RSNM
- `analytical_read_snm.csv`: analytical Read SNM parameters and result
- `cell_geometry_reference.csv`: L/W geometry values and derived ratio references
- `wat_target_comparison.csv`: PU / PG / PD Vt and Idsat deltas
- `sram_wat_results.json`: detailed model data
- `images/01_read_snm_target_comparison.png`: Read VTC target comparison
- `images/01_read_snm_target_comparison.svg`: scalable chart source
- `images/02_read_snm_butterfly.png`: Read butterfly maximum-square chart
- `images/02_read_snm_butterfly.svg`: scalable butterfly source
- `images/03_w0_w1_wsnm_analysis.png`: W0/W1 write-SNM panels and state-specific squares
- `images/03_write_butterfly_curve.svg`: scalable chart source
- `images/image_manifest.csv`: image manifest

The manual curve tab uses the same dated archive structure with the `rsnm_vdd_curve` analysis name:

- `rsnm_vcc_report.html`: dedicated RSNM-versus-VDD report (legacy-compatible filename)
- `rsnm_vcc_curve.csv`: manual inputs, calculated RSNM and valid-eye status
- `rsnm_vcc_curve.json`: complete curve analysis and eye-closure bracket
- `images/01_rsnm_vs_model_vcc.png`: downloadable VDD chart (legacy-compatible filename)
- `images/01_rsnm_vs_model_vcc.svg`: scalable VDD chart source (legacy-compatible filename)

## Formula guide

The WAT Vt/Idsat conversion, Read SNM, analytical RSNM, and Write Margin Test equations are explained in Traditional Chinese in the [HV28 SRAM Analysis Formula Guide (PDF)](https://github.com/QooLien/28nm-6t-sram-wat-studio/releases/download/v1.5.0/HV28_SRAM_Analysis_Formula_Guide.pdf). The reproducible source is `tools/generate_formula_guide_zh.py`.

## Presentation decks

- `output/reference/presentations/HV28_SRAM_Core_Formulas_Chinese_v5.pptx`: Traditional Chinese explanation with curve-matched Read SNM and Write SNM figures.
- `output/reference/presentations/HV28_SRAM_Core_Formulas_English_v5.pptx`: English version of the same presentation.

The W0/W1 Write-SNM figure uses blue/purple curves for Lot/Wafer VTC pairs, green maximum-square markers, and orange/red curves for the optional WAT Target reference.

## WAT CSV

```csv
corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids
TT,0.385,44,0.365,82,0.355,124
```

## 6T WAT Excel model-VDD sweep

The GUI can import an `.xlsx` workbook through **Import Excel…** and generate measured-WAT versus WAT-target Read-SNM butterfly panels, an SNM-versus-model-VDD chart, and an all-operating-voltage Vin/Vout overlay. The overlay uses color for operating VDD, solid lines for measured WAT, dashed lines for target, and 0.2 V axis increments. The command line also accepts an `.xlsx` path through `--input`.

For a Model-VDD sweep, use separate `PU`, `PG` and `PD` worksheets with these columns: Lot/Wafer, Site, Model VDD, MOS, Vt, Vt Unit, Idsat, Idsat Unit and Notes. Add one row for every physical MOS/site/VDD measurement. Plain worksheet ranges are recommended; Excel Table objects are not required.

### Single-set 6T WAT Excel

For one Lot/Wafer and one operating VDD, use a **6T WAT Input** worksheet containing one row for each physical MOS: PUL, PUR, PGL, PGR, PDL and PDR. Include explicit Vt and Idsat unit columns; the loader normalizes values to V and µA.

In **6T Bitcell Analysis**:

- **Import Excel...** copies the first complete six-MOS set into the six GUI input panels.
- **Save Current...** writes the currently displayed manual values to the same compatible workbook format.
- Excel is optional; all six Vt/Idsat pairs can still be typed manually.

For repeated site rows, the loader groups data by Lot/Wafer + Model VDD + physical MOS. The arithmetic mean of valid Vt and Idsat measurements is used for each of the six independent MOS inputs; the left and right sides are not merged. Actual row count, median, sample standard deviation, minimum and maximum are exported to `excel_wat_site_statistics.csv`. The sweep result also exports both state margins and the mismatch index. A Model VDD at or below the highest imported MOS Vt remains in the WAT statistics but is reported as SNM N/A.

Use either of these worksheet formats. Blank unit cells default to V for Vt / model VDD and uA for Idsat. Supported voltage units are V, mV, uV and nV; supported current units are A, mA, uA, nA and pA. A 0 V model-VDD row is retained as input data but omitted from SNM plotting because SNM is undefined at zero supply.

Long form - one row per physical MOS at each model VDD:

```text
Lot/Wafer | Model VDD | VDD Unit | MOS | Vt | Vt Unit | Idsat | Idsat Unit
W01       | 900       | mV       | PUL | 380| mV      | 0.045 | mA
```

Wide form - one row per model VDD, with six physical MOS columns:

```text
Lot/Wafer | Model VDD (V) | PUL Vt (mV) | PUL Idsat (uA) | ... | PDR Vt (mV) | PDR Idsat (uA)
W01       | 0.900         | 380         | 45             | ... | 356         | 125
```

Accepted MOS names are PUL/PUR/PGL/PGR/PDL/PDR, PU1/PU2/PG1/PG2/PD1/PD2, or the conventional M2/M4/M5/M6/M1/M3 names. When sheets named PU, PG and PD are present, the loader combines all three automatically. The application saves manual 6T inputs, manual RSNM/VDD rows, targets, model settings, assumptions, output folder and last selected Excel path in `.hv28_sram_analysis_state.json` when it closes; this local state file is not committed to Git.

The **WAT Target → Use as reference** checkbox controls whether Target values participate in analysis. When disabled, the entered Target values remain saved, but charts, HTML tables and CSV outputs contain Lot/Wafer results only; no duplicated Target curve or zero-delta comparison is generated.

## Engineering limitation

This is a generic WAT-calibrated 28 nm compact model. It is intended for Lot/Wafer-versus-Target correlation, trend analysis and engineering screening. It is not a replacement for foundry PDK simulation or silicon sign-off.
