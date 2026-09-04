<#
.SYNOPSIS
    Sets up UQM MegaMod AI - POC on Windows.

.DESCRIPTION
    Checks prerequisites, fetches the pinned content, creates the Python
    virtual environment the game looks for, builds the game, and runs the
    sidecar's own preflight so a broken install says so here rather than
    halfway through a conversation.

    Safe to re-run: every step is skipped if it is already done.

.PARAMETER SkipBuild
    Don't build the game. Use when you already have UrQuanMasters.exe.

.PARAMETER SkipVoice
    Don't install the voice dependencies (torch and chatterbox-tts, several
    GB). Conversation still works; you just get subtitles, which is the
    default way to play anyway.

.PARAMETER WithVoice
    Install the voice dependencies.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -WithVoice
    .\install.ps1 -SkipBuild
#>
[CmdletBinding()]
param(
    [switch] $SkipBuild,
    [switch] $WithVoice,
    [switch] $SkipVoice
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot    = $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $RepoRoot
$ContentRepo = Join-Path $WorkspaceRoot 'uqm-megamod-content'
$AiDir       = Join-Path $RepoRoot 'ai'
$VenvDir     = Join-Path $AiDir '.venv'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
$ContentTag  = '0.8.5'
$ContentUrl  = 'https://github.com/JHGuitarFreak/UQM-MegaMod-Content.git'

$script:Failures = @()

function Write-Step   { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok     { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Skip   { param($m) Write-Host "    [--] $m" -ForegroundColor DarkGray }
function Write-Warn   { param($m) Write-Host "    [!!] $m" -ForegroundColor Yellow }
function Write-Fail   { param($m) Write-Host "    [XX] $m" -ForegroundColor Red
                        $script:Failures += $m }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

# Python 3.11+ for tomllib, which the character files are parsed with.
$python = $null
foreach ($candidate in @('python', 'py')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    try { $raw = & $candidate -c 'import sys; print("%d.%d" % sys.version_info[:2])' }
    catch { continue }
    $version = [version] $raw
    if ($version -ge [version] '3.11') {
        $python = $candidate
        Write-Ok "Python $raw ($($found.Source))"
        break
    }
    Write-Warn "$candidate is $raw; 3.11 or newer is needed for tomllib"
}
if (-not $python) {
    Write-Fail 'No Python 3.11+ found. Install the 64-bit build from https://python.org'
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Write-Ok "git ($($git.Source))" }
else { Write-Fail 'git not found. Install from https://git-scm.com' }

# MSBuild is only needed to build the game.
$msbuild = $null
if (-not $SkipBuild) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $msbuild = & $vswhere -latest -requires Microsoft.Component.MSBuild `
                              -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    }
    if (-not $msbuild) {
        foreach ($edition in 'Community', 'Professional', 'Enterprise', 'BuildTools') {
            $guess = Join-Path $env:ProgramFiles `
                "Microsoft Visual Studio\2022\$edition\MSBuild\Current\Bin\MSBuild.exe"
            if (Test-Path $guess) { $msbuild = $guess; break }
        }
    }
    if ($msbuild) { Write-Ok "MSBuild ($msbuild)" }
    else {
        Write-Fail ('MSBuild not found. Install "Desktop development with C++" from ' +
                    'Visual Studio 2022 (Community is free), or re-run with -SkipBuild')
    }
}

# Credentials are checked but never stored by this script.
$provider = if ($env:UQMAI_PROVIDER) { $env:UQMAI_PROVIDER } else { 'claude' }
switch ($provider) {
    'local' {
        Write-Ok "provider 'local' - no API key needed, nothing is billed"
    }
    'openai' {
        if ($env:OPENAI_API_KEY) { Write-Ok "provider 'openai', OPENAI_API_KEY is set" }
        else { Write-Warn "provider 'openai' but OPENAI_API_KEY is not set" }
    }
    default {
        if ($env:ANTHROPIC_API_KEY) { Write-Ok "provider 'claude', ANTHROPIC_API_KEY is set" }
        else {
            Write-Warn ('ANTHROPIC_API_KEY is not set. Conversation is billed to your ' +
                        'own Anthropic API account. No chat subscription (Claude ' +
                        'Pro/Max or ChatGPT) can be used. To play at no cost, set ' +
                        "UQMAI_PROVIDER=local and run a model with Ollama.")
        }
    }
}

