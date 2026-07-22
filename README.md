# HV28 SRAM Analysis

Python-only、物件導向的 generic 28 nm 6T SRAM WAT compact-model 工具，不使用 SPICE。

目前版本只分析：

- Hold SNM
- Read SNM
- Current WAT 與 Datasheet PU／PG／PD Target 的 VTC曲線及SNM差距

Write SNM、WT Vmin、Vmin sweep、歷史 WT correlation、Target band validation 與 SNM square 圖形均已取消。

## 操作方式

Windows 直接雙擊 `open_sram_wat_analyzer.cmd`。

1. 選擇 `3T Merged` 或 `6T Independent`。
2. 在 SRAM 結構圖旁輸入目前 WAT 的 Vt 與 Isat／Ids。
3. 在右側輸入 Datasheet PU／PG／PD Target Vt 與 Isat。
4. 設定 SRAM VDD 與 WAT VDD。
5. 按下 `Analyze & Open HTML`。

Target 會建立第二組完整 6T compact model，與目前 WAT 模型使用相同 VDD條件比較，不會覆蓋目前 WAT輸入。

## 圖表定義

- 藍色實線：Current WAT VTC
- 藍色虛線：Current WAT mirrored VTC
- 橘色實線：Datasheet Target VTC
- 橘色虛線：Datasheet Target mirrored VTC

SNM數值仍由兩個 butterfly lobes 的 maximum-square 數值演算法取得，但圖中不再繪製SNM square，讓 Current 與 Target 曲線位移更容易比較。

報表直接列出：

- Current SNM
- Target SNM
- Current − Target 差值（mV）
- 百分比差異

## 輸出

- `sram_wat_report.html`：主要報表，完成後自動開啟
- `snm_target_comparison.csv`：Hold／Read SNM Current vs Target 數值
- `wat_target_comparison.csv`：PU／PG／PD Vt、Isat差值
- `sram_wat_results.json`：完整 VTC 與分析結果
- `images/01_hold_read_snm_target_comparison.png`：高解析度 PNG
- `images/01_hold_read_snm_target_comparison.svg`：可縮放 SVG
- `images/image_manifest.csv`：圖片用途說明

## WAT CSV

```csv
corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids
TT,0.385,44,0.365,82,0.355,124
```

## 工程限制

本工具使用 generic 28 nm 6T compact model，適合 WAT方向比較與前期分析，不是 foundry sign-off simulator。實際量產判定仍需相同偏壓條件的 WAT、foundry PDK／BSIM、實際 W/L、溫度、PEX、array loading 與 mismatch資料。
