Attribute VB_Name = "MultiCellImportCleaner"
Option Explicit

' Import and cleanup helper for 6T multi-cell Vt/Idsat workbooks.
' Only worksheets named as a voltage (for example 0.685v, 0.90v, 1.20v)
' are copied and normalized. Data is processed in memory as a 2-D array.

Private Const FIRST_DATA_ROW As Long = 2
Private Const LAST_DATA_COLUMN As Long = 14       ' A:N

Public Sub ImportMultiCellFile()
    Dim picker As Object
    Dim sourcePath As String
    Dim sourceBook As Workbook
    Dim sourceSheet As Worksheet
    Dim targetBook As Workbook
    Dim importedCount As Long
    Dim extension As String

    On Error GoTo Fail
    Application.ScreenUpdating = False
    Application.EnableEvents = False

    Set targetBook = ThisWorkbook
    Set picker = Application.FileDialog(3)          ' msoFileDialogFilePicker
    With picker
        .Title = "Select multi-cell CSV or Excel file"
        .AllowMultiSelect = False
        .Filters.Clear
        .Filters.Add "CSV or Excel", "*.csv;*.xlsx;*.xlsm;*.xls"
        If .Show <> -1 Then GoTo CleanExit
        sourcePath = CStr(.SelectedItems(1))
    End With

    extension = LCase$(Mid$(sourcePath, InStrRev(sourcePath, ".") + 1))
    Set sourceBook = Workbooks.Open(Filename:=sourcePath, ReadOnly:=True, UpdateLinks:=False)

    If extension = "csv" Then
        Set sourceSheet = sourceBook.Worksheets(1)
        Dim csvVoltage As String
        csvVoltage = VoltageNameFromFilename(sourcePath)
        If Len(csvVoltage) = 0 Then
            MsgBox "CSV filename must contain a voltage, for example 0.90v.csv.", vbExclamation
            GoTo CleanExit
        End If
        CopySheetData sourceSheet, GetOrCreateSheet(targetBook, csvVoltage)
        importedCount = 1
    Else
        Dim ws As Worksheet
        For Each ws In sourceBook.Worksheets
            If IsVoltageSheetName(ws.Name) Then
                CopySheetData ws, GetOrCreateSheet(targetBook, ws.Name)
                importedCount = importedCount + 1
            End If
        Next ws
    End If

    NormalizeVoltageSheets targetBook
    If importedCount = 0 Then
        MsgBox "No voltage-named worksheets were found. Nothing was imported.", vbInformation
    Else
        MsgBox CStr(importedCount) & " voltage sheet(s) imported and cleaned.", vbInformation
    End If

CleanExit:
    On Error Resume Next
    If Not sourceBook Is Nothing Then sourceBook.Close SaveChanges:=False
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    Exit Sub

Fail:
    MsgBox "Import failed: " & Err.Description, vbCritical
    Resume CleanExit
End Sub

Public Sub NormalizeVoltageSheetsInCurrentWorkbook()
    On Error GoTo Fail
    Application.ScreenUpdating = False
    Application.EnableEvents = False
    NormalizeVoltageSheets ThisWorkbook
    MsgBox "Voltage sheets cleaned.", vbInformation
CleanExit:
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    Exit Sub
Fail:
    MsgBox "Cleanup failed: " & Err.Description, vbCritical
    Resume CleanExit
End Sub

Private Sub NormalizeVoltageSheets(ByVal book As Workbook)
    Dim ws As Worksheet
    For Each ws In book.Worksheets
        If IsVoltageSheetName(ws.Name) Then NormalizeOneSheet ws
    Next ws
End Sub

Private Sub NormalizeOneSheet(ByVal ws As Worksheet)
    Dim lastRow As Long
    Dim inputData As Variant
    Dim outputData() As Variant
    Dim consumed() As Boolean
    Dim rowIndex As Long, colIndex As Long, outputRow As Long
    Dim nextPayloadRow As Long

    lastRow = LastUsedRowInData(ws)
    If lastRow < FIRST_DATA_ROW Then Exit Sub

    inputData = ws.Range(ws.Cells(1, 1), ws.Cells(lastRow, LAST_DATA_COLUMN)).Value2
    ReDim outputData(1 To lastRow, 1 To LAST_DATA_COLUMN)
    ReDim consumed(1 To lastRow)

    For colIndex = 1 To LAST_DATA_COLUMN
        outputData(1, colIndex) = inputData(1, colIndex)
    Next colIndex
    outputRow = FIRST_DATA_ROW

    For rowIndex = FIRST_DATA_ROW To lastRow
        If consumed(rowIndex) Then GoTo ContinueRow

        Dim hasIdentity As Boolean
        Dim hasDeviceData As Boolean
        hasIdentity = HasValue(inputData(rowIndex, 1)) Or HasValue(inputData(rowIndex, 2))
        hasDeviceData = HasDataInDeviceColumns(inputData, rowIndex)

        If hasIdentity And Not hasDeviceData Then
            nextPayloadRow = FindNextUnidentifiedPayload(inputData, consumed, rowIndex + 1, lastRow)
            If nextPayloadRow > 0 Then
                outputData(outputRow, 1) = inputData(rowIndex, 1)
                outputData(outputRow, 2) = inputData(rowIndex, 2)
                For colIndex = 3 To LAST_DATA_COLUMN
                    outputData(outputRow, colIndex) = inputData(nextPayloadRow, colIndex)
                Next colIndex
                consumed(nextPayloadRow) = True
                outputRow = outputRow + 1
            End If
        ElseIf hasIdentity And hasDeviceData Then
            For colIndex = 1 To LAST_DATA_COLUMN
                outputData(outputRow, colIndex) = inputData(rowIndex, colIndex)
            Next colIndex
            outputRow = outputRow + 1
        End If
        ' Rows with blank A/B and populated C:N, plus completely blank rows,
        ' are intentionally omitted from the compacted output.
