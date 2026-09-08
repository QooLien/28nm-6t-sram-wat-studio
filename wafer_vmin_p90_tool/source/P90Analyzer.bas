Option Explicit

Private Const INPUT_SHEET As String = "Vmin Input"
Private Const CHART_SHEET As String = "P90 Chart"
Private Const MAP_SHEET As String = "Wafer Map"
Private Const FIRST_DATA_ROW As Long = 2
Private Const FIRST_BIN_COL As Long = 2       ' B
Private Const LAST_BIN_COL As Long = 14       ' N
Private Const EXPECTED_BLOCKS As Long = 256

Public Sub CalculateP90AndUpdateChart()
    Dim ws As Worksheet
    Dim lastRow As Long
    Dim selectedRow As Long
    Dim data As Variant
    Dim results() As Variant
    Dim r As Long, c As Long
    Dim total As Double, cumulative As Double
    Dim p90Rank As Long, p90Col As Long
    Dim invalidInput As Boolean
    Dim hasAnyCount As Boolean
    Dim p90Label As String

    On Error GoTo Fail
    Application.ScreenUpdating = False
    Application.EnableEvents = False

    Set ws = ThisWorkbook.Worksheets(INPUT_SHEET)
    lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If lastRow < FIRST_DATA_ROW Then
        MsgBox "沒有可計算的 Chip 資料。", vbExclamation, "P90 Analysis"
        GoTo CleanExit
    End If

    If ActiveSheet.Name = INPUT_SHEET And ActiveCell.Row >= FIRST_DATA_ROW And ActiveCell.Row <= lastRow Then
        selectedRow = ActiveCell.Row
    Else
        selectedRow = FIRST_DATA_ROW
    End If

    data = ws.Range(ws.Cells(FIRST_DATA_ROW, 1), ws.Cells(lastRow, LAST_BIN_COL)).Value2
    ReDim results(1 To UBound(data, 1), 1 To 5)

    For r = 1 To UBound(data, 1)
        If Len(Trim$(CStr(data(r, 1)))) > 0 Then
            total = 0
            invalidInput = False
            hasAnyCount = False
            For c = FIRST_BIN_COL To LAST_BIN_COL
                If IsEmpty(data(r, c)) Or Len(Trim$(CStr(data(r, c)))) = 0 Then
                    data(r, c) = 0
                ElseIf Not IsNumeric(data(r, c)) Or CDbl(data(r, c)) < 0 Then
                    invalidInput = True
                Else
                    hasAnyCount = True
                    total = total + CDbl(data(r, c))
                End If
            Next c

            If Not hasAnyCount Then
                results(r, 5) = ""
            ElseIf invalidInput Then
                results(r, 5) = "Invalid count"
            Else
                results(r, 1) = total
                results(r, 4) = IIf(IsNumeric(data(r, FIRST_BIN_COL)), data(r, FIRST_BIN_COL), "")
            End If

            If hasAnyCount And Not invalidInput And total <> EXPECTED_BLOCKS Then
                results(r, 5) = "Check total: must equal 256"
            ElseIf hasAnyCount And Not invalidInput Then
                p90Rank = WorksheetFunction.RoundUp(total * 0.9, 0)
                cumulative = 0
                p90Col = 0
                ' Input columns are high-to-low, so accumulate from N back to B.
                For c = LAST_BIN_COL To FIRST_BIN_COL Step -1
                    cumulative = cumulative + CDbl(data(r, c))
                    If cumulative >= p90Rank Then
                        p90Col = c
                        Exit For
                    End If
                Next c

                results(r, 2) = p90Rank
                If p90Col = LAST_BIN_COL Then
                    p90Label = ChrW(&H2264) & "0.56 V"
                    results(r, 5) = "Ready (left-censored)"
                ElseIf p90Col = FIRST_BIN_COL Then
                    p90Label = ">1.20 V / Hard Fail"
                    results(r, 5) = "P90 not observable"
                Else
                    p90Label = CStr(ws.Cells(1, p90Col).Value2)
                    results(r, 5) = "Ready"
                End If
                results(r, 3) = p90Label
            End If
        End If
    Next r

    ws.Range(ws.Cells(FIRST_DATA_ROW, 15), ws.Cells(lastRow, 19)).Value = results
    UpdateWaferMap

    If ws.Cells(selectedRow, 15).Value2 <> EXPECTED_BLOCKS Then
        ws.Activate
        ws.Cells(selectedRow, 15).Select
        MsgBox "選取列的 BLK 總數不是 256，請先修正輸入資料。", vbExclamation, "P90 Analysis"
        GoTo CleanExit
    End If

    UpdateChartForRow selectedRow

CleanExit:
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    Exit Sub

Fail:
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    MsgBox "P90 計算失敗：" & Err.Description, vbCritical, "P90 Analysis"
End Sub

