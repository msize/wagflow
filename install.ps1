<#
.SYNOPSIS
  Install WagFlow and its tools. Install WagTerm first. Safe to re-run: each step checks before it acts.

.DESCRIPTION
  Tools (skipped with -SkipTools), installing only what is missing:
    git, gh, python, node, go              via scoop
    WagTerm                              must already be installed from github.com/msize/wagterm,
                                         then its own agent skill and status hooks are installed
    Claude Code                          via its official installer
    Codex CLI                            via npm
    revmux, revdiff                      via go install (neither ships a Windows binary)

  Then WagFlow itself:
    ~/.claude/commands/wagflow-start.md          the /wagflow-start slash command
    ~/.claude/commands/wagflow-implementer.md    Claude's side of the loop when "implementer" is "claude"
    ~/.claude/commands/wagflow-triage.md         the issue triage judgment (-Triage)
    ~/.codex/skills/wagflow-implementer/         Codex's side of the loop
    this folder on your user PATH                so `wagflow` works in cmd and PowerShell
    wagflow.ps1 in this folder                   PowerShell's entry point (when it may run local scripts)
    ~/.wagflow.json                              created with defaults if it does not exist

  Anything that installs software asks first, unless you pass -Yes.

.PARAMETER Bypass
  Start Claude with --dangerously-skip-permissions in WagFlow sessions. Without it Claude asks
  before each shell command, and the loop stops at every one of them. Codex is sandboxed either way.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Yes -Bypass
#>
[CmdletBinding()]
param([switch] $Yes, [switch] $SkipTools, [switch] $Bypass)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\WagFlow.Common.ps1')

function Confirm-Step([string] $Question) {
    if ($Yes) { return $true }
    return ((Read-Host "$Question [y/N]") -match '^(y|yes)$')
}
function Test-Tool([string] $Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
}

function Install-Scoop {
    if (Test-Tool scoop) { return }
    if (-not (Confirm-Step "scoop (https://scoop.sh) is needed to install tools. Install it for this user?")) {
        throw "scoop is required for the missing tools; re-run with -SkipTools to skip them"
    }
    Invoke-RestMethod -Uri 'https://get.scoop.sh' | Invoke-Expression
    Update-SessionPath
}

function Install-WithScoop([string] $Command, [string] $Package) {
    if (Test-Tool $Command) { Write-Done "$Command present"; return }
    if (-not (Confirm-Step "$Command is missing. Install '$Package' with scoop?")) { Write-Warning "skipped $Command"; return }
    Install-Scoop
    scoop install $Package | Out-Host
    Update-SessionPath
}

function Install-GoTool([string] $Module, [string] $Name) {
    if (Test-Tool $Name) { Write-Done "$Name present"; return }
    if (-not (Test-Tool go)) { Write-Warning "$Name needs go, which is missing"; return }
    if (-not (Confirm-Step "$Name is missing. Build it with 'go install $Module@latest'?")) { Write-Warning "skipped $Name"; return }
    $gobin = (& go env GOBIN).Trim()
    if (-not $gobin) { $gobin = Join-Path (& go env GOPATH).Trim() 'bin' }
    & go install "$Module@latest"
    if ($LASTEXITCODE -ne 0) { throw "go install $Module failed" }
    # umputun's tools keep their main package in app/, so go names the binary app.exe
    Move-Item -Force -LiteralPath (Join-Path $gobin 'app.exe') -Destination (Join-Path $gobin "$Name.exe")
    Add-UserPath $gobin
    Write-Done "$Name built into $gobin"
}

function Add-UserPath([string] $Dir) {
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($user -split ';' | Where-Object { $_ })
    if ($parts -contains $Dir) { return }
    [Environment]::SetEnvironmentVariable('Path', (($parts + $Dir) -join ';'), 'User')
    Update-SessionPath
    Write-Done "added to your PATH: $Dir"
}

Write-Host "WagFlow install" -ForegroundColor Cyan

# --- tools -----------------------------------------------------------------------------------
if (-not $SkipTools) {
    Assert-WagTermInstalled
    Install-WithScoop git git
    Install-WithScoop gh gh
    Install-WithScoop python python
    Install-WithScoop node nodejs-lts
    Install-WithScoop go go

    Write-Done "WagTerm present"
    Install-WagtermIntegrations

    if (Test-Tool claude) { Write-Done "claude present" }
    elseif (Confirm-Step "Claude Code is missing. Run its official installer (claude.ai/install.ps1)?") {
        Invoke-RestMethod -Uri 'https://claude.ai/install.ps1' | Invoke-Expression
        Update-SessionPath
    }
    if (Test-Tool codex) { Write-Done "codex present" }
    elseif ((Test-Tool npm) -and (Confirm-Step "Codex CLI is missing. Install @openai/codex with npm?")) {
        & npm install -g '@openai/codex' | Out-Host
    }

    Install-GoTool 'github.com/umputun/revmux/app' 'revmux'
    Install-GoTool 'github.com/umputun/revdiff/app' 'revdiff'

    if (Test-Tool gh) {
        & gh auth status *> $null
        if ($LASTEXITCODE -ne 0) { Write-Warning "gh is not logged in: run 'gh auth login' before using WagFlow" }
    }
}

# --- the WagFlow ---------------------------------------------------------------------------
$claudeCommands = Join-Path $HOME '.claude\commands'
New-Item -ItemType Directory -Force -Path $claudeCommands | Out-Null
$commands = @{
    'start-github-issue.md' = 'wagflow-start.md'
    'triage-issue.md' = 'wagflow-triage.md'
    'wagflow-implementer.md' = 'wagflow-implementer.md'
}
foreach ($source in $commands.Keys) {
    Copy-Item -Force -LiteralPath (Join-Path $PSScriptRoot "claude\commands\$source") -Destination (Join-Path $claudeCommands $commands[$source])
}
Write-Done "claude: /wagflow-start, /wagflow-triage and /wagflow-implementer installed"

$codexSkills = Join-Path $HOME '.codex\skills'
foreach ($skill in Get-ChildItem -Directory (Join-Path $PSScriptRoot 'codex\skills')) {
    if (-not (Test-Path -LiteralPath (Join-Path $skill.FullName 'SKILL.md'))) { continue }
    $target = Join-Path $codexSkills $skill.Name
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -Force -Path (Join-Path $skill.FullName '*') -Destination $target -Recurse
    Write-Done "codex: skill $($skill.Name) installed"
}

Add-UserPath $PSScriptRoot
Install-PowerShellEntry $PSScriptRoot | Out-Null

$configPath = Join-Path $HOME '.wagflow.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    $claudeArgs = @()
    if ($Bypass) { $claudeArgs = @('--dangerously-skip-permissions') }
    [ordered]@{
        claudeArgs   = $claudeArgs
        codexArgs    = @()
        checkoutRoot = (Join-Path $HOME 'source\wagflow')
        allowNetwork = $false
    } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
    Write-Done "config written: $configPath"
} elseif ($Bypass) {
    Write-Warning "$configPath already exists; -Bypass did not change it. Edit claudeArgs there if you want it."
}

Write-Host ""
Write-Host "Done. Open a new terminal (for PATH), then:  wagflow <owner/repo#issue>" -ForegroundColor Cyan
