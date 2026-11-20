//go:build windows

package backend

import (
	"context"
	"encoding/json"
	"errors"
	"os/exec"
)

func antigravityAppEndpoints(ctx context.Context) ([]antigravityEndpoint, error) {
	// Ownership, direct App ancestry and the listener PID are verified together.
	// No command line, CSRF token or account metadata is logged.
	script := `$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rows=@()
foreach($engine in @(Get-CimInstance Win32_Process -Filter "Name LIKE 'language%server%.exe'")) {
  $parent=Get-CimInstance Win32_Process -Filter "ProcessId=$($engine.ParentProcessId)"
  if(-not $parent -or $parent.Name -ine 'Antigravity.exe'){continue}
  if((Invoke-CimMethod -InputObject $engine -MethodName GetOwnerSid).Sid -ne $sid -or (Invoke-CimMethod -InputObject $parent -MethodName GetOwnerSid).Sid -ne $sid){continue}
  $appRoot=[IO.Path]::GetDirectoryName($parent.ExecutablePath).TrimEnd('\')+'\'
  if(-not $engine.ExecutablePath.StartsWith($appRoot,[StringComparison]::OrdinalIgnoreCase)){continue}
  if($engine.CommandLine -match '(?i)antigravity-ide|antigravity_cli|antigravity-cli'){continue}
  if($engine.CommandLine -notmatch '--csrf_token[=\s]+([^\s"'']+)'){continue}
  $csrf=$Matches[1]
  foreach($socket in @(Get-NetTCPConnection -State Listen -OwningProcess $engine.ProcessId -ErrorAction SilentlyContinue)) {
    if($socket.LocalAddress -eq '127.0.0.1'){$rows+=@{port=[int]$socket.LocalPort;csrf=$csrf}}
  }
}
ConvertTo-Json -InputObject @($rows) -Compress`
	command := exec.CommandContext(ctx, "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script)
	systemConfigureCommand(command)
	data, err := command.Output()
	if err != nil {
		return nil, errors.New("无法读取 Antigravity App 本地服务")
	}
	var endpoints []antigravityEndpoint
	if json.Unmarshal(data, &endpoints) != nil {
		return nil, errors.New("Antigravity App 本地服务信息无效")
	}
	return endpoints, nil
}
