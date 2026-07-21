# HV28 SRAM Analysis

## Datasheet WAT Target 驗證

本工具的主要目的，是使用實測 WAT 與 WT Vmin 判斷 Datasheet 的 PU／PG／PD Vt、Idsat Target 是否能有效篩選 SRAM WT 表現。Target 是待驗證的假設，不直接視為正確答案。

GUI 可設定 Vt／Idsat Target band、Scan4N／Select_Write／Select_Read Vmin 上限，並可選擇歷史 CSV。報告會輸出 Target band 內外的 WT pass rate、pass-rate lift、Target distance 對 worst normalized Vmin 的 Pearson／Spearman correlation，以及 `SUPPORTED`、`CONTRADICTED`、`INCONCLUSIVE` 或 `INSUFFICIENT DATA` 結論。樣本不足時不會硬判 Target 合理。

歷史 CSV 欄位如下；WT 全測但 WAT 未量測時，WAT 欄位可以留白。WT-only 資料會計入資料覆蓋率，只有 WAT 與 WT 成對的列會進入 Target 統計驗證。

```csv
lot_wafer,pu_vt,pu_idsat,pg_vt,pg_idsat,pd_vt,pd_idsat,scan4n_vmin,select_write_vmin,select_read_vmin
LOT01_W01,0.380,45,0.370,80,0.360,120,0.610,0.570,0.590
LOT01_W02,,,,,,,0.620,0.580,0.600
```

新增輸出：`wat_target_validation_rows.csv`、`wat_target_validation_summary.csv` 與逐項 PU／PG／PD 證據表 `wat_target_parameter_evidence.csv`。

## 新增的穩定度與尺寸比例輸出

- **Cell Ratio**：模型 β 比值 `PD / PG`；另保留直接由 WAT 計算的 Isat/Ids proxy。
- **Pull-up Ratio**：模型 β 比值 `PG / PU`；另保留直接由 WAT 計算的 Isat/Ids proxy。
- **Hold SNM**：WL 關閉、cell 保持資料時，以 butterfly maximum-square 方法估算。
- **Write SNM**：以「低電位 write bitline 可容許的最大雜訊」定義之 compact-model proxy，適合比較 WAT 上下變動方向，不等同 foundry/SPICE sign-off WSNM。

上述項目與 Read SNM 皆由手動輸入的 WAT Vt/Isat 自動推導；報表會輸出數值、敏感度表格，以及獨立 PNG/SVG 圖片。

### 判斷模型

GUI 的 **Judgment targets** 可填入設計或 datasheet 規格。每一項採 higher-is-better 判定：

- `PASS`：模型值大於或等於目標。
- `MARGINAL`：低於目標，但仍在使用者設定的 marginal band 內。
- `FAIL`：低於 marginal band；總判定只要任一項 FAIL 即為 FAIL。

輸出會包含各項 margin、總判定、PU/PG/PD 建議調整方向，以及 `parameter_judgment.csv`。預設門檻只是可編輯範例，不能視為所有 28 nm SRAM 的共同 sign-off 規格。

固定使用 generic 28 nm 6T SRAM 架構，以 WAT 的 `Vt` / `Ids` 校準 compact model，分析 PU（pull-up）、PG（pass-gate）、PD（pull-down）個別向上／向下變動時，對 hold/read SNM 與 read/write Vmin 的影響。

固定 bitcell 架構為 2×PU PMOS、2×PG NMOS、2×PD NMOS；L=28 nm，WPU/WPG/WPD=70/100/140 nm，預設 VDD=0.9 V、溫度 25 °C。介面不提供其他製程節點選項。

## 快速使用

Windows 直接雙擊 `open_sram_wat_analyzer.cmd`。介面可切換兩種物件導向模式：

第一次啟動若缺少 PNG 圖表元件，啟動程式會依 `requirements.txt` 自動安裝 ReportLab 與 svglib。
Windows 啟動時會自動最大化；右側分析欄可垂直捲動，而 `Analyze & Open HTML` 按鈕固定在視窗底部，避免受到螢幕高度或顯示縮放影響。

- `3T Merged`：PU、PG、PD 三個共享物件，左右對稱映射到實體 6T bitcell。
- `6T Independent`：PUL、PUR、PGL、PGR、PDL、PDR 六個獨立物件，可分析左右 mismatch。

在 Datasheet test targets 輸入 PU／PG／PD 的目標 Vt 與 Isat，程式會和實際 WAT 量測值比較並輸出 ΔVt、ΔIsat 與百分比。Target 只作比較，不會取代 WAT 校準模型。再手動填入測試完成後的 Scan4N、Select_Write、Select_Read Vmin；這三個實測值不由 Python 模型產生。完成後按「Analyze & Open Report」。不需要 SPICE，也不需要先準備 CSV。

目前版本不執行假設性的 Vt/Ids variation sweep；SNM 只使用實際輸入的 WAT 值建立完整 6T cell 分析。

`Vmin Start / Stop / Step` 是實際 WT tester sweep recipe，因此保留為手動輸入：Start 是掃描起始電壓、Stop 是終止電壓、Step 是每次調整的電壓間距。三者會寫入報表，並決定模型估算時使用的搜尋範圍與解析度。

CSV 僅是多個 corner 批次分析的選用功能：

```powershell
python sram_wat_analyzer.py --input sample_wat.csv --output output
```

若 CSV 有多個 corner，程式會在輸出目錄下為每個 corner 建立子目錄。輸出包含：

- `sram_wat_report.html`：主要輸出報表；分析完成後會自動在瀏覽器開啟
- `sram_wat_results.csv`：可匯入 Excel/JMP 的數值
- `wat_target_comparison.csv`：Datasheet target 與各 PU/PG/PD WAT 實測物件的 Vt／Isat 差值
- `wt_test_0bit_vmin.csv`：手動輸入的 Scan4N、Select_Write、Select_Read WT Vmin 與資料來源
- `sram_wat_results.json`：完整機器可讀結果與 VTC 曲線
- `images/`：完整 6T cell 的 Hold／Read／Write SNM butterfly 高解析度 PNG；同名 SVG 保留作為可縮放圖檔
- `images/image_manifest.csv`：圖片檔名、格式、用途、對應元件與說明

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
- SNM：以完整 6T cell 分析 Hold、Read 與 Write operating condition，不將 SNM 拆成個別 PU／PG／PD 數值。
- WT target：PG／PU／PD 是測試時選定的 target。WT Vmin 是測試完成後手動輸入的量測結果，不使用 compact model 取代實測值。

Vmin 範圍與步階、SNM 下限、Vt/Ids 調整量都可由 GUI 或 CLI 改動。

## 工程限制

本工具是 generic 28 nm 架構推演，不含任何晶圓廠專有 PDK。它用於 WAT correlation、SNM 方向判讀與設計前期分析，不是 sign-off simulator。實際 tape-out/量產判定仍需 foundry BSIM model card、實際 W/L、body effect、DIBL、溫度、PEX、bitline/WL waveform、cell/array loading 與 Monte Carlo mismatch。WAT Ids 若不是在相同偏壓條件量測，應先正規化再輸入。
