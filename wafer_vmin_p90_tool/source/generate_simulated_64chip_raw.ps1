param(
    [string]$SourceWorkbook = "",
    [string]$OutputWorkbook = ""
)

$ErrorActionPreference = 'Stop'

$toolRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceWorkbook)) {
    $SourceWorkbook = Join-Path $toolRoot 'SRAM_BLK_Vmin_P90_Tool_updated.xlsm'
}
if ([string]::IsNullOrWhiteSpace($OutputWorkbook)) {
    $OutputWorkbook = Join-Path $toolRoot 'SRAM_BLK_Vmin_P90_Tool_simulated_64chip.xlsm'
}

if (-not (Test-Path -LiteralPath $SourceWorkbook)) {
    throw "Source workbook was not found: $SourceWorkbook"
}

Copy-Item -LiteralPath $SourceWorkbook -Destination $OutputWorkbook -Force

# B:N in the input sheet is ordered from the hardest bin to the easiest bin.
# A fixed seed lets the same 64-chip example be regenerated when needed.
$rng = [System.Random]::new(20260908)
$rawData = New-Object 'object[,]' 64, 13

function Get-ApproximateNormal {
    param([System.Random]$Random)
    $sum = 0.0
    for ($i = 0; $i -lt 6; $i++) {
        $sum += $Random.NextDouble()
    }
    return ($sum - 3.0) / [Math]::Sqrt(0.5)
}

for ($chip = 0; $chip -lt 64; $chip++) {
    # Low-to-high Vmin-bin index: 0 is <=0.56 V, 12 is Hard Fail.
    # The centre and spread vary by chip to mimic wafer-level process variation.
    $centre = 4.6 + (3.2 * $rng.NextDouble())
    $spread = 0.85 + (0.60 * $rng.NextDouble())

    # A small number of deliberately weak chips make the example distribution useful.
    if (($chip % 19) -eq 18) {
        $centre = 9.3 + (0.8 * $rng.NextDouble())
        $spread = 1.10 + (0.35 * $rng.NextDouble())
    }

    $lowToHigh = New-Object 'int[]' 13
    for ($blk = 0; $blk -lt 256; $blk++) {
        $bin = [int][Math]::Round($centre + ($spread * (Get-ApproximateNormal -Random $rng)))
        $bin = [Math]::Max(0, [Math]::Min(12, $bin))
        $lowToHigh[$bin]++
    }

    # Reverse to match worksheet columns B:N: Hard Fail ... <=0.56 V.
    for ($column = 0; $column -lt 13; $column++) {
        $rawData[$chip, $column] = $lowToHigh[12 - $column]
    }

    $total = 0
    for ($column = 0; $column -lt 13; $column++) {
        $total += [int]$rawData[$chip, $column]
    }
    if ($total -ne 256) {
        throw "Simulated chip $($chip + 1) does not sum to 256 BLK."
    }
}

$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $workbook = $excel.Workbooks.Open($OutputWorkbook, 0, $false)
    $inputSheet = $workbook.Worksheets.Item('Vmin Input')
    $inputSheet.Range('B2:N65').Value2 = $rawData
    $workbook.Save()
}
finally {
    if ($null -ne $workbook) { $workbook.Close($true) }
    if ($null -ne $excel) { $excel.Quit() }
    if ($null -ne $inputSheet) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($inputSheet) }
    if ($null -ne $workbook) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) }
    if ($null -ne $excel) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

Write-Output "Created: $OutputWorkbook"
