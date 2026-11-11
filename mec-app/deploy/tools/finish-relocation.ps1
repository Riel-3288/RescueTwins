[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).ProviderPath
$nestedRoot = Join-Path $projectRoot 'RescueTwins'
$runtimeRoot = Join-Path $projectRoot 'mec-app\deploy\runtime'
$backupRoot = Join-Path $runtimeRoot 'relocation-cache-backup'
if ($projectRoot -ne 'C:\Users\User\Desktop\Other\RescueTwins') { throw 'Unexpected project location.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.git\HEAD'))) { throw 'The relocated Git repository is missing.' }
if (-not (Test-Path -LiteralPath $nestedRoot)) { Write-Output 'The nested folder has already been removed.'; return }
if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) { throw 'The relocated runtime folder is missing.' }
if (Test-Path -LiteralPath $backupRoot) { throw "The backup destination already exists; nothing will overwrite it: $backupRoot" }

function Assert-PlainDirectory {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected an ordinary directory, without directory links: $Path"
    }
}

function Assert-OnlyDirectories {
    param([string]$Path, [string[]]$Allowed)
    foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
        if (-not $item.PSIsContainer -or $item.Name -notin $Allowed -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unexpected content remains; review it before cleanup: $($item.FullName)"
        }
    }
}

# Validate both absolute paths and the shallow leftover structure before moving.
# The inaccessible pytest directories are never enumerated or deleted.
$nestedResolved = (Resolve-Path -LiteralPath $nestedRoot).ProviderPath
$runtimeResolved = (Resolve-Path -LiteralPath $runtimeRoot).ProviderPath
if ($nestedResolved -ne (Join-Path $projectRoot 'RescueTwins') -or $runtimeResolved -ne (Join-Path $projectRoot 'mec-app\deploy\runtime')) {
    throw 'Unexpected source or destination.'
}
foreach ($path in @($projectRoot, $nestedRoot, (Join-Path $nestedRoot 'mec-app'), (Join-Path $nestedRoot 'mec-app\deploy'), (Join-Path $nestedRoot 'mec-app\deploy\runtime'), (Join-Path $projectRoot 'mec-app'), (Join-Path $projectRoot 'mec-app\deploy'), $runtimeRoot)) {
    Assert-PlainDirectory -Path $path
}
Assert-OnlyDirectories -Path $nestedRoot -Allowed @('mec-app')
Assert-OnlyDirectories -Path (Join-Path $nestedRoot 'mec-app') -Allowed @('deploy')
Assert-OnlyDirectories -Path (Join-Path $nestedRoot 'mec-app\deploy') -Allowed @('runtime')
Assert-OnlyDirectories -Path (Join-Path $nestedRoot 'mec-app\deploy\runtime') -Allowed @('pytest-3d-final', 'pytest-mqtt-publish-fix', 'pytest-rescue', 'pytest-rescue-acceptance', 'pytest-rescue-checked', 'pytest-rescue-final', 'temp')

if ($PSCmdlet.ShouldProcess($nestedRoot, "Move remaining test caches intact to $backupRoot")) {
    try {
        # A same-volume directory rename preserves all contents and permissions.
        # Move-Item can walk the children and fail on protected pytest caches.
        [IO.Directory]::Move($nestedResolved, $backupRoot)
    } catch {
        $cause = $_.Exception
        while ($cause.InnerException) { $cause = $cause.InnerException }
        $windowsError = $cause.HResult -band 0xffff
        if ($windowsError -in @(32, 33)) {
            throw "Windows reports that the old folder is in use. Save your work and close terminals, editors, and File Explorer windows using $nestedRoot, then retry this helper from the new project folder. No files were deleted."
        }
        throw "Could not move the leftover directory: $($cause.Message). The relocated project is still at $projectRoot. No files were deleted."
    }
    if (Test-Path -LiteralPath $nestedRoot) { throw 'The nested folder still exists; relocation is incomplete.' }
    if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) { throw 'The cache backup could not be verified.' }
    Write-Output "Project folder: $projectRoot"
    Write-Output "Old test caches preserved at: $backupRoot"
    Write-Output 'The extra nested project folder is removed. Code, queued data, and Git history are preserved.'
}
