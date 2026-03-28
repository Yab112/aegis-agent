# ACE Stop Hook - Aggregates AI-Trail trajectory with git context
# Input: status, loop_count

$inputJson = [Console]::In.ReadToEnd()
$data = $inputJson | ConvertFrom-Json -ErrorAction SilentlyContinue
$status = $data.status
$loopCount = $data.loop_count

if ($status -eq "completed" -and $loopCount -eq 0) {
    # Capture git context
    $gitBranch = git rev-parse --abbrev-ref HEAD 2>$null
    if (-not $gitBranch) { $gitBranch = "unknown" }
    $gitHash = git rev-parse --short HEAD 2>$null
    if (-not $gitHash) { $gitHash = "unknown" }

    # Count trajectory entries
    $aceDir = ".cursor\ace"
    $mcpCount = 0; $shellCount = 0; $editCount = 0; $responseCount = 0
    if (Test-Path "$aceDir\mcp_trajectory.jsonl") {
        $mcpCount = (Get-Content "$aceDir\mcp_trajectory.jsonl" | Measure-Object -Line).Lines
    }
    if (Test-Path "$aceDir\shell_trajectory.jsonl") {
        $shellCount = (Get-Content "$aceDir\shell_trajectory.jsonl" | Measure-Object -Line).Lines
    }
    if (Test-Path "$aceDir\edit_trajectory.jsonl") {
        $editCount = (Get-Content "$aceDir\edit_trajectory.jsonl" | Measure-Object -Line).Lines
    }
    if (Test-Path "$aceDir\response_trajectory.jsonl") {
        $responseCount = (Get-Content "$aceDir\response_trajectory.jsonl" | Measure-Object -Line).Lines
    }

    $summary = "MCP:$mcpCount Shell:$shellCount Edits:$editCount Responses:$responseCount"
    $msg = "Session complete. AI-Trail: $summary. Git: $gitBranch ($gitHash). Call ace_learn to capture patterns."
    Write-Output "{`"followup_message`": `"$msg`"}"
} else {
    Write-Output '{}'
}
