//go:build windows

package backend

import (
	"context"
	"encoding/json"
	"os/exec"
)

func antigravityAppEndpoints(ctx context.Context) ([]antigravityEndpoint, error) {
	// Match an App-published descriptor to the live, same-user App child and
	// its loopback sockets. Older App builds pass the handshake in arguments.
	script := `$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$session=(Get-Process -Id $PID).SessionId
$daemon=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.gemini\antigravity\daemon'
$processes=@(Get-CimInstance Win32_Process -Filter "Name='Antigravity.exe' OR Name LIKE 'language%server%.exe'")
$apps=@{}
foreach($app in $processes) {
  if($app.Name -ine 'Antigravity.exe' -or !$app.ExecutablePath -or $app.SessionId -eq 0 -or ($session -ne 0 -and $app.SessionId -ne $session)){continue}
  if((Invoke-CimMethod -InputObject $app -MethodName GetOwnerSid -ErrorAction SilentlyContinue).Sid -eq $sid){$apps[[int]$app.ProcessId]=$app}
}
$rows=@()
foreach($engine in $processes) {
  if($engine.Name -inotmatch '^language[-_]server.*\.exe$' -or !$engine.ExecutablePath){continue}
  $parent=$apps[[int]$engine.ParentProcessId]
  if(!$parent -or $engine.SessionId -ne $parent.SessionId -or $engine.CreationDate -lt $parent.CreationDate){continue}
  if((Invoke-CimMethod -InputObject $engine -MethodName GetOwnerSid -ErrorAction SilentlyContinue).Sid -ne $sid){continue}
  $appRoot=[IO.Path]::GetDirectoryName($parent.ExecutablePath).TrimEnd('\')+'\'
  if(-not $engine.ExecutablePath.StartsWith($appRoot,[StringComparison]::OrdinalIgnoreCase)){continue}
  if($engine.CommandLine -match '(?i)antigravity-ide|antigravity_cli|antigravity-cli'){continue}
  $ports=@(Get-NetTCPConnection -State Listen -OwningProcess $engine.ProcessId -ErrorAction SilentlyContinue |
           Where-Object LocalAddress -eq '127.0.0.1' | ForEach-Object {[int]$_.LocalPort} | Sort-Object -Unique)
  $endpoint=$null
  foreach($port in $ports) {
    try {
      $descriptorPath=Join-Path $daemon ('hub_'+$port+'.json')
      $file=Get-Item -LiteralPath $descriptorPath -ErrorAction SilentlyContinue
      if(!$file -or $file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
         $file.Length -gt 16384 -or $file.LastWriteTimeUtc -lt $engine.CreationDate.ToUniversalTime()){continue}
      $owner=(Get-Acl -LiteralPath $file.FullName).GetOwner([Security.Principal.SecurityIdentifier])
      if($owner.Value -ne $sid){continue}
      $descriptor=Get-Content -LiteralPath $file.FullName -Encoding UTF8 -Raw | ConvertFrom-Json
      if($descriptor.pid -ne $engine.ProcessId -or $descriptor.httpPort -ne $port -or
         $descriptor.httpsPort -eq $port -or $ports -notcontains $descriptor.httpsPort -or
         $descriptor.csrfToken -notmatch '^[A-Za-z0-9._~+\/=-]{16,512}$'){continue}
      $endpoint=@{port=[int]$descriptor.httpsPort;csrf=[string]$descriptor.csrfToken}
      break
    } catch {continue}
  }
  if($endpoint) {
    $rows+=@($endpoint)
  } elseif($engine.CommandLine -match '(?:^|\s)--csrf[-_]token[=\s]+"?([^\s"'']+)') {
    $csrf=$Matches[1]
    foreach($port in $ports) {$rows+=@{port=$port;csrf=$csrf}}
  }
}
ConvertTo-Json -InputObject @{running=$apps.Count -gt 0;endpoints=@($rows)} -Compress -Depth 4`
	command := exec.CommandContext(ctx, "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script)
	systemConfigureCommand(command)
	data, err := command.Output()
	if err != nil {
		return nil, errAntigravityQuotaUnavailable
	}
	var discovery struct {
		Running   bool                  `json:"running"`
		Endpoints []antigravityEndpoint `json:"endpoints"`
	}
	if err := json.Unmarshal(data, &discovery); err != nil {
		return nil, errAntigravityQuotaUnavailable
	}
	if !discovery.Running {
		return nil, errAntigravityAppClosed
	}
	if len(discovery.Endpoints) == 0 {
		return nil, errAntigravityQuotaUnavailable
	}
	return discovery.Endpoints, nil
}
