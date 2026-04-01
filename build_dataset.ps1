# Build RandomX shared library from source (Windows)
# Requires: Git, CMake, Visual Studio Build Tools (or MinGW)
# Builds the latest RandomX with v2 support
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RxDir = Join-Path $ScriptDir "RandomX"

Write-Host "=== Building RandomX DLL (with v2 support) ==="

# Clone or update
if (-not (Test-Path $RxDir)) {
    Write-Host "Cloning latest RandomX (with v2 support)..."
    git clone https://github.com/tevador/RandomX.git $RxDir
} else {
    Write-Host "Updating existing RandomX source..."
    Set-Location $RxDir
    git fetch origin
    git checkout master
    git pull origin master
    Set-Location $ScriptDir
}

# Build
$BuildDir = Join-Path $RxDir "build"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Set-Location $BuildDir

Write-Host "Running cmake..."
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON

Write-Host "Compiling..."
cmake --build . --config Release

# Copy DLL to project root
$dll = Get-ChildItem -Path $BuildDir -Recurse -Filter "randomx.dll" | Select-Object -First 1
if ($dll) {
    Copy-Item -Force $dll.FullName (Join-Path $ScriptDir "randomx.dll")
    Write-Host "`n=== Done ==="
    Write-Host "DLL copied to: $(Join-Path $ScriptDir 'randomx.dll')"
} else {
    $dll = Get-ChildItem -Path $BuildDir -Recurse -Filter "librandomx.dll" | Select-Object -First 1
    if ($dll) {
        Copy-Item -Force $dll.FullName (Join-Path $ScriptDir "librandomx.dll")
        Write-Host "`n=== Done ==="
        Write-Host "DLL copied to: $(Join-Path $ScriptDir 'librandomx.dll')"
    } else {
        Write-Host "WARNING: Could not find built DLL"
        Write-Host "Check $BuildDir for the output"
    }
}

Set-Location $ScriptDir

Write-Host ""
Write-Host "=== RandomX v2 Support ==="
Write-Host "This build includes RandomX v2 (rx/2) algorithm support."
Write-Host "The miner will auto-negotiate rx/0 or rx/2 with your pool."
Write-Host ""
Write-Host "=== Windows Large Pages Setup ==="
Write-Host "1. Run gpedit.msc"
Write-Host "2. Computer Config > Windows Settings > Security Settings > Local Policies > User Rights Assignment"
Write-Host "3. Add your user to 'Lock pages in memory'"
Write-Host "4. Restart your computer"
Write-Host ""
Write-Host "=== Run the miner ==="
Write-Host "python tpu-tensor.py --config dataset-config.json"
