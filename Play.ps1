<#
.SYNOPSIS
    Configure and launch UQM MegaMod AI - POC.

.DESCRIPTION
    One place to check prerequisites, choose which AI answers the aliens, turn
    voice on or off, install whatever is missing, and start the game. Settings
    are written to uqmai.toml beside the game's own configuration, so nothing
    depends on remembering environment variables.

    Run it with no arguments and it shows a menu.

.PARAMETER Play
    Skip the menu and launch straight away with the saved settings.

.PARAMETER Configure
    Show the menu even if everything is already set up.

.EXAMPLE
    .\Play.ps1
    .\Play.ps1 -Play
#>
[CmdletBinding()]
param(
    [switch] $Play,
    [switch] $Configure
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot   = $PSScriptRoot
$AiDir      = Join-Path $RepoRoot 'ai'
$VenvPython = Join-Path $AiDir '.venv\Scripts\python.exe'
$Exe        = Join-Path $RepoRoot 'UrQuanMasters.exe'
$ConfigDir  = Join-Path $env:APPDATA 'uqm-megamod'
$ConfigFile = Join-Path $ConfigDir 'uqmai.toml'

# ---------------------------------------------------------------------------
# Settings file
# ---------------------------------------------------------------------------
function Read-Settings {
    $settings = @{
        provider         = 'claude'
        api_key          = ''
        model            = ''
        base_url         = ''
        use_subscription = $false
        voice            = $false
    }
    if (-not (Test-Path $ConfigFile)) { return $settings }

    # Deliberately a plain reader, not a TOML parser: this file is only ever
    # written by this script, and the sidecar does the real parsing.
    foreach ($line in Get-Content $ConfigFile) {
        if ($line -match '^\s*#') { continue }
        if ($line -notmatch '^\s*([A-Za-z_]+)\s*=\s*(.+?)\s*$') { continue }
        $key = $Matches[1]; $raw = $Matches[2]
        if (-not $settings.ContainsKey($key)) { continue }
        if ($raw -eq 'true')      { $settings[$key] = $true }
        elseif ($raw -eq 'false') { $settings[$key] = $false }
        else { $settings[$key] = $raw.Trim('"') }
    }
    return $settings
}

function Write-Settings($settings) {
    if (-not (Test-Path $ConfigDir)) {
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    }
    $lines = @(
        '# UQM MegaMod AI - written by Play.ps1',
        '# The environment overrides anything here.',
        '',
        ('provider = "{0}"' -f $settings.provider)
    )
    foreach ($key in 'api_key', 'model', 'base_url') {
        if ($settings[$key]) { $lines += ('{0} = "{1}"' -f $key, $settings[$key]) }
    }
    $lines += ('use_subscription = {0}' -f $settings.use_subscription.ToString().ToLower())
    $lines += ('voice = {0}' -f $settings.voice.ToString().ToLower())

    # UTF-8 without a BOM: tomllib rejects a BOM.
    [System.IO.File]::WriteAllLines(
        $ConfigFile, $lines, (New-Object System.Text.UTF8Encoding $false))
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
function Test-Prereqs($settings) {
    $missing = @()

    if (-not (Test-Path $Exe)) {
        $missing += @{ what = 'The game is not built'
                       fix  = 'Run .\install.ps1' }
    }
    if (-not (Test-Path $VenvPython)) {
        $missing += @{ what = 'The Python sidecar is not set up'
                       fix  = 'Run .\install.ps1' }
    } else {
        & $VenvPython -c 'import claude_agent_sdk' 2>$null
        if ($LASTEXITCODE -ne 0 -and $settings.provider -eq 'claude') {
            $missing += @{ what = 'claude-agent-sdk is not installed'
                           fix  = 'Install it from this menu (option 4)' }
        }
        if ($settings.voice) {
            & $VenvPython -c 'import chatterbox' 2>$null
            if ($LASTEXITCODE -ne 0) {
                $missing += @{ what = 'Voice is on but chatterbox-tts is not installed'
                               fix  = 'Install it from this menu (option 3)' }
            }
        }
    }

    $content = Join-Path (Split-Path -Parent $RepoRoot) 'uqm-megamod-content\base\comm'
    if (-not (Test-Path $content)) {
        $missing += @{ what = 'Game content is missing'
                       fix  = 'Run .\install.ps1' }
    }

    switch ($settings.provider) {
        'claude' {
            if (-not $settings.api_key -and -not $env:ANTHROPIC_API_KEY `
                -and -not $settings.use_subscription) {
                $missing += @{ what = 'No Anthropic credentials'
                               fix  = 'Choose an AI from this menu (option 1)' }
            }
        }
        'openai' {
            if (-not $settings.api_key -and -not $env:OPENAI_API_KEY) {
                $missing += @{ what = 'No OpenAI API key'
                               fix  = 'Choose an AI from this menu (option 1)' }
            }
        }
    }
    return $missing
}

function Show-Status($settings) {
    Write-Host ''
    Write-Host '  The Ur-Quan Masters MegaMod AI' -ForegroundColor Cyan
    Write-Host '  proof of concept' -ForegroundColor DarkGray
    Write-Host ''

    $who = switch ($settings.provider) {
        'claude' { if ($settings.use_subscription) { 'Claude (your subscription)' }
                   else { 'Claude (API key)' } }
        'openai' { 'OpenAI (API key)' }
        'local'  { $m = if ($settings.model) { $settings.model } else { 'llama3.1:8b' }
                   "Local model - $m, free" }
        'mock'   { 'None (scripted replies, for testing)' }
        default  { $settings.provider }
    }
    Write-Host ("    AI     : {0}" -f $who)
    Write-Host ("    Voice  : {0}" -f $(if ($settings.voice) { 'on' } else { 'off (subtitles)' }))
    Write-Host ("    Config : {0}" -f $ConfigFile) -ForegroundColor DarkGray

    $missing = Test-Prereqs $settings
    if ($missing.Count -eq 0) {
        Write-Host '    Status : ready' -ForegroundColor Green
    } else {
        Write-Host '    Status : not ready' -ForegroundColor Yellow
        foreach ($m in $missing) {
            Write-Host ("             - {0} ({1})" -f $m.what, $m.fix) -ForegroundColor Yellow
        }
    }
    Write-Host ''
    return $missing
}

# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------
function Select-Provider($settings) {
    Write-Host ''
    Write-Host '  Which AI should answer the aliens?' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    1. Claude, with an API key      - billed per conversation'
    Write-Host '    2. Claude, with my subscription - personal use only, see below'
    Write-Host '    3. OpenAI, with an API key      - billed per conversation'
    Write-Host '    4. A local model (Ollama)       - free, private, no account'
    Write-Host '    5. None - play as plain MegaMod'
    Write-Host ''
    $choice = Read-Host '  Choose 1-5'

    switch ($choice) {
        '1' {
            $settings.provider = 'claude'; $settings.use_subscription = $false
            Write-Host ''
            Write-Host '  Create a key at https://console.anthropic.com/settings/keys'
            $key = Read-Host '  Paste it here (blank to keep the current one)'
            if ($key) { $settings.api_key = $key.Trim() }
        }
        '2' {
            Write-Host ''
            Write-Host '  This uses the Claude CLI you are already signed in to.' -ForegroundColor Yellow
            Write-Host '  Anthropic does not permit a distributed product to sign its' -ForegroundColor Yellow
            Write-Host '  users in to claude.ai, so this is for YOUR OWN machine and' -ForegroundColor Yellow
            Write-Host '  your own account. Do not ship a build with it enabled.' -ForegroundColor Yellow
            Write-Host ''
            Write-Host '  It needs the Claude CLI installed and signed in:'
            Write-Host '      npm install -g @anthropic-ai/claude-code   then   claude  ->  /login'
            Write-Host ''
            $ok = Read-Host '  Understood? (y/N)'
            if ($ok -match '^[Yy]') {
                $settings.provider = 'claude'; $settings.use_subscription = $true
            } else {
                Write-Host '  Left unchanged.' -ForegroundColor DarkGray
            }
        }
        '3' {
            $settings.provider = 'openai'; $settings.use_subscription = $false
            Write-Host ''
            Write-Host '  Create a key at https://platform.openai.com/api-keys'
            Write-Host '  (a ChatGPT Plus/Pro subscription does NOT include this)' -ForegroundColor DarkGray
            $key = Read-Host '  Paste it here (blank to keep the current one)'
            if ($key) { $settings.api_key = $key.Trim() }
            $model = Read-Host '  Model (blank for gpt-4o)'
            if ($model) { $settings.model = $model.Trim() }
        }
        '4' {
            $settings.provider = 'local'; $settings.use_subscription = $false
            Write-Host ''
            Write-Host '  Needs a local server. The easiest is Ollama:'
            Write-Host '      https://ollama.com  then  ollama pull llama3.1:8b'
            Write-Host ''
            $model = Read-Host '  Model (blank for llama3.1:8b)'
            if ($model) { $settings.model = $model.Trim() }
            $url = Read-Host '  Server URL (blank for http://localhost:11434/v1)'
            if ($url) { $settings.base_url = $url.Trim() }
            Write-Host ''
            Write-Host '  Note: only Claude has actually been playtested.' -ForegroundColor DarkGray
        }
        '5' { $settings.provider = 'mock' }
        default { Write-Host '  Left unchanged.' -ForegroundColor DarkGray }
    }
    return $settings
}

function Install-Package($package, $label) {
    if (-not (Test-Path $VenvPython)) {
        Write-Host '  The sidecar is not set up yet - run .\install.ps1 first.' -ForegroundColor Red
        return
    }
    Write-Host "  Installing $label..." -ForegroundColor Cyan
    & $VenvPython -m pip install --upgrade $package
    if ($LASTEXITCODE -eq 0) { Write-Host "  $label installed." -ForegroundColor Green }
    else { Write-Host "  $label failed to install." -ForegroundColor Red }
}

function Start-Game($settings) {
    if (-not (Test-Path $Exe)) {
        Write-Host '  The game is not built. Run .\install.ps1 first.' -ForegroundColor Red
        return
    }
    $gameArgs = @()
    if ($settings.provider -eq 'mock') { $gameArgs += '--no-ai' }
    if ($settings.voice) { $gameArgs += '--ai-voice' }

    Write-Host ''
    $shown = if ($gameArgs) { ' ' + ($gameArgs -join ' ') } else { '' }
    Write-Host "  Launching$shown" -ForegroundColor Cyan
    # From the repo root: the game resolves the sidecar as 'ai' relative to
    # the executable.
    Push-Location $RepoRoot
    try { & $Exe @gameArgs } finally { Pop-Location }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
$settings = Read-Settings

if ($Play -and -not $Configure) {
    Start-Game $settings
    return
}

while ($true) {
    $missing = Show-Status $settings

    Write-Host '    1. Choose which AI answers'
    Write-Host ('    2. Voice: turn {0}' -f $(if ($settings.voice) { 'OFF' } else { 'ON' }))
    Write-Host '    3. Install voice support (large download)'
    Write-Host '    4. Install / repair the AI packages'
    Write-Host '    5. Test the connection'
    Write-Host '    6. Play'
    Write-Host '    Q. Quit'
    Write-Host ''
    $choice = Read-Host '  Choose'

    switch ($choice.ToUpper()) {
        '1' { $settings = Select-Provider $settings; Write-Settings $settings }
        '2' {
            $settings.voice = -not $settings.voice
            Write-Settings $settings
            if ($settings.voice) {
                Write-Host '  Voice on. It needs chatterbox-tts (option 3).' -ForegroundColor DarkGray
            }
        }
        '3' { Install-Package 'chatterbox-tts' 'voice support' }
        '4' { Install-Package 'claude-agent-sdk' 'the Claude SDK' }
        '5' {
            if (-not (Test-Path $VenvPython)) {
                Write-Host '  Run .\install.ps1 first.' -ForegroundColor Red
            } else {
                Push-Location $AiDir
                try {
                    # One real request, so a wrong key or a local server that
                    # was never started is caught here and not mid-sentence.
                    & $VenvPython -m uqm_ai --provider $settings.provider --preflight
                } finally { Pop-Location }
            }
        }
        '6' {
            if ($missing.Count -gt 0) {
                Write-Host '  Some things are not ready. Start anyway?' -ForegroundColor Yellow
                $go = Read-Host '  (y/N)'
                if ($go -notmatch '^[Yy]') { continue }
            }
            Start-Game $settings
        }
        'Q' { return }
        default { }
    }
}
