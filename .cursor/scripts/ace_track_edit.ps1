# ACE Edit Tracking Hook - Captures file edits with domain detection
# Input: file_path, edits[]
# Writes domain state to temp file for MCP Resources (Issue #3 fix)

$aceDir = ".cursor\ace"
if (-not (Test-Path $aceDir)) {
    New-Item -ItemType Directory -Path $aceDir -Force | Out-Null
}

$inputJson = [Console]::In.ReadToEnd()
$inputJson | Out-File -Append -FilePath "$aceDir\edit_trajectory.jsonl" -Encoding utf8

# Domain detection function
function Detect-Domain {
    param([string]$FilePath)
    switch -Regex ($FilePath) {
        '(auth|login|session|jwt)' { return 'auth' }
        '(api|routes|endpoint|controller)' { return 'api' }
        '(cache|redis|memo)' { return 'cache' }
        '(db|migration|model|schema)' { return 'database' }
        '(component|ui|view|\.tsx|\.jsx)' { return 'ui' }
        '(test|spec|mock)' { return 'test' }
        default { return 'general' }
    }
}

# Extract file path and detect domain
try {
    $data = $inputJson | ConvertFrom-Json -ErrorAction SilentlyContinue
    $filePath = $data.file_path
    if (-not $filePath) { $filePath = $data.path }

    if ($filePath) {
        $currentDomain = Detect-Domain -FilePath $filePath
        $lastDomainFile = "$aceDir\last_domain.txt"
        $lastDomain = if (Test-Path $lastDomainFile) { Get-Content $lastDomainFile } else { "" }

        if ($currentDomain -ne $lastDomain -and $lastDomain) {
            $shift = @{
                from = $lastDomain
                to = $currentDomain
                file = $filePath
                timestamp = (Get-Date -Format "o")
            } | ConvertTo-Json -Compress
            $shift | Out-File -Append -FilePath "$aceDir\domain_shifts.log" -Encoding utf8
        }

        $currentDomain | Out-File -FilePath $lastDomainFile -Encoding utf8

        # Write domain state to temp file for MCP Resources
        # MCP server reads this to expose ace://domain/current resource
        $settingsPath = "$aceDir\settings.json"
        $projectId = "default"
        if (Test-Path $settingsPath) {
            try {
                $settings = Get-Content $settingsPath | ConvertFrom-Json
                if ($settings.projectId) { $projectId = $settings.projectId }
            } catch {}
        }
        $md5 = [System.Security.Cryptography.MD5]::Create()
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($projectId)
        $hash = [BitConverter]::ToString($md5.ComputeHash($bytes)).Replace("-","").Substring(0,8).ToLower()
        $tempFile = "$env:TEMP\ace-domain-$hash.json"
        @{
            domain = $currentDomain
            file = $filePath
            timestamp = (Get-Date -Format "o")
        } | ConvertTo-Json | Out-File -FilePath $tempFile -Encoding utf8
    }
} catch {
    # Silently continue on parse errors
}
