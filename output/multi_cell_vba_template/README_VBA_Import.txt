Multi-Cell Voltage Template

Files
- Multi_Cell_Voltage_Template.xlsx: blank 6T sheets for 0.685v, 0.90v and 1.20v.
- MultiCellImportCleaner.bas: VBA module for importing and compacting CSV/Excel data.

Install the VBA module
1. Open Multi_Cell_Voltage_Template.xlsx in Excel.
2. Save it as an Excel Macro-Enabled Workbook (*.xlsm).
3. Press Alt+F11, choose File > Import File, and select MultiCellImportCleaner.bas.
4. Run ImportMultiCellFile from the VBA editor or from the Macro dialog.

Processing rules
- Only worksheet names matching a voltage, such as 0.685v, 0.90v or 1.20v, are processed.
- For CSV input, the filename must contain the voltage, for example 0.90v.csv.
- A/B populated and C:N blank: the next unassigned row with blank A/B and populated C:N is moved into that row.
- A/B blank and C:N populated: the complete row is removed from the compacted array.
- Blank rows are removed.
- The worksheet is read once into an array and written back once; no worksheet row deletion loop is used.

Safety
- ImportMultiCellFile opens the selected source as read-only and writes values into the template workbook.
- The source file is not modified.
