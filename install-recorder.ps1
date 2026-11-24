param(
    [switch]$Uninstall,
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$serviceName = 'DXCTVRecorder'
$legacyTaskNames = @('DXCTVRecorder', 'TapoLocalRecorder')
$appDir = Join-Path $env:LOCALAPPDATA 'TapoLocalRecorder'
$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wrapperPath = Join-Path $appDir 'DXCTVRecorder.exe'
$xmlPath = Join-Path $appDir 'DXCTVRecorder.xml'
$uninstallKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\DXCTVRecorder'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator) -and -not $Elevated) {
    $scriptPath = $MyInvocation.MyCommand.Path
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $scriptPath), '-Elevated')
    if ($Uninstall) { $args += '-Uninstall' }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

if ($Uninstall) {
    if (Test-Path $wrapperPath) {
        & $wrapperPath stop 2>$null
        & $wrapperPath uninstall 2>$null
    }
    foreach ($taskName in $legacyTaskNames) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    # A foreground or legacy pythonw recorder can outlive its scheduled task or service.
    # Stop only the recorder process launched with this install's recorder.py and its children.
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $recorderScriptPattern = [regex]::Escape((Join-Path $appDir 'recorder.py'))
    $recorderPids = @($processes | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -match $recorderScriptPattern
    } | ForEach-Object { [int]$_.ProcessId })
    do {
        $previousCount = $recorderPids.Count
        foreach ($process in $processes) {
            $parentId = [int]$process.ParentProcessId
            $processId = [int]$process.ProcessId
            if ($recorderPids -contains $parentId -and $recorderPids -notcontains $processId) {
                $recorderPids += $processId
            }
        }
    } while ($recorderPids.Count -gt $previousCount)
    for ($index = $recorderPids.Count - 1; $index -ge 0; $index--) {
        Stop-Process -Id $recorderPids[$index] -Force -ErrorAction SilentlyContinue
    }
    foreach ($shortcutName in @('DXCTVRecorder.lnk', 'DXCTV Recorder.lnk', 'Tapo Local Recorder.lnk')) {
        Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) $shortcutName) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $wrapperPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $xmlPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appDir 'recorder.py') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appDir 'README.md') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appDir 'DXCTVRecorder_User_Guide.md') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appDir 'install-recorder.ps1') -Force -ErrorAction SilentlyContinue
    Write-Host "DXCTVRecorder service was removed. Settings and recordings in $appDir were kept."
    exit 0
}

$sourceRecorder = Join-Path $sourceDir 'recorder.py'
$installedRecorder = Join-Path $appDir 'recorder.py'
if (-not (Test-Path $sourceRecorder)) {
    throw 'recorder.py was not found beside this installer. Extract the complete recorder folder before installing.'
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'python.exe was not found. Install Python for Windows, then run this installer again.'
}
$pythonVersionText = & $python.Source --version
if ($pythonVersionText -notmatch 'Python\s+(\d+)\.(\d+)') {
    throw 'The Python version could not be read. Install Python 3.10 or later, then run this installer again.'
}
$pythonMajor = [int]$Matches[1]
$pythonMinor = [int]$Matches[2]
if ($pythonMajor -lt 3 -or ($pythonMajor -eq 3 -and $pythonMinor -lt 10)) {
    throw 'DXCTV Recorder requires Python 3.10 or later. Update Python, then run this installer again.'
}
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    throw 'ffmpeg.exe was not found on PATH. Install FFmpeg and confirm ffmpeg -version works before installing the service.'
}

New-Item -ItemType Directory -Path $appDir -Force | Out-Null
$sameSourceFolder = [string]::Equals([IO.Path]::GetFullPath($sourceDir).TrimEnd('\'), [IO.Path]::GetFullPath($appDir).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
if (-not $sameSourceFolder) {
    Copy-Item -LiteralPath $sourceRecorder -Destination $appDir -Force
    if (Test-Path (Join-Path $sourceDir 'README.md')) {
        Copy-Item -LiteralPath (Join-Path $sourceDir 'README.md') -Destination $appDir -Force
    }
    if (Test-Path (Join-Path $sourceDir 'DXCTVRecorder_User_Guide.md')) {
        Copy-Item -LiteralPath (Join-Path $sourceDir 'DXCTVRecorder_User_Guide.md') -Destination $appDir -Force
    }
    Copy-Item -LiteralPath $MyInvocation.MyCommand.Path -Destination $appDir -Force
}

# Keep the existing camera credentials, folder, and recordings on upgrades.
$configPath = Join-Path $appDir 'config.json'
if (-not (Test-Path $configPath)) {
    $config = [ordered]@{
        ffmpeg_path = $ffmpeg.Source
        recordings_folder = 'recordings'
        segment_minutes = 10
        retention_days = 30
        port = 8765
        cameras = @()
    }
    $initialConfigJson = $config | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($configPath, $initialConfigJson, (New-Object System.Text.UTF8Encoding -ArgumentList $false))
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$config.ffmpeg_path = $ffmpeg.Source
$configJson = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $configJson, (New-Object System.Text.UTF8Encoding -ArgumentList $false))

# WinSW is the Windows Service Wrapper used to run DXCTV Recorder in the background.
$winswUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe'
$expectedHash = '05B82D46AD331CC16BDc00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA'
$downloadPath = Join-Path $appDir 'WinSW-x64.download.exe'
Write-Host 'Downloading WinSW x64 (Windows Service Wrapper) for DXCTV Recorder...'
$oldProgressPreference = $ProgressPreference
try {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $winswUrl -OutFile $downloadPath -UseBasicParsing
}
finally {
    $ProgressPreference = $oldProgressPreference
}
Write-Host 'Verifying the downloaded Windows Service Wrapper...'
$actualHash = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) {
    Remove-Item -LiteralPath $downloadPath -Force -ErrorAction SilentlyContinue
    throw 'The downloaded Windows Service Wrapper failed SHA-256 verification; DXCTV Recorder was not installed.'
}

if ((Get-Service -Name $serviceName -ErrorAction SilentlyContinue) -and (Test-Path $wrapperPath)) {
    Write-Host 'Stopping the existing DXCTV Recorder service before upgrading...'
    & $wrapperPath stop
    & $wrapperPath uninstall
}
Move-Item -LiteralPath $downloadPath -Destination $wrapperPath -Force

$pythonPath = [System.Security.SecurityElement]::Escape($python.Source)
$appPath = [System.Security.SecurityElement]::Escape($appDir)
$scriptPath = [System.Security.SecurityElement]::Escape((Join-Path $appDir 'recorder.py'))
$xml = @"
<service>
  <id>DXCTVRecorder</id>
  <name>DXCTV Recorder</name>
  <description>Local CCTV camera recording and browser playback service</description>
  <executable>$pythonPath</executable>
  <arguments>"$scriptPath"</arguments>
  <workingdirectory>$appPath</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>60sec</stoptimeout>
  <logpath>$appPath\logs</logpath>
  <onfailure action="restart" delay="10 sec" />
</service>
"@
Set-Content -LiteralPath $xmlPath -Value $xml -Encoding UTF8

# Remove the prior sign-in tasks so Windows does not launch a second recorder.
foreach ($taskName in $legacyTaskNames) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}

