# One-time setup on Windows: checks Docker Desktop, creates the local config
# files from the templates, asks for the Discord values, then starts the bot and
# Ollama. Safe to run again: existing files and values are kept.
# Start it by double-clicking setup.cmd (that bypasses the script execution policy).

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$MinVmGb = 22          # the default model needs about 20 GB inside Docker's VM
$DefaultModel = "qwen3.6:35b-a3b"
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Say($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "error: $text" -ForegroundColor Red; exit 1 }

# docker writes progress to stderr; with Stop that would abort, so run it plainly.
function Docker-Ok([string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker @arguments *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $previous
    return $ok
}

# ---------------------------------------------------------------------------
# 1. Docker Desktop
# ---------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Say "Docker Desktop is not installed."
    $answer = Read-Host "Install it now with winget? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[Yy]") {
        winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        Say "Installed. Start Docker Desktop from the Start menu, wait until it says it is running, then run setup.cmd again. A reboot may be needed first."
        exit 0
    }
    Fail "Docker Desktop is required: https://docs.docker.com/desktop/setup/install/windows-install/"
}
if (-not (Docker-Ok @("info"))) {
    Fail "Docker is installed but not running. Start Docker Desktop, wait until it says it is running, then run setup.cmd again."
}
if (-not (Docker-Ok @("compose", "version"))) {
    Fail "docker compose is missing; update Docker Desktop."
}

# ---------------------------------------------------------------------------
# 2. Local config files from the templates (never overwritten)
# ---------------------------------------------------------------------------
$pairs = @(
    @(".env.example", ".env"),
    @("sources.example.yml", "sources.yml"),
    @("digest.example.yml", "digest.yml")
)
foreach ($pair in $pairs) {
    $template, $target = $pair
    if (Test-Path $target -PathType Container) {
        # docker leaves an empty directory behind when a mounted file was missing
        if ((Get-ChildItem $target -Force | Measure-Object).Count -gt 0) {
            Fail "$target is a folder with files in it; move it away and run again"
        }
        Remove-Item $target
    }
    if (-not (Test-Path $target)) {
        Copy-Item $template $target
        Write-Host "created $target from $template"
    }
}
New-Item -ItemType Directory -Force -Path data | Out-Null

# .env is read on Linux inside the container: written as UTF-8 without BOM, LF line endings.
function Read-Env { return [IO.File]::ReadAllText((Join-Path $PSScriptRoot ".env")) -split '\r?\n' }
function Write-Env($lines) {
    $text = (($lines | Where-Object { $_ -ne $null }) -join "`n").TrimEnd("`n") + "`n"
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $text, $Utf8NoBom)
}
function Get-EnvValue($key) {
    foreach ($line in Read-Env) {
        if ($line -match "^$key=(.*)$") { return $Matches[1].Trim() }
    }
    return ""
}
function Set-EnvValue($key, $value) {
    $lines = @(Read-Env)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$key=") { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines += "$key=$value" }
    Write-Env $lines
}

# Asks only when the value in .env is still empty.
function Ask($key, $prompt, $pattern, [switch]$Optional) {
    if (Get-EnvValue $key) { return }
    while ($true) {
        $value = (Read-Host $prompt).Trim()
        if ($value -eq "" -and $Optional) { return }
        if ($value -match $pattern) { Set-EnvValue $key $value; return }
        Write-Host "  that does not look right, try again"
    }
}

Write-Env (Read-Env)   # normalise line endings once, in case git converted them
Say "Discord values (see the README for where each one is)"
Ask "DISCORD_TOKEN"          "Bot token"                                '^[A-Za-z0-9._-]{50,}$'
Ask "DISCORD_CHANNEL_ID"     "Channel ID to post in"                    '^[0-9]{15,21}$'
Ask "DISCORD_GUILD_ID"       "Server ID (optional, Enter to skip)"      '^[0-9]{15,21}$' -Optional
Ask "DISCORD_APPLICATION_ID" "Application ID (optional, Enter to skip)" '^[0-9]{15,21}$' -Optional

# ---------------------------------------------------------------------------
# 3. Model
# ---------------------------------------------------------------------------
$digest = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "digest.yml"))
$current = ""
if ($digest -match '(?m)^model:[ \t]*(\S+)') { $current = $Matches[1] }
if (-not $current) { $current = $DefaultModel }
Say "Ollama model for the daily digest. Check the tag exists at https://ollama.com/library/qwen3.6"
$model = (Read-Host "Model tag [$current]").Trim()
if ($model) {
    $digest = [regex]::Replace($digest, '(?m)^model:.*$', "model: $model")
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot "digest.yml"), $digest, $Utf8NoBom)
}

$previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$vmBytes = [int64](& docker info --format "{{.MemTotal}}" 2> $null)
$ErrorActionPreference = $previous
$vmGb = [math]::Floor($vmBytes / 1GB)
if ($vmGb -lt $MinVmGb) {
    Say "Docker's VM has $vmGb GB of RAM; the default model needs about 20 GB while the digest builds."
    Write-Host "  Raise it in %UserProfile%\.wslconfig:  [wsl2]  memory=32GB  then run: wsl --shutdown"
}

# ---------------------------------------------------------------------------
# 4. Start
# ---------------------------------------------------------------------------
Say "Building and starting the bot and Ollama (the first build takes a few minutes)"
$ErrorActionPreference = "Continue"
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed; the output above says why" }

Say "Running. The bot downloads the model on first start (about 20 GB, once)."
Write-Host "  Logs:          docker compose logs -f newsbot"
Write-Host "  Test feeds:    docker compose run --rm newsbot python -m newsbot check"
Write-Host "  Test digest:   docker compose run --rm newsbot python -m newsbot digest"
Write-Host "  Edit:          sources.yml / digest.yml, then /reload in Discord"
Write-Host "                 .env, then: docker compose up -d"
Write-Host "Both containers start again with Docker Desktop."
