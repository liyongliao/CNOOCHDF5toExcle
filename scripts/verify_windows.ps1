param([Parameter(Mandatory = $true)][int]$ExpectedBits)
$ErrorActionPreference = 'Stop'
$executable = (Resolve-Path 'dist/H5ToExcelConverter/H5ToExcelConverter.exe').Path
$reports = Join-Path $PWD 'test-reports'
New-Item -ItemType Directory -Path $reports -Force | Out-Null

foreach ($mode in @('smoke-test', 'self-test')) {
    $reportPath = Join-Path $reports "packaged-$mode.json"
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $executable -ArgumentList @("--$mode", '--report', "`"$reportPath`"") -PassThru
    if (-not $process.WaitForExit(120000)) {
        Stop-Process -Id $process.Id -Force
        throw "Packaged $mode timed out"
    }
    $process.Refresh()
    $stopwatch.Stop()
    if ($process.ExitCode -ne 0) { throw "Packaged $mode failed with exit code $($process.ExitCode)" }
    if (-not (Test-Path $reportPath)) { throw "Packaged $mode did not write a report" }
    $report = Get-Content $reportPath -Raw | ConvertFrom-Json
    if ($report.status -ne 'passed') { throw "Packaged $mode failed: $($report.error)" }
    if ($report.architecture -ne $ExpectedBits) { throw "Packaged application has the wrong architecture" }
    if ($mode -eq 'smoke-test' -and $report.scientific_libraries_loaded) {
        throw 'Scientific libraries loaded before the smoke-test window closed'
    }
    $report | Add-Member -NotePropertyName process_wall_seconds -NotePropertyValue ([math]::Round($stopwatch.Elapsed.TotalSeconds, 3))
    $report | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding utf8
    Write-Host "$mode passed: $($report.seconds) seconds ($ExpectedBits bit)"
}

# Keep an actual packaged-window preview with the reports for visual review.
# Only the application window is captured, on the GitHub Windows worker.
$previewProcess = Start-Process -FilePath $executable -PassThru
try {
    Add-Type -AssemblyName System.Drawing
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public class ConverterWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hwnd, out Rect rectangle);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hwnd, IntPtr destination, uint flags);
}
'@
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 100
        $previewProcess.Refresh()
    } while ($previewProcess.MainWindowHandle -eq [IntPtr]::Zero -and [DateTime]::UtcNow -lt $deadline -and -not $previewProcess.HasExited)
    if ($previewProcess.MainWindowHandle -eq [IntPtr]::Zero) { throw 'Preview window not found' }
    Start-Sleep -Milliseconds 1000
    $rectangle = New-Object ConverterWindowCapture+Rect
    [void][ConverterWindowCapture]::GetWindowRect($previewProcess.MainWindowHandle, [ref]$rectangle)
    $bitmap = [System.Drawing.Bitmap]::new(($rectangle.Right - $rectangle.Left), ($rectangle.Bottom - $rectangle.Top))
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $device = $graphics.GetHdc()
    try {
        $captured = [ConverterWindowCapture]::PrintWindow($previewProcess.MainWindowHandle, $device, 0)
    } finally {
        $graphics.ReleaseHdc($device)
    }
    if (-not $captured) { throw 'Window preview capture unavailable' }
    $bitmap.Save((Join-Path $reports 'desktop-preview.png'), [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
} catch {
    Write-Warning "Preview capture skipped: $_"
} finally {
    if (-not $previewProcess.HasExited) {
        [void]$previewProcess.CloseMainWindow()
        if (-not $previewProcess.WaitForExit(5000)) { Stop-Process -Id $previewProcess.Id -Force }
    }
}
