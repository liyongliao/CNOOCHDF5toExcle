param(
    [Parameter(Mandatory = $true)][ValidateSet('x64', 'x86')][string]$Architecture,
    [string]$Version = ''
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The installer must be compiled on Windows.' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceDirectory = (Resolve-Path (Join-Path $projectRoot 'dist/H5ToExcelConverter')).Path
$desktopExecutable = Join-Path $sourceDirectory 'H5ToExcelConverter.exe'
if (-not (Test-Path -LiteralPath $desktopExecutable -PathType Leaf)) { throw 'Build the standalone desktop bundle first.' }
if (-not $Version) {
    $Version = (Get-Item -LiteralPath $desktopExecutable).VersionInfo.ProductVersion
    if (-not $Version) { $Version = '2.1.0' }
}
if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') { throw 'Version must be a numeric Windows application version.' }

# Verified against both the immutable release asset digest and official .issig file:
# https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3
# https://files.jrsoftware.org/is/6/innosetup-6.7.3.exe.issig
$innoVersion = '6.7.3'
$innoUrl = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe'
$innoSha256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
$languageUrl = 'https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl'
$languageSha256 = '7d544b9bb1d142cfa11f2e5d3cc8abe2e55f8e066c5124e3772675aa236e1278'
$temporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDirectory = Join-Path $temporaryRoot ('h5converter-inno-' + [guid]::NewGuid().ToString('N'))
$compilerDirectory = Join-Path $workDirectory 'compiler'
$reports = Join-Path $projectRoot 'test-reports'
New-Item -ItemType Directory -Path $workDirectory, $reports -Force | Out-Null
$buildReport = [ordered]@{ status = 'failed'; architecture = $Architecture; inno_version = $innoVersion; inno_download = $innoUrl; inno_sha256 = $innoSha256 }

function Get-VerifiedDownload([string]$Uri, [string]$Destination, [string]$ExpectedSha256) {
    Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
    $actualHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ExpectedSha256) { throw "SHA-256 mismatch for $Uri" }
}

try {
    $downloadedInstaller = Join-Path $workDirectory "innosetup-$innoVersion.exe"
    Get-VerifiedDownload $innoUrl $downloadedInstaller $innoSha256
    # The official portable mode skips associations, shortcuts and uninstall registration.
    # Fixed-version source: https://github.com/jrsoftware/issrc/blob/is-6_7_3/isportable.iss
    $extract = Start-Process -FilePath $downloadedInstaller -ArgumentList @(
        '/PORTABLE=1', '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
        ('/DIR="{0}"' -f $compilerDirectory)
    ) -PassThru
    if (-not $extract.WaitForExit(120000)) {
        Stop-Process -Id $extract.Id -Force
        throw 'Inno Setup portable extraction timed out.'
    }
    $extract.Refresh()
    if ($extract.ExitCode -ne 0) { throw "Inno Setup portable extraction failed: $($extract.ExitCode)" }
    $compiler = Join-Path $compilerDirectory 'ISCC.exe'
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'The verified compiler was not extracted.' }
    $languagePath = Join-Path $compilerDirectory 'Languages/ChineseSimplified.isl'
    Get-VerifiedDownload $languageUrl $languagePath $languageSha256
    $outputDirectory = Join-Path $projectRoot 'dist'
    & $compiler '/Qp' "/DArchitecture=$Architecture" "/DSourceDir=$sourceDirectory" "/DAppVersion=$Version" "/O$outputDirectory" (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed: $LASTEXITCODE" }
    $outputFile = Join-Path $outputDirectory "H5ToExcelConverter-Setup-$Architecture.exe"
    if (-not (Test-Path -LiteralPath $outputFile -PathType Leaf)) { throw 'Installer compilation produced no EXE.' }
    $buildReport.status = 'passed'
    $buildReport.version = $Version
    $buildReport.installer = $outputFile
    $buildReport.bytes = (Get-Item -LiteralPath $outputFile).Length
    $buildReport.sha256 = (Get-FileHash -LiteralPath $outputFile -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Built $outputFile ($($buildReport.bytes) bytes)"
} catch {
    $buildReport.error = $_.Exception.Message
    throw
} finally {
    $buildReport | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $reports "installer-build-$Architecture.json") -Encoding utf8
    Remove-Item -LiteralPath $workDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
