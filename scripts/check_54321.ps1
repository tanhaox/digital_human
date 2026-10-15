$ErrorActionPreference = "SilentlyContinue"
$portInfo = Get-NetTCPConnection -LocalPort 54321 -State Listen
if ($portInfo) {
  foreach ($c in $portInfo) {
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p) {
      Write-Host ("PID={0} Name={1} Cmd={2}" -f $p.Id, $p.ProcessName, $p.Path)
    } else {
      Write-Host ("Orphan socket: OwningProcess={0} (no live process)" -f $c.OwningProcess)
    }
  }
} else {
  Write-Host "54321 free"
}