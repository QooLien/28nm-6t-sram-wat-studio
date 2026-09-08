$ErrorActionPreference = 'Stop'

$buildDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $buildDir '..\..')).Path
$toolRoot = (Resolve-Path (Join-Path $buildDir '..')).Path
$artifactDir = Join-Path $toolRoot 'build'
$basePath = Join-Path $artifactDir 'SRAM_BLK_Vmin_P90_Tool_base.xlsx'
$vbaPath = Join-Path $buildDir 'P90Analyzer.bas'
$finalDir = $toolRoot
$finalPath = Join-Path $toolRoot 'SRAM_BLK_Vmin_P90_Tool.xlsm'
$chartPng = Join-Path $artifactDir 'final_chart.png'
$previewPdf = Join-Path $artifactDir 'final_workbook_preview.pdf'
$mapPng = Join-Path $artifactDir 'final_wafer_map.png'
$securityPath = 'HKCU:\Software\Microsoft\Office\16.0\Excel\Security'
$accessVbomExisted = $false
$accessVbomOriginal = $null

New-Item -ItemType Directory -Force -Path $finalDir | Out-Null
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
if (Test-Path -LiteralPath $finalPath) { Remove-Item -LiteralPath $finalPath -Force }

if (-not (Test-Path -LiteralPath $securityPath)) { New-Item -Path $securityPath -Force | Out-Null }
$existingSecurity = Get-ItemProperty -LiteralPath $securityPath -Name 'AccessVBOM' -ErrorAction SilentlyContinue
if ($null -ne $existingSecurity) {
    $accessVbomExisted = $true
    $accessVbomOriginal = [int]$existingSecurity.AccessVBOM
}
Set-ItemProperty -LiteralPath $securityPath -Name 'AccessVBOM' -Type DWord -Value 1

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.ScreenUpdating = $false

