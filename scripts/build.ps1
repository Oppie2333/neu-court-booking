param([string]$Python = 'python', [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if (-not $SkipInstall) {
        & $Python -m pip install -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    }
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    & $Python -m PyInstaller --noconfirm --clean NEUCourtBooking.spec
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    $bundle = Join-Path $projectRoot 'dist/NEUCourtBooking'
    @('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'NEWS.md', 'VERSION', '先读我.txt') | ForEach-Object {
        Copy-Item -LiteralPath (Join-Path $projectRoot $_) -Destination $bundle
    }
    $docsTarget = Join-Path $bundle 'docs'
    New-Item -ItemType Directory -Path $docsTarget -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $projectRoot 'docs') | Copy-Item -Destination $docsTarget -Recurse -Force
    & $Python scripts/collect_licenses.py (Join-Path $bundle 'third_party_licenses')
    if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
    Write-Host "Built: $bundle/NEUCourtBooking.exe"
} finally {
    Pop-Location
}
