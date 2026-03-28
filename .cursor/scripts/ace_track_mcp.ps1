# ACE MCP Tracking Hook - Captures tool executions for AI-Trail
# Input: tool_name, tool_input, result_json, duration

$aceDir = ".cursor\ace"
if (-not (Test-Path $aceDir)) {
    New-Item -ItemType Directory -Path $aceDir -Force | Out-Null
}

$input | Out-File -Append -FilePath "$aceDir\mcp_trajectory.jsonl" -Encoding utf8
