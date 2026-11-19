param(
    [ValidateSet('Recorded','LocalDemo','LocalCse','Swarm','SwarmHeadless','SwarmService','LiveSwarm')]
    [string]$Mode = 'Recorded',
    [switch]$WithoutCse,
    [switch]$ExistingBroker,
    [switch]$Benchmark,
    [ValidateRange(2,16)][int]$DroneCount=8,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if ($WithoutCse) { $Mode = 'LocalDemo' }
    $demoPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $demoPython)) {
        py -3.13 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required. Install it and retry.' }
    }
    if ($Mode -eq 'SwarmService') {
        & $demoPython -X utf8 -m rescuetwin.swarm_service
        if ($LASTEXITCODE -ne 0) { throw 'Swarm service stopped with an error.' }
        return
    }
    $requirements = 'requirements.txt'
    $moduleList = "('paho', 'requests', 'yaml')"
    if ($Mode -in @('LocalDemo','LocalCse')) {
        $requirements = 'requirements-demo.txt'
        $moduleList = "('paho', 'amqtt', 'acmecse')"
    }
    if ($Mode -in @('Swarm','SwarmHeadless')) {
        $requirements = 'requirements-simulation.txt'
        $moduleList = "('numpy', 'matplotlib')"
    }
    & $demoPython -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in $moduleList) else 1)"
    if ($LASTEXITCODE -ne 0) {
        & $demoPython -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip.' }
        & $demoPython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    if ($CheckOnly) { Write-Output "Environment ready for $Mode."; return }
    if ($Mode -in @('Swarm','SwarmHeadless')) {
        $simulationScript = Join-Path $PSScriptRoot 'tools\run_swarm.py'
        if (-not $env:SWARM_MEC_URL) { $env:SWARM_MEC_URL = 'http://127.0.0.1:8780' }
        Write-Output "Swarm uses independent MEC service: $env:SWARM_MEC_URL"
        $simulationArguments = @('-X','utf8',$simulationScript)
        if (-not $Benchmark) { $simulationArguments += '--quick' }
        if ($Mode -eq 'SwarmHeadless') { $simulationArguments += '--no-anim' }
        Write-Output 'The simulation runs benchmarks and regenerates its two output JSONL files before animation.'
        & $demoPython @simulationArguments
    } else {
        $demoArguments = @('-X','utf8','-m','rescuetwin.demo')
        if ($Mode -eq 'LiveSwarm') {
            $demoArguments += @('--live-swarm','--drone-count',"$DroneCount")
        } elseif ($Mode -eq 'Recorded') {
            $demoArguments += @('--config',(Join-Path $PSScriptRoot 'config.team.example.json'),'--recorded-only')
        } else {
            if (-not $ExistingBroker) { $demoArguments += '--local-broker' }
            if ($Mode -eq 'LocalCse') { $demoArguments += '--with-cse' }
        }
        & $demoPython @demoArguments
    }
    if ($LASTEXITCODE -ne 0) { throw 'Run failed. Read the output above.' }
}
finally { Pop-Location }
