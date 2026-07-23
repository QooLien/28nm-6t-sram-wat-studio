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

圖中不再繪製 SNM square，讓 Current 與 Target 曲線位移更容易比較。現行 `snm()` 是以 metastable trip point 為中心的數值 proxy，並不是掃描兩個 butterfly lobes 所有位置的完整 maximum-square 萃取；詳細公式與限制如下。

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

### 3. Trip point 與現行 SNM proxy

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

現行報告輸出：

$$
SNM_{proxy}=s,\qquad SNM_{mV}=1000s
$$

### 4. 圖表座標

圖表的 0～1 座標是節點電壓對 VDD 的正規化比例：

$$
X=\frac{V_Q}{VDD},\qquad Y=\frac{V_{QB}}{VDD}
$$

例如 VDD = 0.9 V 時，座標 0.5 代表實際電壓 0.45 V。座標使用 ratio 顯示，但 SNM 數值仍以實際電壓計算並輸出為 mV；這與 `Cell Ratio` 或 PU／PG／PD 強度比不同。

### 5. SNM 方法限制

標準 butterfly SNM 應搜尋兩個 lobes 各自可容納的最大正方形，並取較小的正方形邊長。現行公式強制候選位置相對於 $V_M$ 對稱，沒有掃描正方形的所有可能位置，因此可能高估絕對 SNM。請將目前數值視為 `WAT-calibrated metastable-centered SNM proxy`，適合 Current／Target 趨勢比較，不適合作為 foundry 或 silicon sign-off 數值。

對應實作位於 [`Device.current()` 與 `Sram6T.snm()`](sram_wat_analyzer.py#L199-L310)。

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
