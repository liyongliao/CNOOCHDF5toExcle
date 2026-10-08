param(
    [Parameter(Mandatory = $true)][ValidateSet('x64', 'x86')][string]$Architecture,
    [int]$ExpectedBits = 0
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Installer verification must run on Windows.' }
$architectureBits = if ($Architecture -eq 'x64') { 64 } else { 32 }
if (-not $ExpectedBits) { $ExpectedBits = $architectureBits }
if ($ExpectedBits -ne $architectureBits) { throw 'ExpectedBits and Architecture disagree.' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$installer = (Resolve-Path (Join-Path $projectRoot "dist/H5ToExcelConverter-Setup-$Architecture.exe")).Path
$reports = Join-Path $projectRoot 'test-reports'
New-Item -ItemType Directory -Path $reports -Force | Out-Null
$testId = [guid]::NewGuid().ToString()
$temporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDirectory = Join-Path $temporaryRoot "h5converter-install-$testId"
$installDirectory = Join-Path $workDirectory 'application'
New-Item -ItemType Directory -Path $workDirectory -Force | Out-Null
$shortcutName = "H5ToExcelConverter-$Architecture-ci-$testId"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) "$shortcutName.lnk"
$programGroup = Join-Path ([Environment]::GetFolderPath('Programs')) $shortcutName
$startMenuShortcut = Join-Path $programGroup "$shortcutName.lnk"
$registryView = if ($Architecture -eq 'x64') { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
$registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $registryView)
$uninstallKeyPath = "Software\Microsoft\Windows\CurrentVersion\Uninstall\H5ToExcelConverter-$Architecture-ci-$testId`_is1"
$summary = [ordered]@{ status = 'failed'; architecture = $ExpectedBits; installer = $installer; tests = @(); uninstall_passed = $false; shortcuts_passed = $false }
$failure = $null

function Invoke-CheckedProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutMs = 120000, [switch]$BundledOnly) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.Arguments = $Arguments -join ' '
    $startInfo.UseShellExecute = $false
    $startInfo.WorkingDirectory = Split-Path -Parent $Executable
    if ($BundledOnly) {
        # Verify independence from setup-python and all external Python search paths.
        $startInfo.EnvironmentVariables.Remove('PYTHONHOME')
        $startInfo.EnvironmentVariables.Remove('PYTHONPATH')
        $startInfo.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    }
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $process = [System.Diagnostics.Process]::Start($startInfo)
    try {
        if (-not $process.WaitForExit($TimeoutMs)) {
            $process.Kill()
            throw "Process timed out: $Executable"
        }
        $process.Refresh()
        if ($process.ExitCode -ne 0) { throw "Process failed ($($process.ExitCode)): $Executable" }
        $stopwatch.Stop()
        return [math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
    } finally {
        $process.Dispose()
    }
}

