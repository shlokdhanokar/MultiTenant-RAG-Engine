# Retries `terraform apply` until Oracle grants A1.Flex capacity.
#
# "Out of host capacity" on Always Free A1 shapes is a queueing problem, not a
# config error — capacity frees up unpredictably. Networking is already built,
# so each attempt only tries to create the instance.
#
# Usage:  .\retry-apply.ps1  [-IntervalSeconds 300]  [-MaxAttempts 0]
# Ctrl+C to stop. Safe to re-run; Terraform is idempotent.

param(
    [int]$IntervalSeconds = 300,
    [int]$MaxAttempts     = 0      # 0 = unlimited
)

Set-Location $PSScriptRoot

$attempt = 0
while ($true) {
    $attempt++
    $stamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$stamp] Attempt $attempt ..." -ForegroundColor Cyan

    $output = terraform apply -auto-approve -input=false -no-color 2>&1
    $output | Select-Object -Last 5

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "SUCCESS - instance created." -ForegroundColor Green
        terraform output -no-color
        break
    }

    if ($output -match "Out of host capacity|OutOfCapacity|LimitExceeded") {
        Write-Host "[$stamp] No capacity yet. Retrying in $IntervalSeconds s." -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "Apply failed for a reason other than capacity - stopping." -ForegroundColor Red
        Write-Host "Fix the error above before re-running." -ForegroundColor Red
        $output | Select-Object -Last 25
        break
    }

    if ($MaxAttempts -gt 0 -and $attempt -ge $MaxAttempts) {
        Write-Host "Reached MaxAttempts ($MaxAttempts). Stopping." -ForegroundColor Yellow
        break
    }

    Start-Sleep -Seconds $IntervalSeconds
}
