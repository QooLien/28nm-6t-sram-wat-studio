# 28 nm 6T SRAM × WAT PU/PG/PD Analyzer

固定使用 generic 28 nm 6T SRAM 架構，以 WAT 的 `Vt` / `Ids` 校準 compact model，分析 PU（pull-up）、PG（pass-gate）、PD（pull-down）個別向上／向下變動時，對 hold/read SNM 與 read/write Vmin 的影響。

固定 bitcell 架構為 2×PU PMOS、2×PG NMOS、2×PD NMOS；L=28 nm，WPU/WPG/WPD=70/100/140 nm，預設 VDD=0.9 V、溫度 25 °C。介面不提供其他製程節點選項。

## 快速使用

Windows 直接雙擊 `open_sram_wat_analyzer.cmd`。介面可切換兩種物件導向模式：

- `3T Merged`：PU、PG、PD 三個共享物件，左右對稱映射到實體 6T bitcell。
- `6T Independent`：PU1、PU2、PG1、PG2、PD1、PD2 六個獨立物件，可分析左右 mismatch。

選擇 PG／PU／PD 作為當次 WT test target，再手動填入測試完成後的 Scan4N、Select_Write、Select_Read Vmin。這三個實測值不由 Python 模型產生；報表中的 model-estimated margin 會分開標示。完成後按「Analyze & Open Report」。不需要 SPICE，也不需要先準備 CSV。

介面不要求填寫 WAT 表中通常沒有的 variation 欄位。Vt ±30 mV 與 Isat/Ids ±10% 是程式內部的假設 sensitivity sweep，不是 WAT 實測數據；報表會清楚標成 assumed scenario。

`Vmin Start / Stop / Step` 是實際 WT tester sweep recipe，因此保留為手動輸入：Start 是掃描起始電壓、Stop 是終止電壓、Step 是每次調整的電壓間距。三者會寫入報表，並決定模型估算時使用的搜尋範圍與解析度。

CSV 僅是多個 corner 批次分析的選用功能：

```powershell
python sram_wat_analyzer.py --input sample_wat.csv --output output
```

若 CSV 有多個 corner，程式會在輸出目錄下為每個 corner 建立子目錄。輸出包含：

- `sram_wat_report.html`：PU／PG／PD 個別 read butterfly、SNM 與 R/W Vmin 圖表
- `sram_wat_results.csv`：可匯入 Excel/JMP 的數值
- `sram_mos_results.csv`：六顆實體 MOS 各自的 Vt/Ids sensitivity 與 worst-side 結果
- `wt_test_0bit_vmin.csv`：手動輸入的 Scan4N、Select_Write、Select_Read WT Vmin 與資料來源
- `sram_wat_results.json`：完整機器可讀結果與 VTC 曲線
- `images/`：架構圖、PU／PG／PD Butterfly、SNM、Model Read/Write Vmin 的獨立 SVG 圖片
- `images/image_manifest.csv`：圖片檔名、對應元件與用途說明

## WAT CSV 格式

必要欄位如下。PMOS 的 `pu_vt` 可填負值或絕對值，程式一律使用 `|Vtp|`；Ids 單位是 µA。

```csv
corner,pu_vt,pu_ids,pg_vt,pg_ids,pd_vt,pd_ids
TT,0.42,45,0.40,80,0.39,120
```

每列代表一個 WAT corner 或量測點。預設 WAT Ids 測試電壓為 1.2 V，必須依實際量測條件調整。

## 計算定義

- MOS：以 WAT `Vt`、`Ids` 校準 28 nm 推演用 compact 元件，使 `VGS=VDS=WAT VDD` 時的飽和電流等於輸入 Ids。
- Hold/Read SNM：由交叉耦合 inverter VTC 的 maximum-square 方法計算。Read 模式把兩條 bitline 預充到 VDD 並打開 WL。
- Read Vmin：最低 VDD，需同時滿足 read SNM 下限（預設 30 mV）與 read 後節點保持 Q < 35% VDD、QB > 65% VDD。
- Write Vmin：從 Q=1 寫入 Q=0，最低 VDD 需達到 Q < 20% VDD、QB > 80% VDD。
- Sensitivity：PU、PG、PD 每次只改一個參數；預設 Vt ±30 mV、Ids ±10%。
- WT target：PG／PU／PD 是測試時選定的 target。WT Vmin 是測試完成後手動輸入的量測結果，不使用 compact model 取代實測值。

Vmin 範圍與步階、SNM 下限、Vt/Ids 調整量都可由 GUI 或 CLI 改動。

## 工程限制

本工具是 generic 28 nm 架構推演，不含任何晶圓廠專有 PDK。它用於 WAT correlation、方向判讀與設計前期 sensitivity，不是 sign-off simulator。實際 tape-out/量產判定仍需 foundry BSIM model card、實際 W/L、body effect、DIBL、溫度、PEX、bitline/WL waveform、cell/array loading 與 Monte Carlo mismatch。WAT Ids 若不是在相同偏壓條件量測，應先正規化再輸入。