try {
    [void](Invoke-CheckedProcess $installer @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/SP-',
        "/CITEST=$testId", '/TASKS=desktopicon', ('/DIR="{0}"' -f $installDirectory),
        ('/LOG="{0}"' -f (Join-Path $reports "installer-install-$Architecture.log"))
    ))
    $installedExecutable = Join-Path $installDirectory 'H5ToExcelConverter.exe'
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) { throw 'Installed application EXE is missing.' }
    if (-not (Test-Path -LiteralPath (Join-Path $installDirectory '_internal') -PathType Container)) { throw 'Installed runtime directory is missing.' }
    $reader = [System.IO.BinaryReader]::new([System.IO.File]::OpenRead($installedExecutable))
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { throw 'Installed EXE has no DOS header.' }
        [void]$reader.BaseStream.Seek(0x3C, [System.IO.SeekOrigin]::Begin)
        $peOffset = $reader.ReadInt32()
        [void]$reader.BaseStream.Seek($peOffset, [System.IO.SeekOrigin]::Begin)
        if ($reader.ReadUInt32() -ne 0x00004550) { throw 'Installed EXE has no PE header.' }
        $expectedMachine = if ($Architecture -eq 'x64') { 0x8664 } else { 0x014C }
        if ($reader.ReadUInt16() -ne $expectedMachine) { throw 'Installed EXE has the wrong machine architecture.' }
    } finally {
        $reader.Dispose()
    }
    $uninstallKey = $registry.OpenSubKey($uninstallKeyPath)
    if ($null -eq $uninstallKey) { throw 'Current-user uninstall registration is missing.' }
    $uninstallKey.Dispose()
    $shell = New-Object -ComObject WScript.Shell
    try {
        foreach ($shortcut in @($desktopShortcut, $startMenuShortcut)) {
            if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) { throw "Installed shortcut is missing: $shortcut" }
            $link = $shell.CreateShortcut($shortcut)
            if ($link.TargetPath -ne $installedExecutable) { throw "Shortcut targets the wrong application: $shortcut" }
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($link)
        }
    } finally {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
    $summary.shortcuts_passed = $true
    foreach ($mode in @('smoke-test', 'self-test')) {
        $reportPath = Join-Path $reports "installed-$Architecture-$mode.json"
        $wallSeconds = Invoke-CheckedProcess $installedExecutable @("--$mode", '--report', ('"{0}"' -f $reportPath)) -BundledOnly
        if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw "Installed $mode wrote no report." }
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
        if ($report.status -ne 'passed') { throw "Installed $mode failed: $($report.error)" }
        if ($report.architecture -ne $ExpectedBits) { throw "Installed $mode reported the wrong architecture." }
        if ($mode -eq 'smoke-test') {
            if ('scientific_libraries_loaded' -notin $report.PSObject.Properties.Name) { throw 'Installed smoke test did not report early imports.' }
            if ($report.scientific_libraries_loaded) { throw 'Scientific libraries loaded before the installed window appeared.' }
        }
        $report | Add-Member -NotePropertyName process_wall_seconds -NotePropertyValue $wallSeconds -Force
        $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
        $summary.tests += $mode
        Write-Host "Installed $mode passed ($ExpectedBits bit, $wallSeconds seconds)"
    }
} catch {
    $failure = $_
    $summary.error = $_.Exception.Message
} finally {
    try {
        $uninstaller = Join-Path $installDirectory 'unins000.exe'
        if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
            [void](Invoke-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                ('/LOG="{0}"' -f (Join-Path $reports "installer-uninstall-$Architecture.log"))))
            if (Test-Path -LiteralPath (Join-Path $installDirectory 'H5ToExcelConverter.exe')) { throw 'Uninstaller left the application EXE behind.' }
            $remainingKey = $registry.OpenSubKey($uninstallKeyPath)
            if ($null -ne $remainingKey) {
                $remainingKey.Dispose()
                throw 'Uninstaller left the CI uninstall registry entry behind.'
            }
            if ((Test-Path -LiteralPath $desktopShortcut) -or (Test-Path -LiteralPath $startMenuShortcut)) { throw 'Uninstaller left application shortcuts behind.' }
            $summary.uninstall_passed = $true
        } else {
            throw 'Installed uninstaller is missing.'
        }
    } catch {
        $summary.uninstall_error = $_.Exception.Message
        if ($null -eq $failure) { $failure = $_ }
    } finally {
        # These paths and key include our fresh GUID, so cleanup cannot touch another installation.
        if (Test-Path -LiteralPath $desktopShortcut) { Remove-Item -LiteralPath $desktopShortcut -Force }
        if (Test-Path -LiteralPath $programGroup) { Remove-Item -LiteralPath $programGroup -Recurse -Force }
        $registry.DeleteSubKeyTree($uninstallKeyPath, $false)
        $registry.Dispose()
        Remove-Item -LiteralPath $workDirectory -Recurse -Force -ErrorAction SilentlyContinue
        if ($null -eq $failure) { $summary.status = 'passed' }
        $summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $reports "installer-verification-$Architecture.json") -Encoding utf8
    }
}
if ($null -ne $failure) { throw $failure }
Write-Host "Installer verification passed for $Architecture; temporary installation was uninstalled."
