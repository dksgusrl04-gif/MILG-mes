param([string]$TaskName = 'MILG-Engine-PoC', [int]$Port = 8765, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this deployment script in an Administrator PowerShell window.'
}
$deliveryRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$pythonPath = Join-Path $deliveryRoot '.venv\Scripts\python.exe'
$statePath = Join-Path $deliveryRoot 'runtime-state'
if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Description -ne ('MILG managed deployment: ' + $deliveryRoot)) { throw 'Task is not owned by this deployment' }
        Stop-ScheduledTask -TaskName $TaskName
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    return
}
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath (Join-Path $statePath 'pilot-active.json'))) {
    throw 'Run Start-MilgDelivery.ps1 -InstallDependencies -Offline once, then stop it before installing startup.'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Task exists. Remove it explicitly with this script before reinstalling.' }
if ($statePath.Contains('"')) { throw 'Unsupported path quote' }
$arguments = '-X utf8 -u -m mlng_ai.pilot serve --auto-learn --state "' + $statePath + '" --port ' + $Port
# Run the engine as Local Service, not as an unrestricted administrator.
# The package is readable by this account; only tenant state is writable.
$serviceSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagate = [Security.AccessControl.PropagationFlags]::None
$allow = [Security.AccessControl.AccessControlType]::Allow
$packageAcl = Get-Acl -LiteralPath $deliveryRoot
$packageAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($serviceSid,[Security.AccessControl.FileSystemRights]::ReadAndExecute,$inherit,$propagate,$allow))
Set-Acl -LiteralPath $deliveryRoot -AclObject $packageAcl
$stateAcl = Get-Acl -LiteralPath $statePath
$stateAcl.SetAccessRuleProtection($true,$false)
foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'),[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
    $stateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,$inherit,$propagate,$allow))
}
$stateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($serviceSid,[Security.AccessControl.FileSystemRights]::Modify,$inherit,$propagate,$allow))
Set-Acl -LiteralPath $statePath -AclObject $stateAcl
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory (Join-Path $deliveryRoot 'ai')
$trigger = New-ScheduledTaskTrigger -AtStartup
$identity = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 100 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $identity -Settings $settings -Description ('MILG managed deployment: ' + $deliveryRoot) | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output ('Installed: ' + $TaskName + '. Engine remains loopback-only; use your approved HTTPS reverse proxy for remote MES clients.')