Public Sub UpdateWaferMap()
    Dim wsIn As Worksheet, wsMap As Worksheet
    Dim lastRow As Long, r As Long
    Dim xCoord As Long, yCoord As Long
    Dim target As Range
    Dim locationText As String, key As String, p90Label As String
    Dim seen As Object
    Dim mappedCount As Long, hardFailCount As Long, duplicateCount As Long

    On Error GoTo MapFail
    Set wsIn = ThisWorkbook.Worksheets(INPUT_SHEET)
    Set wsMap = ThisWorkbook.Worksheets(MAP_SHEET)
    Set seen = CreateObject("Scripting.Dictionary")

    ResetWaferMap
    lastRow = wsIn.Cells(wsIn.Rows.Count, 1).End(xlUp).Row
    For r = FIRST_DATA_ROW To lastRow
        locationText = Trim$(CStr(wsIn.Cells(r, 1).Value2))
        p90Label = Trim$(CStr(wsIn.Cells(r, 17).Value2))
        If Len(locationText) > 0 And TryParseChipLocation(locationText, xCoord, yCoord) Then
            If IsValidWaferCoordinate(xCoord, yCoord) Then
                key = "X" & xCoord & "Y" & yCoord
                Set target = WaferMapCell(wsMap, xCoord, yCoord)
                If seen.Exists(key) Then
                    duplicateCount = duplicateCount + 1
                    target.Value = key & vbLf & "DUPLICATE"
                    target.Interior.Color = RGB(238, 134, 149)
                Else
                    seen.Add key, r
                    If Len(p90Label) > 0 Then
                        mappedCount = mappedCount + 1
                        target.Value = key & vbLf & p90Label
                        ApplyVminColor target, p90Label
                        If InStr(1, p90Label, "Hard Fail", vbTextCompare) > 0 Then hardFailCount = hardFailCount + 1
                    End If
                End If
            End If
        End If
    Next r

    wsMap.Range("O4").Value = mappedCount
    wsMap.Range("O5").Value = 64 - mappedCount
    wsMap.Range("O6").Value = hardFailCount
    wsMap.Range("O7").Value = duplicateCount
    wsMap.Activate
    Exit Sub

MapFail:
    MsgBox "Wafer map update failed: " & Err.Description, vbExclamation, "Wafer Map"
End Sub

Private Sub ResetWaferMap()
    Dim wsMap As Worksheet
    Dim xCoord As Long, yCoord As Long
    Dim target As Range

    Set wsMap = ThisWorkbook.Worksheets(MAP_SHEET)
    For yCoord = 10 To 17
        For xCoord = 7 To 16
            If IsValidWaferCoordinate(xCoord, yCoord) Then
                Set target = WaferMapCell(wsMap, xCoord, yCoord)
                target.Value = "X" & xCoord & "Y" & yCoord
                target.Interior.Color = RGB(234, 243, 251)
                target.Font.Color = RGB(24, 59, 86)
                target.Font.Bold = True
                target.HorizontalAlignment = xlCenter
                target.VerticalAlignment = xlCenter
                target.WrapText = True
            End If
        Next xCoord
    Next yCoord
End Sub

Private Function WaferMapCell(ByVal wsMap As Worksheet, ByVal xCoord As Long, ByVal yCoord As Long) As Range
    Set WaferMapCell = wsMap.Cells(5 + (yCoord - 10), 3 + (xCoord - 7))
End Function

Private Function IsValidWaferCoordinate(ByVal xCoord As Long, ByVal yCoord As Long) As Boolean
    Select Case yCoord
        Case 10, 17
            IsValidWaferCoordinate = (xCoord >= 10 And xCoord <= 13)
        Case 11, 16
            IsValidWaferCoordinate = (xCoord >= 8 And xCoord <= 15)
        Case 12 To 15
            IsValidWaferCoordinate = (xCoord >= 7 And xCoord <= 16)
        Case Else
            IsValidWaferCoordinate = False
    End Select
End Function

Private Function TryParseChipLocation(ByVal locationText As String, ByRef xCoord As Long, ByRef yCoord As Long) As Boolean
    Dim re As Object, matches As Object
    On Error GoTo ParseFail
    Set re = CreateObject("VBScript.RegExp")
    re.Pattern = "X\s*(\d+)\s*Y\s*(\d+)"
    re.IgnoreCase = True
    re.Global = False
    If re.Test(locationText) Then
        Set matches = re.Execute(locationText)
        xCoord = CLng(matches(0).SubMatches(0))
        yCoord = CLng(matches(0).SubMatches(1))
        TryParseChipLocation = True
    End If
    Exit Function
ParseFail:
    TryParseChipLocation = False
End Function