try {
    $book = $excel.Workbooks.Open($basePath, 0, $false)
    $book.SaveAs($finalPath, 52)

    try {
        $component = $book.VBProject.VBComponents.Add(1)
        $component.Name = 'P90Analyzer'
        $vbaCode = Get-Content -LiteralPath $vbaPath -Raw -Encoding UTF8
        $component.CodeModule.AddFromString($vbaCode)
    }
    catch {
        throw "Excel could not write the VBA project. Original error: $($_.Exception.Message)"
    }

    $inputSheet = $book.Worksheets.Item('Vmin Input')
    $chartSheet = $book.Worksheets.Item('P90 Chart')
    $mapSheet = $book.Worksheets.Item('Wafer Map')

    foreach ($button in @($inputSheet.Buttons())) { $button.Delete() }

    $anchor = $inputSheet.Range('U8')
    $button1 = $inputSheet.Buttons().Add($anchor.Left, $anchor.Top, 180, 34)
    $button1.Name = 'btnCalculateP90'
    $button1.OnAction = 'CalculateP90AndUpdateChart'
    $button1.Characters().Text = 'Calculate P90 / Update Chart'
    $button1.Font.Name = 'Microsoft JhengHei'
    $button1.Font.Size = 10
    $button1.Font.Bold = $true

    $anchor2 = $inputSheet.Range('U10')
    $button2 = $inputSheet.Buttons().Add($anchor2.Left, $anchor2.Top, 180, 30)
    $button2.Name = 'btnClearP90'
    $button2.OnAction = 'ClearP90Results'
    $button2.Characters().Text = 'Clear Results'
    $button2.Font.Name = 'Microsoft JhengHei'
    $button2.Font.Size = 10

    foreach ($button in @($mapSheet.Buttons())) { $button.Delete() }
    $mapAnchor = $mapSheet.Range('N9')
    $mapButton = $mapSheet.Buttons().Add($mapAnchor.Left, $mapAnchor.Top, 160, 32)
    $mapButton.Name = 'btnUpdateWaferMap'
    $mapButton.OnAction = 'UpdateWaferMap'
    $mapButton.Characters().Text = 'Update Wafer Map'
    $mapButton.Font.Name = 'Microsoft JhengHei'
    $mapButton.Font.Size = 10
    $mapButton.Font.Bold = $true

    $inputAnchor = $mapSheet.Range('N11')
    $inputButton = $mapSheet.Buttons().Add($inputAnchor.Left, $inputAnchor.Top, 160, 28)
    $inputButton.Name = 'btnGoToVminInput'
    $inputButton.OnAction = 'GoToVminInput'
    $inputButton.Characters().Text = 'Go to Vmin Input'
    $inputButton.Font.Name = 'Microsoft JhengHei'
    $inputButton.Font.Size = 10

    $chartObject = $chartSheet.ChartObjects().Item(1)
    $chartObject.Name = 'chtVminP90'
    $chart = $chartObject.Chart
    $chart.ChartType = 4
    $chart.PlotVisibleOnly = $false
    $chart.HasLegend = $true
    $chart.Legend.Position = -4160

    $blue = 22 + 119 * 256 + 255 * 65536
    $orange = 242 + 140 * 256 + 40 * 65536
    $gray = 107 + 114 * 256 + 128 * 65536

    $series1 = $chart.SeriesCollection(1)
    $series1.Name = 'Cumulative BLK (%)'
    $series1.Format.Line.ForeColor.RGB = $blue
    $series1.Format.Line.Weight = 2.25
    $series1.MarkerStyle = 8
    $series1.MarkerSize = 5
    $series1.MarkerForegroundColor = $blue
    $series1.MarkerBackgroundColor = 16777215

    $series2 = $chart.SeriesCollection(2)
    $series2.Name = 'P90 Vmin'
    $series2.Format.Line.Visible = 0
    $series2.MarkerStyle = 2
    $series2.MarkerSize = 11
    $series2.MarkerForegroundColor = $orange
    $series2.MarkerBackgroundColor = $orange

    $series3 = $chart.SeriesCollection(3)
    $series3.Name = '90% reference'
    $series3.Format.Line.ForeColor.RGB = $gray
    $series3.Format.Line.Weight = 1.25
    $series3.Format.Line.DashStyle = 4
    $series3.MarkerStyle = -4142

    $chart.Axes(2).MinimumScale = 0
    $chart.Axes(2).MaximumScale = 1
    $chart.Axes(2).MajorUnit = 0.1
    $chart.Axes(2).TickLabels.NumberFormat = '0%'
    $chart.Axes(2).HasTitle = $true
    $chart.Axes(2).AxisTitle.Text = 'Cumulative BLK (%)'
    $chart.Axes(1).HasTitle = $true
    $chart.Axes(1).AxisTitle.Text = 'Vmin Bin'
    $chart.ChartTitle.Font.Name = 'Microsoft JhengHei'

    $chartSheet.Columns('P:S').Hidden = $true

    $excel.Run("'" + $book.Name + "'!CalculateP90AndUpdateChart")
    $book.Save()

    $chart.Export($chartPng, 'PNG') | Out-Null

    try {
        $mapSheet.Activate()
        $mapSheet.Range('A1:O16').Select()
        $mapSheet.Range('A1:O16').CopyPicture(1, 2)
        $mapChartObject = $mapSheet.ChartObjects().Add(0, 0, 1100, 700)
        $mapChartObject.Chart.Paste()
        $mapChartObject.Chart.Export($mapPng, 'PNG') | Out-Null
        $mapChartObject.Delete()
    }
    catch {
        # Excel can reject Range.CopyPicture in a hidden automation session.
        # The artifact-tool render is an equivalent verified preview fallback.
        $fallbackMapPng = Join-Path $artifactDir 'wafer_map_preview.png'
        if (Test-Path -LiteralPath $fallbackMapPng) {
            Copy-Item -LiteralPath $fallbackMapPng -Destination $mapPng -Force
        }
    }

    $inputSheet.PageSetup.PrintArea = '$A$1:$Y$12'
    $inputSheet.PageSetup.Orientation = 2
    $inputSheet.PageSetup.Zoom = $false
    $inputSheet.PageSetup.FitToPagesWide = 1
    $inputSheet.PageSetup.FitToPagesTall = 1
    $chartSheet.PageSetup.PrintArea = '$A$1:$N$24'
    $chartSheet.PageSetup.Orientation = 2
    $chartSheet.PageSetup.Zoom = $false
    $chartSheet.PageSetup.FitToPagesWide = 1
    $chartSheet.PageSetup.FitToPagesTall = 1
    $mapSheet.PageSetup.PrintArea = '$A$1:$O$16'
    $mapSheet.PageSetup.Orientation = 2
    $mapSheet.PageSetup.Zoom = $false
    $mapSheet.PageSetup.FitToPagesWide = 1
    $mapSheet.PageSetup.FitToPagesTall = 1
    $book.ExportAsFixedFormat(0, $previewPdf)

    Write-Output ('Final=' + $finalPath)
    Write-Output ('P90=' + $inputSheet.Range('Q2').Text)
    Write-Output ('Total=' + $inputSheet.Range('O2').Text)
    Write-Output ('Status=' + $inputSheet.Range('S2').Text)
    Write-Output ('Buttons=' + $inputSheet.Buttons().Count)
    Write-Output ('Charts=' + $chartSheet.ChartObjects().Count)
    Write-Output ('MapButtons=' + $mapSheet.Buttons().Count)
    Write-Output ('Mapped=' + $mapSheet.Range('O4').Text)
    Write-Output ('VBModules=' + $book.VBProject.VBComponents.Count)
}
finally {
    if ($book) { $book.Close($true) }
    $excel.Quit()
    if ($chart) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($chart) | Out-Null }
    if ($chartObject) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($chartObject) | Out-Null }
    if ($chartSheet) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($chartSheet) | Out-Null }
    if ($mapSheet) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($mapSheet) | Out-Null }
    if ($inputSheet) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($inputSheet) | Out-Null }
    if ($book) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) | Out-Null }
    [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) | Out-Null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if ($accessVbomExisted) {
        Set-ItemProperty -LiteralPath $securityPath -Name 'AccessVBOM' -Type DWord -Value $accessVbomOriginal
    }
    else {
        Remove-ItemProperty -LiteralPath $securityPath -Name 'AccessVBOM' -ErrorAction SilentlyContinue
    }
}
