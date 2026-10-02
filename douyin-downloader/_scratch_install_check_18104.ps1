$ErrorActionPreference = 'Stop'
$root = 'F:\18752\Documents\douyin download\douyin-downloader'
$distExe = (Get-ChildItem -LiteralPath (Join-Path $root 'dist\1.8.10.4') -Filter '*.exe' | Select-Object -First 1).FullName
$setup = (Get-ChildItem -LiteralPath (Join-Path $root 'dist') -Filter '*1.8.10.4-setup.exe' | Select-Object -First 1).FullName
$dir = Join-Path $env:TEMP 'vdl18104'
$out = 'F:\18752\Documents\douyin download\_scratch_x_probe\_install_check_18104.txt'
$log = @()
$log += "dist exe : $distExe"
$log += "setup    : $setup"
if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }

& $setup /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="$dir" /MERGETASKS="!desktopicon"
$installed = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    $c = Get-ChildItem -LiteralPath $dir -Filter '*.exe' -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -notlike 'unins*' } | Select-Object -First 1
    if ($c) { $installed = $c.FullName; break }
}
if (-not $installed) {
    $log += 'INSTALL FAILED: installed exe not found after 120s'
    Set-Content -LiteralPath $out -Value $log -Encoding UTF8
    exit 1
}
$log += "installed: $installed"
$h1 = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
$h2 = (Get-FileHash -LiteralPath $distExe -Algorithm SHA256).Hash
$log += "sha256 match vs dist: " + ($h1 -eq $h2)
$log += "sha256 = $h1"

Start-Process -FilePath $installed
Start-Sleep -Seconds 14
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $installed }
if ($running) {
    foreach ($p in $running) {
        $log += ('run: pid={0} {1}MB responding={2}' -f $p.Id, [int]($p.WorkingSet64 / 1MB), $p.Responding)
    }
} else {
    $log += 'run: PROCESS NOT ALIVE'
}
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $installed } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$unins = Join-Path $dir 'unins000.exe'
if (Test-Path -LiteralPath $unins) {
    & $unins /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (-not (Test-Path -LiteralPath $dir)) { break }
    }
}
$log += 'dir gone after uninstall: ' + (-not (Test-Path -LiteralPath $dir))
if (Test-Path -LiteralPath $dir) { $log += 'leftover: ' + ((Get-ChildItem -LiteralPath $dir -Force | Select-Object -ExpandProperty Name) -join ', ') }
Set-Content -LiteralPath $out -Value $log -Encoding UTF8
'ok'
