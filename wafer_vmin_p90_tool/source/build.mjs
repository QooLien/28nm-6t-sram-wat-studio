import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "wafer_vmin_p90_tool/build";
await fs.mkdir(outputDir, { recursive: true });

const font = "Microsoft JhengHei";
const navy = "#183B56";
const blue = "#1677FF";
const orange = "#F28C28";
const lightBlue = "#EAF3FB";
const lightYellow = "#FFF6D9";
const lightGray = "#EEF2F6";
const border = "#CBD5E1";

const inputBins = [
  "Hard Fail", "1.20 V", "1.035 V", "0.99 V", "0.90 V", "0.81 V",
  "0.765 V", "0.72 V", "0.68 V", "0.64 V", "0.60 V", "0.56 V", "≤0.56 V",
];
const chartBins = [...inputBins].reverse();
const sampleCounts = [22, 0, 1, 2, 3, 5, 8, 15, 25, 40, 55, 40, 40];
const chartCounts = [...sampleCounts].reverse();
const total = sampleCounts.reduce((a, b) => a + b, 0);
const rank = Math.ceil(total * 0.9);
let running = 0;
let p90Index = -1;
const cumulative = chartCounts.map((v, i) => {
  running += v;
  if (p90Index < 0 && running >= rank) p90Index = i;
  return running / total;
});

const waferRows = [
  { y: 10, xStart: 10, xEnd: 13 },
  { y: 11, xStart: 8, xEnd: 15 },
  { y: 12, xStart: 7, xEnd: 16 },
  { y: 13, xStart: 7, xEnd: 16 },
  { y: 14, xStart: 7, xEnd: 16 },
  { y: 15, xStart: 7, xEnd: 16 },
  { y: 16, xStart: 8, xEnd: 15 },
  { y: 17, xStart: 10, xEnd: 13 },
];
const waferLocations = waferRows.flatMap(({ y, xStart, xEnd }) =>
  Array.from({ length: xEnd - xStart + 1 }, (_, i) => `X${xStart + i}Y${y}`),
);

const workbook = Workbook.create();
const chartSheet = workbook.worksheets.add("P90 Chart");
const mapSheet = workbook.worksheets.add("Wafer Map");
const inputSheet = workbook.worksheets.add("Vmin Input");

// Output/chart sheet.
chartSheet.showGridLines = false;
chartSheet.tabColor = navy;
chartSheet.getRange("A1:N1").merge();
chartSheet.getRange("A1").values = [["Single-Chip BLK Vmin P90 Analysis"]];
chartSheet.getRange("A1:N1").format = {
  font: { name: font, size: 16, bold: true, color: navy },
  verticalAlignment: "center",
};
chartSheet.getRange("A2:N2").merge();
chartSheet.getRange("A2").values = [["選擇 Vmin Input 的 Chip 資料列，再按「計算 P90／更新圖表」。P90 Vmin 越低代表低壓操作能力越好。"]];
chartSheet.getRange("A2:N2").format = {
  font: { name: font, size: 10, italic: true, color: "#5B6777" },
};
chartSheet.getRange("A1:N2").format.rowHeight = 25;
chartSheet.getRange("A1:N2").format.columnWidth = 12;

chartSheet.getRange("P1:S14").values = [
  ["Vmin Bin", "Cumulative BLK (%)", "P90 Marker", "P90 Reference"],
  ...chartBins.map((b, i) => [b, cumulative[i], i === p90Index ? cumulative[i] : null, 0.9]),
];
chartSheet.getRange("Q2:S14").format.numberFormat = "0%";
chartSheet.getRange("P1:S1").format = {
  fill: navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
};
chartSheet.getRange("P2:S14").format.font = { name: font, size: 9, color: "#334155" };

const chart = chartSheet.charts.add("line", chartSheet.getRange("P1:S14"));
chart.title = "Chip A - Vmin Cumulative Distribution (P90 = 0.90 V)";
chart.titleTextStyle.typeface = font;
chart.titleTextStyle.fontSize = 14;
chart.legend = { position: "top", textStyle: { typeface: font, fontSize: 10 } };
chart.xAxis = { axisType: "textAxis", textStyle: { typeface: font, fontSize: 9 } };
chart.yAxis = {
  minimumScale: 0,
  maximumScale: 1,
  majorUnit: 0.1,
  numberFormatCode: "0%",
  numberFormatSourceLinked: false,
  textStyle: { typeface: font, fontSize: 9 },
};
chart.xAxis.title.text = "Vmin Bin";
chart.yAxis.title.text = "Cumulative BLK (%)";
chart.setPosition("A4", "N24");
if (chart.series.items[0]) chart.series.items[0].line = { fill: blue, style: "solid", width: 2.5 };
if (chart.series.items[1]) chart.series.items[1].line = { fill: orange, style: "solid", width: 3 };
if (chart.series.items[2]) chart.series.items[2].line = { fill: "#6B7280", style: "dashed", width: 1.5 };

