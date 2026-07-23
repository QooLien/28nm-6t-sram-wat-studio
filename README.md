# HV28 SRAM Analysis

Python-only、物件導向的 generic 28 nm 6T SRAM WAT compact-model 工具，不使用 SPICE。

目前版本只分析：

- Hold SNM
- Read SNM
- Current WAT 與 Datasheet PU／PG／PD Target 的 VTC曲線及SNM差距

Write SNM、WT Vmin、Vmin sweep、歷史 WT correlation 與 Target band validation 均已取消。Read SNM 另輸出 Figure 3.15(b) 形式的雙 lobe 最大方形圖。

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

第一張圖不繪製 SNM square，讓 Current 與 Target 曲線位移容易比較。第二張圖依 PDF Figure 3.15(b) 分別顯示 Current／Target 的 read butterfly、Square 1、Square 2，並以較小方形邊長作為 geometric Read SNM。

## 現行程式計算公式

### 1. 由 WAT 校準 MOS β

輸入的 `Vt`、`Idsat` 與 WAT 測試電壓用來校準 square-law MOS：

$$
V_{OV,WAT}=\max(V_{WAT}-|V_T|,\ 0.05)
$$

$$
\beta=\frac{2I_{DSAT}}{V_{OV,WAT}^{2}}
$$

截止區：

$$
V_{GS}-V_T\leq0 \Rightarrow I_D=0
$$

線性區：

$$
I_D=\beta\left[(V_{GS}-V_T)V_{DS}-\frac{V_{DS}^{2}}{2}\right]
$$

飽和區：

$$
I_D=\frac{1}{2}\beta(V_{GS}-V_T)^2
$$

### 2. Hold／Read VTC

令 $V_{in}=V_Q$、$V_{out}=V_{QB}$。每一個輸入電壓都以二分搜尋求解電流平衡，得到 $V_{out}=VTC(V_{in})$。

Hold 模式：

$$
I_{PU}(VDD-V_{in},VDD-V_{out})=I_{PD}(V_{in},V_{out})
$$

Read 模式設定 `WL = BL = VDD`，加入 access transistor PG 的 read-disturb 電流：

$$
I_{PU}+I_{PG}=I_{PD}
$$

其中：

$$
I_{PG}=I_{PG}(VDD-V_{out},VDD-V_{out})
$$

Mirrored VTC 由原始 VTC 交換座標取得：

$$
(x,y)\rightarrow(y,x)
$$

### 3. Trip point 與保留的 legacy SNM proxy

先求 metastable trip point $V_M$：

$$
VTC(V_M)=V_M
$$

再以二分搜尋尋找最大的 $s$，使：

$$
VTC(V_M-s)\geq V_M+s
$$

且：

$$
VTC(V_M+s)\leq V_M-s
$$

搜尋範圍：

$$
0\leq s\leq\min(V_M,VDD-V_M)
$$

Legacy proxy 定義：

$$
SNM_{proxy}=s,\qquad SNM_{mV}=1000s
$$

### 4. 圖表座標

圖表的 0～1 座標是節點電壓對 VDD 的正規化比例：

$$
X=\frac{V_Q}{VDD},\qquad Y=\frac{V_{QB}}{VDD}
$$

例如 VDD = 0.9 V 時，座標 0.5 代表實際電壓 0.45 V。座標使用 ratio 顯示，但 SNM 數值仍以實際電壓計算並輸出為 mV；這與 `Cell Ratio` 或 PU／PG／PD 強度比不同。

### 5. Figure 3.15(b) maximum-square SNM

主報告現在使用數值幾何搜尋，分別找出兩個 butterfly lobes 可容納的最大正方形：

$$
SNM=\min(Square\ 1\ side,\ Square\ 2\ side)
$$

程式對每一個候選水平區間 $[x_i,x_j]$ 計算方形寬度及兩條 VTC 邊界之間的最小垂直空間；只有在整個區間都能容納該方形時才接受。`snm()` 的 metastable-centered 方法仍保留為 legacy traceability，但主報告的 `hold_snm_mv` 與 `read_snm_mv` 已改用 `butterfly_squares()`。

Figure 3.15(b) 方法仍建立在 generic WAT-calibrated square-law VTC 上，適合方向比較與圖形分析，不適合作為 foundry 或 silicon sign-off 數值。

對應實作位於 [`Sram6T.butterfly_squares()`](sram_wat_analyzer.py)。

## PDF Section 3.4.2 Analytical Read SNM

報表另行計算 *High-Speed CMOS Circuit Technology* 第 3.4.2 節、Equation 3.36 的 read-accessed 6T SRAM 解析式。這個結果與 VTC proxy 分開呈現，不取代 Hold SNM，也不直接生成 VTC 曲線。

PDF 定義：