if ($script:Failures.Count -gt 0) {
    Write-Host "`nCannot continue:" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Game content
# ---------------------------------------------------------------------------
# The sidecar reads the UNPACKED content tree to build each character's
# phrase table, so the repo is needed even though the game itself plays from
# the packaged .uqm archives.
Write-Step "Fetching game content (pinned at $ContentTag)"

if (Test-Path (Join-Path $ContentRepo 'base\comm')) {
    Write-Skip "already present at $ContentRepo"
} else {
    # autocrlf MUST be off. These are binary assets under names git will
    # happily treat as text: base/planets/alkali-med.ani has to be exactly
    # 30 bytes, and line-ending translation makes it 31 and corrupts it.
    & git -c core.autocrlf=false clone --depth 1 --branch $ContentTag $ContentUrl $ContentRepo
    if ($LASTEXITCODE -ne 0) { Write-Fail 'content clone failed'; exit 1 }
    & git -C $ContentRepo config core.autocrlf false
    Write-Ok "cloned to $ContentRepo"
}

$canary = Join-Path $ContentRepo 'base\planets\alkali-med.ani'
if (Test-Path $canary) {
    $size = (Get-Item $canary).Length
    if ($size -eq 30) {
        Write-Ok 'content integrity check passed'
    } else {
        Write-Fail ("content is CRLF-corrupted (alkali-med.ani is $size bytes, " +
                    'expected 30). Delete the content folder and re-run.')
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 3. Python environment
# ---------------------------------------------------------------------------
# The game looks for ai\.venv\Scripts\python.exe specifically and falls back
# to whatever `python` resolves to, so the venv must live exactly here.
Write-Step 'Setting up the Python sidecar'

if (Test-Path $VenvPython) {
    Write-Skip "virtual environment already at $VenvDir"
} else {
    & $python -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { Write-Fail 'venv creation failed'; exit 1 }
    Write-Ok "created $VenvDir"
}

& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet claude-agent-sdk
if ($LASTEXITCODE -ne 0) { Write-Fail 'claude-agent-sdk install failed'; exit 1 }
Write-Ok 'claude-agent-sdk installed'

& $VenvPython -m pip install --quiet pytest
Write-Ok 'pytest installed (for the test suite)'

$installVoice = $false
if ($WithVoice -and $SkipVoice) {
    Write-Fail 'pass either -WithVoice or -SkipVoice, not both'; exit 1
} elseif ($WithVoice) {
    $installVoice = $true
} elseif (-not $SkipVoice) {
    Write-Host ''
    Write-Host '    Voice synthesis needs torch and chatterbox-tts: several GB, and' -ForegroundColor DarkGray
    Write-Host '    it loads for ~23s on first use. Voice is OFF by default anyway.' -ForegroundColor DarkGray
    $answer = Read-Host '    Install voice support? (y/N)'
    $installVoice = $answer -match '^[Yy]'
}

if ($installVoice) {
    Write-Host '    installing torch and chatterbox-tts, this takes a while...'
    & $VenvPython -m pip install --quiet chatterbox-tts
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'voice install failed; conversation still works with subtitles'
    } else {
        Write-Ok 'voice support installed'
    }
} else {
    Write-Skip 'voice support not installed (subtitles only)'
}

# ---------------------------------------------------------------------------
# 4. Build
# ---------------------------------------------------------------------------
# Win32 only. The 32-bit build is why the AI runs as a separate process at
# all: a 64-bit Python cannot be loaded into it.
if ($SkipBuild) {
    Write-Step 'Skipping build (-SkipBuild)'
} else {
    Write-Step 'Building the game (Release, Win32)'
    $solution = Join-Path $RepoRoot 'build\msvs2019\UrQuanMastersMegaMod.sln'
    & $msbuild $solution /p:Configuration=Release /p:Platform=Win32 /v:minimal /nologo
    if ($LASTEXITCODE -ne 0) { Write-Fail 'build failed'; exit 1 }
    Write-Ok 'build succeeded'
}

# ---------------------------------------------------------------------------
# 4b. The launcher
# ---------------------------------------------------------------------------
# Compiled with the C# compiler that ships inside Windows, so this needs no
# SDK and the result needs no runtime: .NET Framework 4.x is present on every
# Windows 10 and 11. That is the whole reason the player-facing tool is a
# window rather than a script.
Write-Step 'Building the setup launcher'
$csc = Get-ChildItem `
    'C:\Windows\Microsoft.NET\Framework644.0.30319\csc.exe', `
    'C:\Windows\Microsoft.NET\Framework4.0.30319\csc.exe' `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if ($csc) {
    & $csc.FullName /nologo /target:winexe /optimize+ `
        /out:(Join-Path $RepoRoot 'UQMAI-Setup.exe') `
        /reference:System.dll /reference:System.Drawing.dll `
        /reference:System.Windows.Forms.dll `
        (Join-Path $RepoRoot 'launcher\Launcher.cs')
    if ($LASTEXITCODE -eq 0) { Write-Ok 'UQMAI-Setup.exe built' }
    else { Write-Warn 'launcher build failed; configure by hand instead' }
} else {
    Write-Warn 'no in-box C# compiler found; skipping the launcher'
}

$exe = Join-Path $RepoRoot 'UrQuanMasters.exe'
if (Test-Path $exe) { Write-Ok "game at $exe" }
else { Write-Warn "UrQuanMasters.exe not found at $exe" }

# ---------------------------------------------------------------------------
# 5. Verify
# ---------------------------------------------------------------------------
Write-Step 'Verifying the install'

Push-Location $AiDir
try {
    & $VenvPython -m pytest tests/ -q
    if ($LASTEXITCODE -eq 0) { Write-Ok 'test suite passed' }
    else { Write-Warn 'test suite failed; see output above' }
} finally {
    Pop-Location
}

$canRunLive = ($provider -eq 'local') -or
              ($provider -eq 'openai' -and $env:OPENAI_API_KEY) -or
              ($provider -eq 'claude' -and $env:ANTHROPIC_API_KEY)
if ($canRunLive) {
    Push-Location $AiDir
    try {
        # Makes one real request. Catches a rejected key, an empty balance or
        # a local server that was never started - all of which look healthy
        # until something is actually asked.
        & $VenvPython -m uqm_ai --provider $provider --preflight
        if ($LASTEXITCODE -eq 0) { Write-Ok 'live connectivity check passed' }
        else { Write-Warn 'live check failed; see output above' }
    } finally {
        Pop-Location
    }
} else {
    Write-Skip "live check skipped (no credentials for '$provider')"
}

# ---------------------------------------------------------------------------
Write-Host "`nDone." -ForegroundColor Cyan
Write-Host '  Run UQMAI-Setup.exe to choose an AI and play.' -ForegroundColor Cyan
Write-Host @"

  Play with AI conversation (default):
      .\UrQuanMasters.exe

  With synthesised voice:
      .\UrQuanMasters.exe --ai-voice

  As plain MegaMod, no AI:
      .\UrQuanMasters.exe --no-ai

  Pick a backend and give it credentials:
      `$env:ANTHROPIC_API_KEY = 'sk-ant-...'                       # Claude
      `$env:UQMAI_PROVIDER = 'openai'; `$env:OPENAI_API_KEY = '...'  # OpenAI
      `$env:UQMAI_PROVIDER = 'local'                                # free, via Ollama

  Keep a log when reporting a problem:
      .\UrQuanMasters.exe --logfile=game.log
"@
