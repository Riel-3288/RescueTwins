[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'
$expectedPython = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe').ToLowerInvariant()
$expectedRuntime = (Join-Path $PSScriptRoot 'runtime\demo').ToLowerInvariant()
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -in @(8765,18883,18000,18001,18002) })
$targets = @()
foreach ($demoProcessId in @($listeners.OwningProcess | Sort-Object -Unique)) {
    $demoProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$demoProcessId"
    if (-not $demoProcess) { continue }
    $command = ([string]$demoProcess.CommandLine).Replace('/','\').ToLowerInvariant()
    $isDemo = $command.Contains($expectedPython) -and $command -match '-m\s+rescuetwin\.demo(?:\s|$)'
    $isCse = $command.Contains($expectedRuntime) -and $command -match '-m\s+(?:acmecse|tools\.local_cse)(?:\s|$)'
    if (-not ($isDemo -or $isCse)) {
        throw "Port is owned by an unrecognized process ($demoProcessId). Nothing has been stopped."
    }
    $targets += $demoProcessId
}
foreach ($demoProcessId in $targets) {
    if ($PSCmdlet.ShouldProcess("RescueTwins demo process $demoProcessId", 'Stop')) {
        Stop-Process -Id $demoProcessId -ErrorAction SilentlyContinue
        Wait-Process -Id $demoProcessId -Timeout 5 -ErrorAction SilentlyContinue
        Write-Output "Stopped demo process $demoProcessId"
    }
}
if ($targets.Count -eq 0) { Write-Output 'No local RescueTwins demo processes are listening.' }
# Preserve databases and incident history.
