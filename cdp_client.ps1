# CDP Client for Opera GX - PowerShell implementation

$script:ws = $null
$script:nextId = 1

function Connect-Cdp {
    param([string]$webSocketUrl)
    
    $script:ws = New-Object System.Net.WebSockets.ClientWebSocket
    $script:ws.ConnectAsync((New-Object System.Uri($webSocketUrl)), [System.Threading.CancellationToken]::None).Wait() | Out-Null
}

function Send-CdpCommandSync {
    param([string]$method, [hashtable]$params, [int]$timeoutSec = 10)
    
    $id = $script:nextId++
    $command = @{
        id = $id
        method = $method
        params = $params
    } | ConvertTo-Json -Depth 10
    
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($command)
    $segment = New-Object System.ArraySegment[Byte](,$bytes)
    $script:ws.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [System.Threading.CancellationToken]::None).Wait() | Out-Null
    
    # Receive loop - wait for response with matching ID
    $buffer = New-Object Byte[] 65536
    $start = Get-Date
    
    while ((Get-Date) - $start -lt (New-TimeSpan -Seconds $timeoutSec)) {
        try {
            $segment = New-Object System.ArraySegment[Byte](,$buffer)
            $result = $script:ws.ReceiveAsync($segment, [System.Threading.CancellationToken]::None).Result
            
            if ($result.MessageType -eq 'Text') {
                $json = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $result.Count)
                $msg = $json | ConvertFrom-Json
                
                if ($msg.PSObject.Properties.Name -contains 'id' -and $msg.id -eq $id) {
                    return $msg
                }
            }
            
            if ($result.CloseStatus -ne $null) {
                return @{error = "websocket_closed"; closeStatus = $result.CloseStatus.ToString()}
            }
        } catch {
            return @{error = "receive_failed"; exception = $_.Exception.Message}
        }
    }
    
    return @{error = "timeout"; method = $method; id = $id}
}

function Close-Cdp {
    if ($script:ws -and $script:ws.State -eq 'Open') {
        $script:ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, "done", [System.Threading.CancellationToken]::None).Wait() | Out-Null
        $script:ws.Dispose()
    }
    $script:ws = $null
}