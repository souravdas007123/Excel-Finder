<#
.SYNOPSIS
    Excel Finder ka Windows installer banata hai: dist\installer\ExcelFinder-Setup-<version>.exe

.DESCRIPTION
    Ek hi command me: tests chalata hai, license server ka address aur PUBLIC key app me daalta hai, PyInstaller se app
    bandhta hai, usko ek baar chalakar check karta hai (smoke test), phir Inno Setup se installer banata hai.

    Pehle chahiye (sirf aapke build PC par, customer ko nahi):
      * Python 3.12 ya 3.13 (64-bit), "Add python.exe to PATH" ke saath
      * Inno Setup 6.3+  (https://jrsoftware.org/isdl.php). Na ho toh portable ZIP ban jata hai.

.EXAMPLE
    .\installer\build_windows.ps1 -ServerUrl https://license.example.com `
        -PrivateKeyFile license_server\private_key.txt -BuyUrl https://example.com/buy -Support help@example.com `
        -UpdateUrl https://example.com/latest.json

.EXAMPLE
    # Sirf apne PC par test (license server local, http):
    .\installer\build_windows.ps1 -ServerUrl http://127.0.0.1:8800 -AllowHttp -PublicKey <public key> -SkipInstaller
#>
param(
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [string]$PublicKey = "",
    [string]$PrivateKeyFile = "",          # isse public key nikal li jati hai (private key app me nahi jati)
    [string]$BuyUrl = "",
    [string]$Support = "",
    [string]$UpdateUrl = "",               # website par latest.json ka https address (naye version ke notice ke liye)
    [string]$Publisher = "Excel Finder",
    [switch]$AllowHttp,
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Run($exe, $argList) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe $($argList -join ' ')" }
}

if (-not $PublicKey -and -not $PrivateKeyFile) { throw "Give -PublicKey or -PrivateKeyFile (license_server\private_key.txt)." }
$configPath = Join-Path $root "licensing\build_config.py"

try {
    Step "Python virtual environment (.venv-build)"
    $python = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue) }
    if (-not $python) { throw "Python was not found. Install Python 3.12+ (64-bit) and tick 'Add to PATH'." }
    if (-not (Test-Path ".venv-build\Scripts\python.exe")) { Run $python.Source @("-m", "venv", ".venv-build") }
    $py = Join-Path $root ".venv-build\Scripts\python.exe"
    Run $py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
    Run $py @("-m", "pip", "install", "--quiet", "-r", "requirements.txt", "-r", "requirements-build.txt")

    if (-not $SkipTests) {
        Step "Running the tests"
        Run $py @("manage.py", "test")
    }

    Step "Writing licensing\build_config.py (license server address + public key)"
    $cfg = @("installer\make_build_config.py", "--server", $ServerUrl)
    if ($PrivateKeyFile) { $cfg += @("--private-key-file", $PrivateKeyFile) } else { $cfg += @("--public-key", $PublicKey) }
    if ($BuyUrl) { $cfg += @("--buy-url", $BuyUrl) }
    if ($Support) { $cfg += @("--support", $Support) }
    if ($UpdateUrl) { $cfg += @("--update-url", $UpdateUrl) }
    if ($AllowHttp) { $cfg += "--allow-http" }
    Run $py $cfg

    Step "Collecting static files"
    if (Test-Path "staticfiles") { Remove-Item -Recurse -Force "staticfiles" }
    Run $py @("manage.py", "collectstatic", "--noinput", "-v", "0")

    Step "Building the app with PyInstaller"
    foreach ($dir in @("dist\ExcelFinder", "build")) { if (Test-Path $dir) { Remove-Item -Recurse -Force $dir } }
    Run $py @("-m", "PyInstaller", "installer\ExcelFinder.spec", "--noconfirm", "--distpath", "dist", "--workpath", "build")

    Step "Smoke test: start the built app once"
    $exe = Join-Path $root "dist\ExcelFinder\ExcelFinder.exe"
    $tmpData = Join-Path ([System.IO.Path]::GetTempPath()) ("excelfinder-smoke-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmpData | Out-Null
    $env:EXCEL_FINDER_DATA = $tmpData
    $proc = Start-Process -FilePath $exe -ArgumentList @("--no-gui", "--no-browser", "--port", "8799") -PassThru -WindowStyle Hidden
    $healthy = $false
    try {
        for ($i = 0; $i -lt 60 -and -not $healthy; $i++) {
            Start-Sleep -Seconds 1
            try { $healthy = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8799/setup/" -TimeoutSec 3).StatusCode -eq 200 } catch { }
        }
    } finally {
        Get-Process -Name "ExcelFinder" -ErrorAction SilentlyContinue | Stop-Process -Force
        Remove-Item Env:\EXCEL_FINDER_DATA -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Remove-Item -Recurse -Force $tmpData -ErrorAction SilentlyContinue
    }
    if (-not $healthy) { throw "The built app did not start. Rebuild with `$env:EXCEL_FINDER_CONSOLE='1' to see the error." }
    Write-Host "App starts fine." -ForegroundColor Green

    $version = (& $py -c "from search.version import VERSION; print(VERSION)").Trim()
    if ($SkipInstaller) { Write-Host "`nDone (installer skipped). App folder: dist\ExcelFinder" -ForegroundColor Green; return }

    Step "Making the installer (version $version)"
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) {
        Run $iscc @("/Qp", "/DAppVersion=$version", "/DAppPublisher=$Publisher", "installer\ExcelFinder.iss")
        Write-Host "`nInstaller ready: dist\installer\ExcelFinder-Setup-$version.exe" -ForegroundColor Green
        Write-Host "Sign it with your code-signing certificate before selling (see installer\README.md)." -ForegroundColor Yellow
    } else {
        $zip = "dist\ExcelFinder-portable-$version.zip"
        if (Test-Path $zip) { Remove-Item $zip }
        Compress-Archive -Path "dist\ExcelFinder\*" -DestinationPath $zip
        Write-Host "`nInno Setup not found, made a portable ZIP instead: $zip" -ForegroundColor Yellow
        Write-Host "Install Inno Setup 6.3+ to get a proper installer." -ForegroundColor Yellow
    }
}
finally {
    # Ye file license ki settings rakhti hai: build ke baad hata do, taaki aapke apne source copy me license check na lage
    if (Test-Path $configPath) { Remove-Item $configPath -Force }
}
