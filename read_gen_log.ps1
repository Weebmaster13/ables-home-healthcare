. "C:\Users\nzomo\AppData\Local\Temp\opencode\cdp_client.ps1"
$js = '(async () => { const r = await fetch("/cpsess4323750598/download?file=" + encodeURIComponent("/home/soev6l6iqyxn/ables-app/instance/logs/gen.log"), {credentials:"include"}); const t = await r.text(); const l = t.split(/\n/); return JSON.stringify(l.slice(-12)); })()'
$w = Connect-Cdp -WsUrl "ws://127.0.0.1:9222/devtools/page/FF257D2B52E1AC621AC56226655621AF"
$r = Send-CdpCommandSync -Ws $w -Method "Runtime.evaluate" -Params @{ expression=$js; awaitPromise=$true; returnByValue=$true } -TimeoutMs 120000
Write-Host $r.result.result.value
Close-Cdp -Ws $w
