# Opens Blender with the TinyDirector worker running.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File blender_engine\run_blender.ps1 [-Keep] [-Blender "C:\path\to\blender.exe"]
param([switch]$Keep, [string]$Blender = "")

$root = Split-Path -Parent $PSScriptRoot
if (-not $Blender) {
    $cmd = Get-Command blender -ErrorAction SilentlyContinue
    if ($cmd) { $Blender = $cmd.Source }
}
if (-not $Blender) {
    $found = @()
    foreach ($base in @("$env:ProgramFiles\Blender Foundation", "${env:ProgramFiles(x86)}\Blender Foundation",
                        "$env:LOCALAPPDATA\Programs\Blender Foundation")) {
        if (Test-Path $base) { $found += Get-ChildItem $base -Recurse -Filter blender.exe -ErrorAction SilentlyContinue }
    }
    $steam = "${env:ProgramFiles(x86)}\Steam\steamapps\common\Blender\blender.exe"
    if (Test-Path $steam) { $found += Get-Item $steam }
    if ($found.Count -gt 0) { $Blender = ($found | Sort-Object { $_.Directory.Name } -Descending | Select-Object -First 1).FullName }
}
if (-not $Blender -or -not (Test-Path $Blender)) {
    Write-Error "Couldn't find blender.exe. Re-run with -Blender 'C:\full\path\to\blender.exe'"
    exit 1
}
Write-Host "Using $Blender"
$env:TINYDIRECTOR_ROOT = $root
$extra = @()
if ($Keep) { $extra = @("--", "--keep") }
& $Blender --python "$root\blender_engine\worker.py" @extra
