$ErrorActionPreference = 'Stop'

$buildDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = (Resolve-Path (Join-Path $buildDir '..\..')).Path
$toolRoot = (Resolve-Path (Join-Path $buildDir '..')).Path
$artifactDir = Join-Path $toolRoot 'build'
$sourcePath = Join-Path $toolRoot 'SRAM_BLK_Vmin_P90_Tool.xlsm'
$testPath = Join-Path $artifactDir 'boundary_test.xlsm'
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
Copy-Item -LiteralPath $sourcePath -Destination $testPath -Force

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false

try {
    $book = $excel.Workbooks.Open($testPath, 0, $false)
    $sheet = $book.Worksheets.Item('Vmin Input')

    $sheet.Range('A3').Value2 = 'X11Y10'
    $sheet.Range('N3').Value2 = 256
    $sheet.Activate()
    $sheet.Range('A3').Select()
    $excel.Run("'" + $book.Name + "'!CalculateP90AndUpdateChart")
    $leftResult = $sheet.Range('Q3').Text

    $sheet.Range('A4').Value2 = 'X12Y10'
    $sheet.Range('N4').Value2 = 226
    $sheet.Range('B4').Value2 = 30
    $sheet.Activate()
    $sheet.Range('A4').Select()
    $excel.Run("'" + $book.Name + "'!CalculateP90AndUpdateChart")
    $hardResult = $sheet.Range('Q4').Text
    $hardStatus = $sheet.Range('S4').Text
    $mapSheet = $book.Worksheets.Item('Wafer Map')
    $leftMap = $mapSheet.Range('G5').Text
    $hardMap = $mapSheet.Range('H5').Text

    Write-Output ('LeftBoundary=' + $leftResult)
    Write-Output ('HardFailBoundary=' + $hardResult)
    Write-Output ('HardFailStatus=' + $hardStatus)
    Write-Output ('LeftMap=' + $leftMap)
    Write-Output ('HardMap=' + $hardMap)

    $expectedLeft = ([char]0x2264).ToString() + '0.56 V'
    if ($leftResult -ne $expectedLeft) { throw 'Left-censored P90 test failed.' }
    if ($hardResult -ne '>1.20 V / Hard Fail') { throw 'Hard-fail P90 test failed.' }
    if ($leftMap -notlike '*0.56 V*') { throw 'Wafer map left-censored location test failed.' }
    if ($hardMap -notlike '*Hard Fail*') { throw 'Wafer map hard-fail location test failed.' }
}
finally {
    if ($book) { $book.Close($false) }
    $excel.Quit()
    if ($mapSheet) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($mapSheet) | Out-Null }
    if ($sheet) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet) | Out-Null }
    if ($book) { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) | Out-Null }
    [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) | Out-Null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
