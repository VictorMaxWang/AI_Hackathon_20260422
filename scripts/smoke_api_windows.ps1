param(
    [int]$Port = 8001,
    [string]$Message
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("Message")) {
    $Message = -join ([char[]](
        0x5E2E,
        0x6211,
        0x67E5,
        0x770B,
        0x5F53,
        0x524D,
        0x78C1,
        0x76D8,
        0x4F7F,
        0x7528,
        0x60C5,
        0x51B5
    ))
}

$Uri = "http://127.0.0.1:$Port/api/chat"
$body = @{
    raw_user_input = $Message
} | ConvertTo-Json -Compress

$utf8Body = [System.Text.Encoding]::UTF8.GetBytes($body)

try {
    $webResponse = Invoke-WebRequest `
        -Uri $Uri `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -UseBasicParsing `
        -Body $utf8Body
} catch {
    Write-Error "API smoke request failed: $($_.Exception.Message)"
    exit 1
}

$responseText = ""
if ($null -ne $webResponse.RawContentStream) {
    $stream = $webResponse.RawContentStream
    if ($stream.CanSeek) {
        $stream.Position = 0
    }
    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
    $responseText = $reader.ReadToEnd()
} elseif ($webResponse.Content -is [byte[]]) {
    $responseText = [System.Text.Encoding]::UTF8.GetString($webResponse.Content)
} else {
    $responseText = [string]$webResponse.Content
}

try {
    $response = $responseText | ConvertFrom-Json
} catch {
    Write-Error "API smoke failed: response was not valid JSON. $($_.Exception.Message)"
    exit 1
}

$serverInput = ""
if ($null -ne $response.operator_panel -and $null -ne $response.operator_panel.user_input) {
    $serverInput = [string]$response.operator_panel.user_input
} elseif ($null -ne $response.intent -and $null -ne $response.intent.raw_user_input) {
    $serverInput = [string]$response.intent.raw_user_input
}

$intent = ""
if ($null -ne $response.intent -and $null -ne $response.intent.intent) {
    $intent = [string]$response.intent.intent
}

$status = ""
if ($null -ne $response.result -and $null -ne $response.result.status) {
    $status = [string]$response.result.status
}

$errorSummary = ""
if ($null -ne $response.result -and $null -ne $response.result.error) {
    $errorSummary = [string]$response.result.error
}

Write-Host "uri: $Uri"
Write-Host "server_input: $serverInput"
Write-Host "intent: $intent"
Write-Host "result_status: $status"
if ($errorSummary) {
    Write-Host "error: $errorSummary"
}

if ($serverInput -match "\?{4,}") {
    Write-Error "API smoke failed: server received question marks instead of Chinese text."
    exit 1
}

if ($intent -eq "unknown" -or $status -eq "unsupported") {
    Write-Error "API smoke failed: unexpected intent/status. intent=$intent result_status=$status"
    exit 1
}
