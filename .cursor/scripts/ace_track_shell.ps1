# ACE Shell Tracking Hook - Captures terminal commands for AI-Trail
# Input: command, output, duration

$aceDir = ".cursor\ace"
if (-not (Test-Path $aceDir)) {
    New-Item -ItemType Directory -Path $aceDir -Force | Out-Null
}

$input | Out-File -Append -FilePath "$aceDir\shell_trajectory.jsonl" -Encoding utf8