Write-Host 'Registering DXCTV Recorder as a Windows service...'
& $wrapperPath install
if ($LASTEXITCODE -ne 0) { throw 'WinSW could not register the DXCTVRecorder service.' }

# Clear an older interactive copy of this recorder if it owns the web port. If another
# application owns the port, stop with a clear message instead of claiming success.
$port = if ($config.port) { [int]$config.port } else { 8765 }
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $null -ne $_.OwningProcess -and [int]$_.OwningProcess -gt 0 } |
    Select-Object -First 1
if ($listener) {
    $ownerProcessId = [int]$listener.OwningProcess
    $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerProcessId" -ErrorAction SilentlyContinue
    $expectedRecorderPath = [regex]::Escape((Join-Path $appDir 'recorder.py'))
    if ($listenerProcess -and $listenerProcess.Name -in @('python.exe', 'pythonw.exe') -and $listenerProcess.CommandLine -match $expectedRecorderPath) {
        Write-Host 'Stopping the older DXCTV Recorder process so the service can start...'
        $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
        $processTree = [System.Collections.Generic.List[int]]::new()
        $processTree.Add($ownerProcessId)
        do {
            $previousCount = $processTree.Count
            foreach ($process in $processes) {
                if ($processTree.Contains([int]$process.ParentProcessId) -and -not $processTree.Contains([int]$process.ProcessId)) {
                    $processTree.Add([int]$process.ProcessId)
                }
            }
        } while ($processTree.Count -gt $previousCount)
        for ($index = $processTree.Count - 1; $index -ge 0; $index--) {
            Stop-Process -Id $processTree[$index] -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $null -ne $_.OwningProcess -and [int]$_.OwningProcess -gt 0 } |
            Select-Object -First 1
    }
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$listener.OwningProcess)" -ErrorAction SilentlyContinue
        $ownerName = if ($owner) { $owner.Name } else { 'another application' }
        throw "DXCTV Recorder could not start because port $port is already in use by $ownerName (PID $([int]$listener.OwningProcess)). Close that application and run the installer again."
    }
}

Write-Host 'Starting the DXCTV Recorder Windows service...'
& $wrapperPath start
if ($LASTEXITCODE -ne 0) { throw 'The service was installed but could not be started. Check the DXCTV Recorder logs.' }
$serviceDeadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Seconds 1
    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
} until (($service -and $service.Status -eq 'Running') -or (Get-Date) -ge $serviceDeadline)
if (-not $service -or $service.Status -ne 'Running') {
    throw 'The service was installed but did not reach the Running state. Check the DXCTV Recorder logs.'
}
Write-Host 'DXCTV Recorder service is running.'

# Publish DXCTVRecorder in Windows Installed apps with its uninstall command.
New-Item -Path $uninstallKey -Force | Out-Null
$uninstallCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Uninstall' -f (Join-Path $appDir 'install-recorder.ps1')
New-ItemProperty -Path $uninstallKey -Name 'DisplayName' -Value 'DXCTV Recorder' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'DisplayVersion' -Value '1.0.0' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'InstallLocation' -Value $appDir -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'UninstallString' -Value $uninstallCommand -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'DisplayIcon' -Value $wrapperPath -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name 'NoRepair' -Value 1 -PropertyType DWord -Force | Out-Null

$oldShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Tapo Local Recorder.lnk'
Remove-Item -LiteralPath $oldShortcut -Force -ErrorAction SilentlyContinue
$oldNamedShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'DXCTVRecorder.lnk'
Remove-Item -LiteralPath $oldNamedShortcut -Force -ErrorAction SilentlyContinue
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'DXCTV Recorder.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($desktopShortcut)
$shortcut.TargetPath = "http://127.0.0.1:$port/"
$shortcut.Description = 'Open DXCTV Recorder web client'
$shortcut.Save()

Write-Host 'DXCTV Recorder is installed as an automatic Windows service.'
Write-Host 'It is running now and starts with Windows, including before you sign in.'
Write-Host "Open http://127.0.0.1:$port/ or use the DXCTV Recorder desktop shortcut."
Write-Host 'The service runs as LocalSystem. Use the browser folder selector to choose storage.'
