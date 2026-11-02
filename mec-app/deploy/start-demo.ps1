param(
    [switch]$WithoutCse,
    [switch]$ExistingBroker,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $demoPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $demoPython)) {
        py -3.13 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required. Install it and retry.' }
    }
    & $demoPython -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('paho', 'amqtt', 'acmecse')) else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $demoPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
        if ($LASTEXITCODE -ne 0) {
            & $demoPython -m ensurepip --upgrade
            if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip. Recreate the local virtual environment.' }
        }
        & $demoPython -m pip install -r requirements-demo.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    if ($CheckOnly) {
        Write-Output 'Python environment ready. Run without -CheckOnly to start the demo.'
        return
    }
    $demoArguments = @('-X', 'utf8', '-m', 'rescuetwin.demo')
    if (-not $ExistingBroker) { $demoArguments += '--local-broker' }
    if (-not $WithoutCse) { $demoArguments += '--with-cse' }
    & $demoPython @demoArguments
    if ($LASTEXITCODE -ne 0) { throw 'Demo exited with an error. Read the output above.' }
}
finally { Pop-Location }
