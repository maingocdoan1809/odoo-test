param(
    [Parameter(Mandatory=$true)][string]$WorkbookPath,
    [Parameter(Mandatory=$true)][string]$ReportPath,
    [Parameter(Mandatory=$true)][string]$PatchPath
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$patch = Get-Content -LiteralPath $PatchPath -Raw -Encoding UTF8 | ConvertFrom-Json
$excel = [Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application')
$book = $null
foreach ($openBook in $excel.Workbooks) {
    if ([StringComparer]::OrdinalIgnoreCase.Equals([string]$openBook.FullName, $WorkbookPath)) {
        $book = $openBook
        break
    }
}
if ($null -eq $book) { throw 'The testcase workbook is not open in the active Excel instance.' }
if ($book.ReadOnly) { throw 'The testcase workbook is open read-only.' }
$sheet = $book.Worksheets.Item([string]$patch.sheet)
foreach ($row in $patch.rows) {
    $currentId = [string]$sheet.Cells.Item([int]$row.row, [int]$patch.id_column).Value2()
    if ($currentId.Trim() -cne [string]$row.id) { throw "The testcase ID at row $($row.row) changed." }
}

$alerts = $excel.DisplayAlerts
$security = $excel.AutomationSecurity
$staged = $null
try {
    $excel.DisplayAlerts = $false
    $excel.AutomationSecurity = 3
    $staged = $excel.Workbooks.Open($ReportPath, 0, $true)
    $report = $staged.Worksheets.Item([string]$patch.sheet)
    foreach ($col in $patch.new_columns) {
        $current = [string]$sheet.Cells.Item([int]$patch.header, [int]$col).Value2()
        $expected = [string]$report.Cells.Item([int]$patch.header, [int]$col).Value2()
        if ($current -and $current -ne $expected) { throw "Column $col is already in use." }
    }
    $existing = @{}
    foreach ($openSheet in $book.Worksheets) { $existing[[string]$openSheet.Name] = $openSheet }
    foreach ($name in $patch.evidence) {
        $incoming = $staged.Worksheets.Item([string]$name)
        $previous = $existing[$name]
        if ($null -ne $previous) {
            if ($previous.CustomProperties.Count -eq 0 -or
                $previous.CustomProperties.Item(1).Name -ne 'OQA_Session' -or
                $previous.CustomProperties.Item(1).Value -ne $patch.session) {
                throw "An evidence sheet name is already in use: $name."
            }
            $previous.Delete()
        }
        $incoming.Copy([Type]::Missing, $book.Worksheets.Item($book.Worksheets.Count))
        $created = $book.Worksheets.Item($book.Worksheets.Count)
        $null = $created.CustomProperties.Add('OQA_Session', [string]$patch.session)
    }
    foreach ($cell in $patch.cells) {
        $row = [int]$cell.row
        $col = [int]$cell.col
        $from = $report.Cells.Item($row, $col)
        $to = $sheet.Cells.Item($row, $col)
        $to.Value2 = $from.Value2()
        $to.Interior.Color = $from.Interior.Color
        $to.Font.Color = $from.Font.Color
        if ($from.Hyperlinks.Count) {
            $to.Hyperlinks.Delete()
            $null = $sheet.Hyperlinks.Add($to, '', [string]$from.Hyperlinks.Item(1).SubAddress)
        }
    }
    $book.Save()
} finally {
    if ($null -ne $staged) { $staged.Close($false) }
    $excel.AutomationSecurity = $security
    $excel.DisplayAlerts = $alerts
}
