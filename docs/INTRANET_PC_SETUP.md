# HV28 SRAM Analysis：公司內網／離線 PC 安裝

本工具為 Python 桌面程式，不需要 SPICE、foundry PDK 或 Model Card。Excel Sweep 需要 `openpyxl`，PNG／HTML 圖表需要 `reportlab` 與 `svglib`。

## 建議環境

- Windows 10 或 Windows 11，64-bit
- 64-bit Python 3.11、3.12 或 3.13
- 安裝 Python 時勾選 `py launcher`；若未安裝 launcher，請勾選 `Add Python to PATH`
- 準備離線套件時，連網 PC 與內網 PC 必須使用相同 Python 主／次版本與相同 64-bit 架構

## 方法 A：內網 PC 可以連公司 PyPI／Proxy

1. 下載或解壓縮完整專案資料夾。
2. 雙擊 `launchers/windows/install_dependencies.cmd`。
3. 安裝完成後雙擊 `launchers/windows/open_sram_wat_analyzer.cmd`。

安裝程式會使用公司已設定的 Python package index；若公司需要 Proxy，請由 IT 先設定 pip。

## 方法 B：完全離線安裝

在一台可上網、且 Python 版本與內網 PC 相同的 Windows PC：

1. 下載完整專案。
2. 雙擊 `launchers/windows/prepare_offline_packages.cmd`。
3. 確認專案內產生 `wheelhouse` 資料夾及多個 `.whl` 檔。
4. 將完整專案資料夾（必須包含 `wheelhouse`）複製到內網 PC。

在內網 PC：

1. 雙擊 `launchers/windows/install_dependencies.cmd`。
2. 安裝程式會自動使用：

   ```text
   pip install --no-index --find-links wheelhouse -r requirements.txt
   ```

3. 出現 `HV28 SRAM dependencies verified` 後，雙擊 `launchers/windows/open_sram_wat_analyzer.cmd`。

## 驗證安裝

在專案資料夾開啟命令提示字元並執行：

```bat
py -3 -c "import tkinter, reportlab, svglib, openpyxl; print('OK')"
py -3 -m unittest -q
```

應看到 `OK`，且所有測試為 `OK`。

## 公司電腦操作

1. 雙擊 `launchers/windows/open_sram_wat_analyzer.cmd`。
2. 手動輸入六顆 MOS WAT，或使用 `Import Excel...` 選擇符合 README 欄位格式的 WAT Excel；Multi-Cell 可先使用 `input/multi_cell_example.xlsx` 驗證。
3. 填入 PU／PG／PD WAT Target 與 Model Settings。
4. 選擇輸出資料夾。
5. 執行一般分析或 `Analyze Excel VDD Sweep`。
6. 程式會開啟 HTML 報告，PNG／CSV／SVG 存放於輸出資料夾。

## 常見問題

### 找不到 Python

重新安裝 64-bit Python，啟用 `py launcher` 或 `Add Python to PATH`。公司內網若禁止自行安裝，請由 IT 部署相同版本。

### `No matching distribution found`

通常是連網 PC 與內網 PC 的 Python 版本或 32／64-bit 架構不同。請使用與內網 PC 完全相同的 Python 重新執行 `launchers/windows/prepare_offline_packages.cmd`。

### `No module named openpyxl`

重新執行 `launchers/windows/install_dependencies.cmd`，並確認 `requirements.txt` 與 `wheelhouse` 都在專案根目錄。

### Tkinter 無法使用

Tkinter 隨官方 Windows Python 安裝。請重新執行 Python Installer，選擇 Modify，啟用 `tcl/tk and IDLE`。

### 公司防毒阻擋 `.cmd`

三個 `.cmd` 都是純文字啟動檔，可交由 IT 檢查。實際執行程式為 `sram_wat_analyzer.py`，不包含下載器或背景服務。

## 安全與模型限制

- GUI 的上次輸入保存在本機 `.hv28_sram_analysis_state.json`，不會上傳 GitHub。
- 報告與 WAT Excel 預設只寫入使用者指定的輸出資料夾。
- 這是 generic 28 nm WAT 校正推演模型，適合 Target 比較與趨勢篩選，不可取代 foundry PDK 或 silicon sign-off。
