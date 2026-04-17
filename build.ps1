# =============================================================================
# YTD Clone - Windows build script
# =============================================================================
# Run this in PowerShell from the project folder:
#
#     powershell -ExecutionPolicy Bypass -File .\build.ps1
#
# Output: .\dist\YTDClone.exe   (single file, ffmpeg bundled inside)
#
# Requirements on your machine:
#   * Python 3.10 - 3.13 (3.14 works but some wheels lag; 3.12 is the safest)
#   * Internet access (pulls the latest yt-dlp and ffmpeg)
# =============================================================================

$ErrorActionPreference = "Stop"

# --- Config -----------------------------------------------------------------
$AppName   = "YTDClone"
$Entry     = "ytd_clone.py"
$Icon      = ""   # optional: path to .ico file for the exe icon; leave "" if none
$BuildRoot = "build"
$DistDir   = "dist"
$VenvDir   = ".venv-build"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# --- Sanity checks ----------------------------------------------------------
if (-not (Test-Path $Entry)) {
    Write-Host "ERROR: Can't find $Entry in $(Get-Location)." -ForegroundColor Red
    Write-Host "Run this script from the folder that contains $Entry." -ForegroundColor Red
    exit 1
}

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) {
    Write-Host "ERROR: 'python' is not on PATH." -ForegroundColor Red
    exit 1
}
Write-Host "==> Using Python: $($py.Source)"
python --version

# --- Clean previous output --------------------------------------------------
Write-Host "==> Cleaning previous build output..."
Remove-Item -Recurse -Force $BuildRoot, $DistDir -ErrorAction SilentlyContinue
Remove-Item -Force "$AppName.spec" -ErrorAction SilentlyContinue

# --- Fresh venv -------------------------------------------------------------
# A clean venv keeps the exe lean. Without this, PyInstaller drags in
# whatever random packages happen to live in your global site-packages.
if (Test-Path $VenvDir) {
    Write-Host "==> Reusing existing venv at $VenvDir"
} else {
    Write-Host "==> Creating venv at $VenvDir"
    python -m venv $VenvDir
}
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

Write-Host "==> Upgrading pip and installing build deps"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPip install --upgrade --quiet yt-dlp pyinstaller

# --- Download + extract ffmpeg ---------------------------------------------
$FfmpegStage = Join-Path $BuildRoot "ffmpeg"
New-Item -ItemType Directory -Force -Path $FfmpegStage | Out-Null

$FfmpegZip = Join-Path $FfmpegStage "ffmpeg.zip"
if (-not (Test-Path (Join-Path $FfmpegStage "ffmpeg.exe"))) {
    Write-Host "==> Downloading ffmpeg from $FfmpegUrl"
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip -UseBasicParsing

    Write-Host "==> Extracting ffmpeg"
    $ExtractTmp = Join-Path $FfmpegStage "_extract"
    Expand-Archive -Path $FfmpegZip -DestinationPath $ExtractTmp -Force

    # The zip contains a single top-level folder like "ffmpeg-7.x-essentials_build".
    # Pull ffmpeg.exe + ffprobe.exe out of its bin/ and flatten into $FfmpegStage.
    $Bin = Get-ChildItem -Path $ExtractTmp -Recurse -Filter "ffmpeg.exe" |
           Select-Object -First 1
    if (-not $Bin) {
        Write-Host "ERROR: ffmpeg.exe not found in downloaded archive." -ForegroundColor Red
        exit 1
    }
    Copy-Item $Bin.FullName        (Join-Path $FfmpegStage "ffmpeg.exe")  -Force
    $Probe = Join-Path $Bin.Directory.FullName "ffprobe.exe"
    if (Test-Path $Probe) {
        Copy-Item $Probe (Join-Path $FfmpegStage "ffprobe.exe") -Force
    }

    Remove-Item -Recurse -Force $ExtractTmp, $FfmpegZip
}

Write-Host "==> Bundled ffmpeg:"
Get-ChildItem $FfmpegStage | Format-Table Name, Length -AutoSize

# --- Run PyInstaller --------------------------------------------------------
# --onefile      : single exe; extracts to a temp dir on launch (sys._MEIPASS).
# --windowed     : no console window on launch.
# --add-binary   : bundle ffmpeg.exe and ffprobe.exe into the exe.
# --clean        : fresh cache; avoids stale imports from earlier builds.
Write-Host "==> Running PyInstaller"

$PiArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", $AppName,
    "--add-binary", "$FfmpegStage\ffmpeg.exe;."
)

if (Test-Path (Join-Path $FfmpegStage "ffprobe.exe")) {
    $PiArgs += @("--add-binary", "$FfmpegStage\ffprobe.exe;.")
}
if ($Icon -and (Test-Path $Icon)) {
    $PiArgs += @("--icon", $Icon)
}
$PiArgs += $Entry

& $VenvPy -m PyInstaller @PiArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- Done -------------------------------------------------------------------
$ExePath = Join-Path $DistDir "$AppName.exe"
if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: Build finished but $ExePath wasn't produced." -ForegroundColor Red
    exit 1
}

$SizeMB = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host " Build succeeded" -ForegroundColor Green
Write-Host "   File:  $ExePath"
Write-Host "   Size:  $SizeMB MB"
Write-Host ""
Write-Host " Send this single .exe to your testers." -ForegroundColor Green
Write-Host " They just double-click - no Python, no ffmpeg install needed." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
