# Дев-цели для Windows — зеркало Makefile (на винде нет make; см. README).
# Использование: .\dev.ps1 <цель>   (например: .\dev.ps1 test)
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "help", "venv", "install", "install-hud", "install-voice",
        "lint", "fmt", "typecheck", "test",
        "certs", "broker", "broker-down",
        "core", "desktop", "cli", "voice", "hud", "home"
    )]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Invoke-VenvPython {
    param([string[]]$ArgList)
    if (-not (Test-Path $venvPy)) {
        throw "Нет venv — сначала .\dev.ps1 install"
    }
    & $venvPy @ArgList
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Target) {
    "help" {
        Write-Host "Цели: install install-hud install-voice lint fmt typecheck test broker broker-down core desktop cli voice hud home"
    }
    "venv" {
        python -m venv .venv
    }
    "install" {
        if (-not (Test-Path $venvPy)) { python -m venv .venv }
        Invoke-VenvPython @("-m", "pip", "install", "-U", "pip")
        Invoke-VenvPython @("-m", "pip", "install", "-e", ".[dev,hud]")
    }
    "install-hud" {
        Invoke-VenvPython @("-m", "pip", "install", "-e", ".[dev,hud]")
    }
    "install-voice" {
        Invoke-VenvPython @("-m", "pip", "install", "-e", ".[dev,voice]")
        # openWakeWord с --no-deps: его tflite-runtime не собирается под py312, мы на ONNX
        Invoke-VenvPython @("-m", "pip", "install", "openwakeword>=0.6", "--no-deps")
    }
    "lint" { Invoke-VenvPython @("-m", "ruff", "check", "src", "tests") }
    "fmt" {
        Invoke-VenvPython @("-m", "black", "src", "tests")
        Invoke-VenvPython @("-m", "ruff", "check", "--fix", "src", "tests")
    }
    "typecheck" { Invoke-VenvPython @("-m", "mypy", "src") }
    "test" { Invoke-VenvPython @("-m", "pytest") }
    "certs" {
        if (-not (Test-Path "infra\certs\ca.crt")) {
            # скрипт генерации — bash (Git Bash ставится вместе с Git для Windows)
            bash infra/scripts/gen-certs.sh
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    }
    "broker" {
        & $PSCommandPath certs
        Push-Location infra
        try { docker compose up -d; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
        finally { Pop-Location }
    }
    "broker-down" {
        Push-Location infra
        try { docker compose down }
        finally { Pop-Location }
    }
    "core" { Invoke-VenvPython @("-m", "friday.core.app") }
    "desktop" { Invoke-VenvPython @("-m", "friday.agents.desktop.app") }
    "cli" { Invoke-VenvPython @("-m", "friday.cli.app") }
    "voice" { Invoke-VenvPython @("-m", "friday.agents.voice.app") }
    "hud" { Invoke-VenvPython @("-m", "friday.hud.app") }
    "home" { Invoke-VenvPython @("-m", "friday.agents.home.app") }
}
