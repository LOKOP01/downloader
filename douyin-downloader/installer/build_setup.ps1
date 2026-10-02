# Rebuild the Windows setup.exe with Inno Setup.
# Requires: dist\ exe already built by PyInstaller.
$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$root = Split-Path -Parent $here
$dist = Join-Path $root 'dist'
$iss = Join-Path $here 'setup.iss'
$raw = Get-Content -LiteralPath $iss -Raw

# 版本号与 exe 名都从 setup.iss 里取（setup.iss 是唯一来源）。
# 不在本脚本里硬编码中文文件名：PowerShell 5.1 会按系统 ANSI 码页读取
# 无 BOM 的 .ps1，中文会变成乱码，进而 Test-Path 找不到 exe。
$versionMatch = [regex]::Match($raw, '#define MyAppVersion "([^"]+)"')
if (-not $versionMatch.Success) { throw 'Missing MyAppVersion in setup.iss.' }
$version = $versionMatch.Groups[1].Value

$exeNameMatch = [regex]::Match($raw, '#define MyAppExeName "([^"]+)"')
if (-not $exeNameMatch.Success) { throw 'Missing MyAppExeName in setup.iss.' }
$exeName = $exeNameMatch.Groups[1].Value

$exe = Join-Path (Join-Path $dist $version) $exeName
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Missing $exe. Run PyInstaller with --distpath dist/$version first."
}

$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path -LiteralPath $iscc)) {
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($pf86) { $iscc = Join-Path $pf86 'Inno Setup 6\ISCC.exe' }
}
if (-not (Test-Path -LiteralPath $iscc)) {
    $iscc = Join-Path ${env:ProgramFiles} 'Inno Setup 6\ISCC.exe'
}
if (-not (Test-Path -LiteralPath $iscc)) {
    throw 'Inno Setup not found. Install: winget install --id JRSoftware.InnoSetup -e'
}

Write-Host ('ISCC: ' + $iscc)
Write-Host ('Script: ' + $iss)
& $iscc /Qp $iss
if ($LASTEXITCODE -ne 0) { throw ('Inno Setup compile failed, exit ' + $LASTEXITCODE) }

$setup = Get-ChildItem -LiteralPath $dist -Filter '*-setup.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setup) {
    $mb = [math]::Round($setup.Length / 1MB, 1)
    Write-Host ('OK: ' + $setup.FullName + ' (' + $mb + ' MB)')
}