$$
q=\frac{\beta_p}{\beta_a},\qquad r=\frac{\beta_d}{\beta_a}
$$

其中 $\beta_p$、$\beta_a$、$\beta_d$ 分別對應 PU、PG access 與 PD。由於 PDF 假設所有 cell MOS 使用共同 $V_{TH}$，而工具輸入的是三個不同 WAT Vt，因此程式明確採用：

$$
V_{TH,eff}=\frac{|V_{t,PU}|+V_{t,PG}+V_{t,PD}}{3}
$$

中間參數：

$$
V_s=VDD-V_{TH,eff}
$$

$$
V_r=V_s-\frac{r}{r+1}V_{TH,eff}
$$

$$
k=\frac{r}{r+1}\left(\sqrt{\frac{r+1}{r+1-V_s^2/V_r^2}}-1\right)
$$

Equation 3.36 實作為：

$$
SNM_{6T}=V_{TH,eff}-\frac{1}{k+1}\left[
\frac{VDD-\frac{2r+1}{r+1}V_{TH,eff}}{1+\frac{r}{k(r+1)}}-
\frac{VDD-2V_{TH,eff}}{1+\frac{kr}{q}+\sqrt{\frac{r}{q}\left(1+2k+\frac{r}{q}k^2\right)}}
\right]
$$

公式使用長通道 square-law、指定的飽和／線性工作區、共同 VTH 與局部線性假設，且忽略 short-channel effects。若 Equation 3.32 的根號定義域在 28 nm／低 VDD WAT 條件下小於或等於零，報告會顯示 `N/A - outside real-valued analytical domain`，不會強制產生複數或假數值。

額外輸出 `analytical_read_snm_eq_3_36.csv`，包含 Current／Target 的 $q$、$r$、$V_{TH,eff}$、$V_s$、$V_r$、$k$、解析 RSNM 與公式適用性原因。

報表直接列出：

- Current SNM
- Target SNM
- Current − Target 差值（mV）
- 百分比差異

## 輸出

- `sram_wat_report.html`：主要報表，完成後自動開啟
- `snm_target_comparison.csv`：Hold／Read SNM Current vs Target 數值
- `analytical_read_snm_eq_3_36.csv`：PDF Equation 3.36 的解析參數、數值與適用性
- `wat_target_comparison.csv`：PU／PG／PD Vt、Isat差值
- `sram_wat_results.json`：完整 VTC 與分析結果
- `images/01_hold_read_snm_target_comparison.png`：高解析度 PNG
- `images/01_hold_read_snm_target_comparison.svg`：可縮放 SVG
- `images/02_read_snm_fig_3_15_style.png`：Figure 3.15(b) 形式的 Read butterfly 與 Square 1/2
- `images/02_read_snm_fig_3_15_style.svg`：可縮放 Figure 3.15(b) 圖檔
- `images/image_manifest.csv`：圖片用途說明

## WAT-only electrical SNM table

The report also exports `wat_electrical_snm_table.csv`. Its inputs are limited to values available from WAT or entered as datasheet targets:

- PU / PG / PD threshold voltage (`Vt`)
- PU / PG / PD saturation current (`Idsat`)
- WAT measurement VDD and SRAM analysis VDD

The table derives current ratios and square-law beta proxies from `Vt` and `Idsat`, then reports geometric Hold SNM, geometric Read SNM, and the Equation 3.36 analytical Read SNM reference. It does not require W/L, Cox, mobility, BSIM coefficients, extracted parasitics, or other PDK/model-card-only parameters. The absolute SNM values remain generic WAT-calibrated estimates for target correlation, not foundry sign-off values.

### Generic 28 nm fallback policy

Measured WAT values always override generic assumptions. The default operating point is 0.90 V and 25 °C; the generic architecture reference uses L=28 nm and WPU/WPG/WPD=70/100/140 nm. Read assumes WL and the precharged bitlines at VDD, while Hold assumes WL=0 V. The widths and temperature are documented reference values and do not override the beta extracted from WAT.

Equation 3.36 requires a common threshold, so the tool maps the available measurements to `mean(|VtPU|, VtPG, VtPD)`. Effective beta is calculated from measured Vt/Idsat. Cox, mobility, tox and channel-length modulation are therefore left unused instead of being silently guessed. Every default and its active/inactive status is exported in `generic_28nm_assumptions.csv`.

## WAT CSV

```csv
corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids
TT,0.385,44,0.365,82,0.355,124
```

## 工程限制

本工具使用 generic 28 nm 6T compact model，適合 WAT方向比較與前期分析，不是 foundry sign-off simulator。實際量產判定仍需相同偏壓條件的 WAT、foundry PDK／BSIM、實際 W/L、溫度、PEX、array loading 與 mismatch資料。