Private Sub ApplyVminColor(ByVal target As Range, ByVal p90Label As String)
    Dim numericText As String, vminValue As Double

    target.Font.Color = RGB(37, 54, 74)
    If InStr(1, p90Label, "Hard Fail", vbTextCompare) > 0 Or Left$(p90Label, 1) = ">" Then
        target.Interior.Color = RGB(255, 179, 179)
        Exit Sub
    End If
    If InStr(1, p90Label, ChrW(&H2264), vbBinaryCompare) > 0 Then
        target.Interior.Color = RGB(183, 228, 199)
        Exit Sub
    End If

    numericText = Replace$(Replace$(p90Label, "V", ""), " ", "")
    If Not IsNumeric(numericText) Then
        target.Interior.Color = RGB(234, 243, 251)
        Exit Sub
    End If
    vminValue = CDbl(numericText)
    Select Case vminValue
        Case Is <= 0.68
            target.Interior.Color = RGB(216, 243, 220)
        Case Is <= 0.81
            target.Interior.Color = RGB(255, 243, 191)
        Case Is <= 1.2
            target.Interior.Color = RGB(255, 216, 168)
        Case Else
            target.Interior.Color = RGB(255, 179, 179)
    End Select
End Sub

Public Sub GoToVminInput()
    ThisWorkbook.Worksheets(INPUT_SHEET).Activate
End Sub

Private Sub UpdateChartForRow(ByVal sourceRow As Long)
    Dim wsIn As Worksheet, wsOut As Worksheet
    Dim helper(1 To 13, 1 To 4) As Variant
    Dim c As Long, idx As Long
    Dim total As Double, cumulative As Double
    Dim p90Rank As Long, p90Index As Long
    Dim p90Label As String, chipName As String
    Dim chartObj As ChartObject
    Dim pointIndex As Long

    Set wsIn = ThisWorkbook.Worksheets(INPUT_SHEET)
    Set wsOut = ThisWorkbook.Worksheets(CHART_SHEET)
    total = CDbl(wsIn.Cells(sourceRow, 15).Value2)
    p90Rank = CLng(wsIn.Cells(sourceRow, 16).Value2)
    p90Label = CStr(wsIn.Cells(sourceRow, 17).Value2)
    chipName = CStr(wsIn.Cells(sourceRow, 1).Value2)

    cumulative = 0
    p90Index = 0
    idx = 0
    ' Reverse the descending input columns so the chart remains low-to-high.
    For c = LAST_BIN_COL To FIRST_BIN_COL Step -1
        idx = idx + 1
        cumulative = cumulative + CDbl(Val(wsIn.Cells(sourceRow, c).Value2))
        helper(idx, 1) = CStr(wsIn.Cells(1, c).Value2)
        helper(idx, 2) = cumulative / total
        If p90Index = 0 And cumulative >= p90Rank Then p90Index = idx
        If idx = p90Index Then
            helper(idx, 3) = cumulative / total
        Else
            helper(idx, 3) = CVErr(xlErrNA)
        End If
        helper(idx, 4) = 0.9
    Next c

    wsOut.Range("P2:S14").Value = helper
    wsOut.Range("A1").Value = "Single-Chip BLK Vmin P90 Analysis - " & chipName
    wsOut.Range("A2").Value = "P90 rank " & p90Rank & " / " & CLng(total) & " BLKs; P90 Vmin = " & p90Label

    Set chartObj = wsOut.ChartObjects("chtVminP90")
    With chartObj.Chart
        .HasTitle = True
        .ChartTitle.Text = chipName & " - Vmin Cumulative Distribution (P90 = " & p90Label & ")"
        .PlotVisibleOnly = False
        On Error Resume Next
        .SeriesCollection(2).HasDataLabels = False
        On Error GoTo 0
        pointIndex = p90Index
        If pointIndex > 0 Then
            With .SeriesCollection(2).Points(pointIndex)
                .HasDataLabel = True
                .DataLabel.Text = "P90 = " & p90Label
                .DataLabel.Position = xlLabelPositionAbove
            End With
        End If
    End With

    wsOut.Activate
End Sub

Public Sub ClearP90Results()
    Dim ws As Worksheet, wsOut As Worksheet
    Dim lastRow As Long

    Set ws = ThisWorkbook.Worksheets(INPUT_SHEET)
    Set wsOut = ThisWorkbook.Worksheets(CHART_SHEET)
    lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If lastRow >= FIRST_DATA_ROW Then ws.Range(ws.Cells(FIRST_DATA_ROW, 15), ws.Cells(lastRow, 19)).ClearContents
    wsOut.Range("Q2:R14").ClearContents
    wsOut.Range("A2").Value = "選擇 Vmin Input 的 Chip 資料列，再按「計算 P90／更新圖表」。"
    ResetWaferMap
    ThisWorkbook.Worksheets(MAP_SHEET).Range("O4:O7").Value = Application.Transpose(Array(0, 64, 0, 0))
    ws.Activate
End Sub