ContinueRow:
    Next rowIndex

    ws.Range(ws.Cells(1, 1), ws.Cells(lastRow, LAST_DATA_COLUMN)).ClearContents
    ws.Range(ws.Cells(1, 1), ws.Cells(lastRow, LAST_DATA_COLUMN)).Value2 = outputData
    If outputRow <= lastRow Then
        ws.Range(ws.Cells(outputRow, 1), ws.Cells(lastRow, LAST_DATA_COLUMN)).ClearContents
    End If
End Sub

Private Function FindNextUnidentifiedPayload(ByRef data As Variant, ByRef consumed() As Boolean, _
                                             ByVal startRow As Long, ByVal lastRow As Long) As Long
    Dim rowIndex As Long
    For rowIndex = startRow To lastRow
        If Not consumed(rowIndex) Then
            If Not HasValue(data(rowIndex, 1)) And Not HasValue(data(rowIndex, 2)) Then
                If HasDataInDeviceColumns(data, rowIndex) Then
                    FindNextUnidentifiedPayload = rowIndex
                    Exit Function
                End If
            End If
        End If
    Next rowIndex
    FindNextUnidentifiedPayload = 0
End Function

Private Function HasDataInDeviceColumns(ByRef data As Variant, ByVal rowIndex As Long) As Boolean
    Dim colIndex As Long
    For colIndex = 3 To LAST_DATA_COLUMN
        If HasValue(data(rowIndex, colIndex)) Then
            HasDataInDeviceColumns = True
            Exit Function
        End If
    Next colIndex
    HasDataInDeviceColumns = False
End Function

Private Function HasValue(ByVal value As Variant) As Boolean
    If IsError(value) Or IsEmpty(value) Then Exit Function
    HasValue = (Len(Trim$(CStr(value))) > 0)
End Function

Private Function LastUsedRowInData(ByVal ws As Worksheet) As Long
    Dim lastCell As Range
    Set lastCell = ws.Range("A:N").Find(What:="*", After:=ws.Range("A1"), _
                                         LookIn:=xlFormulas, LookAt:=xlPart, _
                                         SearchOrder:=xlByRows, SearchDirection:=xlPrevious)
    If lastCell Is Nothing Then
        LastUsedRowInData = 1
    Else
        LastUsedRowInData = lastCell.Row
    End If
End Function

Private Function IsVoltageSheetName(ByVal sheetName As String) As Boolean
    Dim re As Object
    Set re = CreateObject("VBScript.RegExp")
    re.Pattern = "^[0-9]+([.][0-9]+)?[vV]$"
    re.IgnoreCase = True
    IsVoltageSheetName = re.Test(Trim$(sheetName))
End Function

Private Function VoltageNameFromFilename(ByVal filePath As String) As String
    Dim re As Object, matches As Object
    Set re = CreateObject("VBScript.RegExp")
    re.Pattern = "([0-9]+([.][0-9]+)?)[vV]"
    re.IgnoreCase = True
    Set matches = re.Execute(Mid$(filePath, InStrRev(filePath, Application.PathSeparator) + 1))
    If matches.Count > 0 Then VoltageNameFromFilename = matches(0).Value
End Function

Private Function GetOrCreateSheet(ByVal book As Workbook, ByVal sheetName As String) As Worksheet
    On Error Resume Next
    Set GetOrCreateSheet = book.Worksheets(sheetName)
    On Error GoTo 0
    If GetOrCreateSheet Is Nothing Then
        Set GetOrCreateSheet = book.Worksheets.Add(After:=book.Worksheets(book.Worksheets.Count))
        GetOrCreateSheet.Name = sheetName
    End If
End Function

Private Sub CopySheetData(ByVal sourceSheet As Worksheet, ByVal targetSheet As Worksheet)
    Dim lastRow As Long, lastColumn As Long
    lastRow = sourceSheet.Cells.Find(What:="*", After:=sourceSheet.Range("A1"), _
                                     LookIn:=xlFormulas, LookAt:=xlPart, _
                                     SearchOrder:=xlByRows, SearchDirection:=xlPrevious).Row
    lastColumn = sourceSheet.Cells.Find(What:="*", After:=sourceSheet.Range("A1"), _
                                        LookIn:=xlFormulas, LookAt:=xlPart, _
                                        SearchOrder:=xlByColumns, SearchDirection:=xlPrevious).Column
    If lastRow < 1 Then Exit Sub
    If lastColumn > LAST_DATA_COLUMN Then lastColumn = LAST_DATA_COLUMN
    targetSheet.Range("A:N").ClearContents
    targetSheet.Range(targetSheet.Cells(1, 1), targetSheet.Cells(lastRow, lastColumn)).Value2 = _
        sourceSheet.Range(sourceSheet.Cells(1, 1), sourceSheet.Cells(lastRow, lastColumn)).Value2
End Sub
