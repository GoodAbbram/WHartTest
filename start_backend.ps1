# WHartTest 后端一键重启脚本
# 作用：先杀掉旧后端进程（避免多开/端口冲突），再用项目 .venv 全新启动 Django + Celery
# 用法：右键"使用 PowerShell 运行"，或在 PowerShell 中执行  .\start_backend.ps1

$ErrorActionPreference = "SilentlyContinue"

$root      = "D:\IdeaProjects\WHartTest"
$djangoDir = "$root\WHartTest_Django"
$venv      = "$djangoDir\.venv\Scripts"

Write-Host "==> 停止旧后端进程..." -ForegroundColor Cyan
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match "WHartTest_Django" -and $_.CommandLine -match "runserver|celery"
} | ForEach-Object {
    Write-Host "  停止 PID $($_.ProcessId)"
    taskkill /PID $_.ProcessId /T /F | Out-Null
}
Start-Sleep -Seconds 2

Write-Host "==> 启动 Django (:8100)..." -ForegroundColor Cyan
Start-Process -FilePath "$venv\python.exe" `
    -ArgumentList "manage.py runserver 0.0.0.0:8100 --noreload" `
    -WorkingDirectory $djangoDir -WindowStyle Minimized

Start-Sleep -Seconds 3

Write-Host "==> 启动 Celery worker..." -ForegroundColor Cyan
Start-Process -FilePath "$venv\celery.exe" `
    -ArgumentList "-A wharttest_django worker --pool=solo --loglevel=info --concurrency=1" `
    -WorkingDirectory $djangoDir -WindowStyle Minimized

Write-Host "==> 等待服务就绪..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $client = New-Object Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", 8100)
        if ($client.Connected) { $ok = $true }
    } catch {}
    finally { $client.Close() }
    if ($ok) { break }
}

if ($ok) {
    Write-Host "后端已就绪: http://127.0.0.1:8100 （前端: http://localhost:5173）" -ForegroundColor Green
} else {
    Write-Host "后端未在 30 秒内就绪，请查看 Django 窗口或 logs\app.log" -ForegroundColor Red
}