// Fixed 64-chip wafer map: X7-X16 and Y10-Y17 with the requested edge shape.
mapSheet.showGridLines = false;
mapSheet.tabColor = "#2A9D8F";
mapSheet.getRange("A1:O1").merge();
mapSheet.getRange("A1").values = [["64-Chip Wafer Map - P90 Vmin"]];
mapSheet.getRange("A1:O1").format = {
  font: { name: font, size: 16, bold: true, color: navy },
  verticalAlignment: "center",
};
mapSheet.getRange("A2:O2").merge();
mapSheet.getRange("A2").values = [["固定座標：左上第一顆為 X10Y10。計算 P90 後，Chip Location 會自動對應至 wafer map。低 Vmin 表現較佳。"]];
mapSheet.getRange("A2:O2").format = {
  font: { name: font, size: 10, italic: true, color: "#5B6777" },
};
mapSheet.getRange("C4:L4").values = [[...Array.from({ length: 10 }, (_, i) => `X${7 + i}`)]];
mapSheet.getRange("B5:B12").values = Array.from({ length: 8 }, (_, i) => [`Y${10 + i}`]);
mapSheet.getRange("C4:L4").format = {
  fill: navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
mapSheet.getRange("B5:B12").format = {
  fill: navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
const waferGrid = Array.from({ length: 8 }, (_, yIdx) =>
  Array.from({ length: 10 }, (_, xIdx) => {
    const y = 10 + yIdx;
    const x = 7 + xIdx;
    const spec = waferRows.find((row) => row.y === y);
    return spec && x >= spec.xStart && x <= spec.xEnd ? `X${x}Y${y}` : null;
  }),
);
mapSheet.getRange("C5:L12").values = waferGrid;
const validSegments = ["F5:I5", "D6:K6", "C7:L10", "D11:K11", "F12:I12"];
for (const address of validSegments) {
  mapSheet.getRange(address).format = {
    fill: lightBlue,
    font: { name: font, size: 10, bold: true, color: navy },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#9FB3C8" },
  };
}
mapSheet.getRange("C5:L12").format.rowHeight = 52;
mapSheet.getRange("C:L").format.columnWidth = 12;
mapSheet.getRange("B:B").format.columnWidth = 8;
mapSheet.getRange("A:A").format.columnWidth = 3;
mapSheet.getRange("M:M").format.columnWidth = 3;
mapSheet.getRange("N:N").format.columnWidth = 20;
mapSheet.getRange("O:O").format.columnWidth = 13;
mapSheet.getRange("N3:O3").values = [["Map Summary", "Count"]];
mapSheet.getRange("N4:N7").values = [["Mapped P90"], ["Missing P90"], ["Hard Fail Tail"], ["Duplicate Location"]];
mapSheet.getRange("O4:O7").values = [[1], [63], [0], [0]];
mapSheet.getRange("N3:O3").format = {
  fill: navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
mapSheet.getRange("N4:O7").format = {
  fill: lightGray,
  font: { name: font, size: 10, color: "#334155" },
  borders: { preset: "all", style: "thin", color: border },
};
mapSheet.getRange("O4:O7").format.horizontalAlignment = "center";
mapSheet.getRange("B14:L14").merge();
mapSheet.getRange("B14").values = [["P90 Vmin color guide (lower is better)"]];
mapSheet.getRange("B14:L14").format = { font: { name: font, size: 10, bold: true, color: navy } };
mapSheet.getRange("B15:F15").values = [["≤0.56 V", "0.56-0.68 V", "0.72-0.81 V", "0.90-1.20 V", "Hard Fail tail"]];
mapSheet.getRange("B15:F15").format = {
  font: { name: font, size: 9, bold: true, color: "#25364A" },
  horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: border },
};
mapSheet.getRange("B15").format.fill = "#B7E4C7";
mapSheet.getRange("C15").format.fill = "#D8F3DC";
mapSheet.getRange("D15").format.fill = "#FFF3BF";
mapSheet.getRange("E15").format.fill = "#FFD8A8";
mapSheet.getRange("F15").format.fill = "#FFB3B3";
mapSheet.getRange("F5").values = [["X10Y10\n0.90 V"]];
mapSheet.getRange("F5").format.fill = "#FFD8A8";

// Input sheet.
inputSheet.showGridLines = false;
inputSheet.tabColor = "#5B9BD5";
const headers = [
  "Chip Location", ...inputBins, "Total BLK", "P90 Rank", "P90 Vmin", "Hard Fail", "Status",
];
inputSheet.getRange("A1:S1").values = [headers];
inputSheet.getRange("A1:S1").format = {
  fill: navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};
inputSheet.getRange("A1:S1").format.rowHeight = 42;
inputSheet.getRange("A2:S200").format.font = { name: font, size: 10, color: "#25364A" };
inputSheet.getRange("A2:N200").format.fill = lightYellow;
inputSheet.getRange("O2:S200").format.fill = lightBlue;
inputSheet.getRange("A2:S200").format.verticalAlignment = "center";
inputSheet.getRange("B2:S200").format.horizontalAlignment = "center";
inputSheet.getRange("A2:S200").format.borders = { preset: "all", style: "thin", color: border };
inputSheet.getRange("A2:A65").values = waferLocations.map((location) => [location]);
inputSheet.getRange("B2:N2").values = [[...sampleCounts]];
inputSheet.getRange("O2").formulas = [["=SUM(B2:N2)"]];
inputSheet.getRange("P2").formulas = [["=ROUNDUP(O2*0.9,0)"]];
inputSheet.getRange("Q2:S2").values = [["0.90 V", 22, "Ready"]];
inputSheet.getRange("O2:P200").format.numberFormat = "0";
inputSheet.getRange("R2:R200").format.numberFormat = "0";
inputSheet.getRange("B2:N200").dataValidation = {
  rule: { type: "whole", operator: "between", formula1: 0, formula2: 256 },
};
inputSheet.getRange("U1:Y1").merge();
inputSheet.getRange("U1").values = [["使用方式"]];
inputSheet.getRange("U1:Y1").format = {
  fill: navy,
  font: { name: font, size: 11, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
inputSheet.getRange("U2:Y6").merge();
inputSheet.getRange("U2").values = [[
  "1. A 欄輸入 Chip Location。\n2. B:N 輸入各 Vmin 分組的 BLK 數。\n3. 每列合計必須為 256。\n4. 選取任一 Chip 資料列後按按鈕。\n5. Hard Fail 若進入 P90，結果顯示 >1.20 V。",
]];
inputSheet.getRange("U2:Y6").format = {
  fill: lightGray,
  font: { name: font, size: 10, color: "#334155" },
  wrapText: true,
  verticalAlignment: "top",
  borders: { preset: "outside", style: "thin", color: border },
};
inputSheet.getRange("A2:S200").conditionalFormats.add("expression", {
  formula: "=AND($A2<>\"\",COUNT($B2:$N2)>0,$O2<>256)",
  format: { fill: "#FDECEC", font: { color: "#B42318", bold: true } },
});
inputSheet.freezePanes.freezeRows(1);
inputSheet.freezePanes.freezeColumns(1);
inputSheet.getRange("A:A").format.columnWidth = 17;
inputSheet.getRange("B:B").format.columnWidth = 15;
inputSheet.getRange("C:N").format.columnWidth = 11;
inputSheet.getRange("O:P").format.columnWidth = 12;
inputSheet.getRange("Q:Q").format.columnWidth = 18;
inputSheet.getRange("R:R").format.columnWidth = 12;
inputSheet.getRange("S:S").format.columnWidth = 27;
inputSheet.getRange("T:T").format.columnWidth = 3;
inputSheet.getRange("U:Y").format.columnWidth = 12;
inputSheet.getRange("A2:S200").format.rowHeight = 22;

workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: "Vmin Input!A1:S4",
  include: "values,formulas",
  tableMaxRows: 4,
  tableMaxCols: 19,
  maxChars: 8000,
});
await fs.writeFile(`${outputDir}/inspect.ndjson`, check.ndjson ?? String(check), "utf8");
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
await fs.writeFile(`${outputDir}/errors.ndjson`, errors.ndjson ?? String(errors), "utf8");

const inputPreview = await workbook.render({
  sheetName: "Vmin Input",
  range: "A1:Y10",
  scale: 1.2,
  format: "png",
});
await fs.writeFile(`${outputDir}/input_preview.png`, new Uint8Array(await inputPreview.arrayBuffer()));
const chartPreview = await workbook.render({
  sheetName: "P90 Chart",
  range: "A1:N24",
  scale: 1.2,
  format: "png",
});
await fs.writeFile(`${outputDir}/chart_preview.png`, new Uint8Array(await chartPreview.arrayBuffer()));
const mapPreview = await workbook.render({
  sheetName: "Wafer Map",
  range: "A1:O16",
  scale: 1.2,
  format: "png",
});
await fs.writeFile(`${outputDir}/wafer_map_preview.png`, new Uint8Array(await mapPreview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(`${outputDir}/SRAM_BLK_Vmin_P90_Tool_base.xlsx`);

console.log(JSON.stringify({ output: `${outputDir}/SRAM_BLK_Vmin_P90_Tool_base.xlsx`, total, rank, p90: "0.90 V" }));
