param(
    [string]$TaskName = "DiscordTelegramRoleSoundBot",
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = (Resolve-Path "$PSScriptRoot\..\..").Path
}

$RunBatPath = Join-Path $ProjectPath "run_bot.bat"

if (-not (Test-Path $RunBatPath)) {
    throw "run_bot.bat not found at: $RunBatPath"
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c \"$RunBatPath\""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Start Discord+Telegram role sound bot at user logon" -Force | Out-Null

Write-Host "Scheduled task '$TaskName' installed."
Write-Host "It will start the bot at user logon from: $RunBatPath"
