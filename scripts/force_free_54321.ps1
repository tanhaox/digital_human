$ErrorActionPreference = "Continue"
Write-Host "=== Step 1: List 54321 listeners ==="
$listeners = Get-NetTCPConnection -LocalPort 54321 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $listeners) {
  Write-Host ("  OwningProcess={0} State={1}" -f $c.OwningProcess, $c.State)
}

Write-Host "=== Step 2: Force kill ==="
foreach ($c in $listeners) {
  $killPid = $c.OwningProcess
  Write-Host "Killing PID $killPid ..."
  $proc = $null
  try { $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $killPid) -ErrorAction Stop } catch {}
  if ($proc) {
    Write-Host ("  Found: Name={0} Cmd={1}" -f $proc.Name, $proc.CommandLine)
    Invoke-Expression "cmd.exe /c taskkill /F /T /PID $killPid"
  } else {
    Write-Host "  No live process — orphan socket. Trying netsh int ipv4 reset? (no, requires reboot)"
  }
}

Start-Sleep -Seconds 5
Write-Host "=== Step 3: Recheck ==="
$listeners2 = Get-NetTCPConnection -LocalPort 54321 -State Listen -ErrorAction SilentlyContinue
if (-not $listeners2) { Write-Host "54321 FREE!" }
else { foreach ($c in $listeners2) { Write-Host ("  Still listening: OwningProcess={0}" -f $c.OwningProcess) } }