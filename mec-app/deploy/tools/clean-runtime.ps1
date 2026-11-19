#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess=$true,ConfirmImpact='Medium')]
param()
$ErrorActionPreference='Stop'
$deployRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$repoRoot=Split-Path (Split-Path $deployRoot -Parent) -Parent
$cacheRoots=@((Join-Path $deployRoot 'runtime'),(Join-Path $repoRoot 'RescueTwins\mec-app\deploy\runtime'))
$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent()
foreach($cacheRoot in $cacheRoots){
    if(-not(Test-Path -LiteralPath $cacheRoot)){continue}
    $resolvedRoot=(Resolve-Path -LiteralPath $cacheRoot).Path
    if(-not $resolvedRoot.StartsWith($repoRoot+'\',[System.StringComparison]::OrdinalIgnoreCase)){throw 'Runtime escaped project root'}
    foreach($candidate in Get-ChildItem -LiteralPath $resolvedRoot -Directory -Force){
        if(-not($candidate.Name -like 'pytest-*' -or $candidate.Name -in @('npm-cache','relocation-temp'))){continue}
        $target=(Resolve-Path -LiteralPath $candidate.FullName).Path
        if(-not $target.StartsWith($resolvedRoot+'\',[System.StringComparison]::OrdinalIgnoreCase) -or ($candidate.Attributes -band [System.IO.FileAttributes]::ReparsePoint)){throw 'Invalid cache target'}
        if($PSCmdlet.ShouldProcess($target,'Remove disposable test/cache directory')){
            try{Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop}
            catch{
                & takeown.exe /f $target
                if($LASTEXITCODE -ne 0){throw "Cannot take ownership of $target"}
                & icacls.exe $target /grant "*$($identity.User.Value):(OI)(CI)F" /q
                if($LASTEXITCODE -ne 0){throw "Cannot repair permissions for $target"}
                $links = Get-ChildItem -LiteralPath $target -Recurse -Force -Attributes ReparsePoint -ErrorAction Stop
                if($links){throw "Cache contains links; manual review required: $target"}
                Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
            }
            Write-Output "Removed cache: $target"
        }
    }
}
