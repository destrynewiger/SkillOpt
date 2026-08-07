# Install the SkillOpt-Sleep Codex integration as a user-level Codex skill on Windows.
# Idempotent; prints what it does.

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$AgentsSkills = Join-Path $env:USERPROFILE ".agents\skills"
$LegacyPrompt = Join-Path $CodexHome "prompts\sleep.md"

Write-Output "[install] repo: $RepoRoot"

# 1) user-level skill
$SkillDir = Join-Path $AgentsSkills "skillopt-sleep"
if (-not (Test-Path $SkillDir)) {
    New-Item -ItemType Directory -Path $SkillDir -Force | Out-Null
}
Copy-Item (Join-Path $RepoRoot "plugins\codex\skills\skillopt-sleep\SKILL.md") (Join-Path $SkillDir "SKILL.md") -Force
Write-Output "[install] skill           -> $(Join-Path $SkillDir 'SKILL.md')"

# 2) retire the old custom prompt entrypoint from previous installs
if (Test-Path $LegacyPrompt) {
    $Backup = "${LegacyPrompt}.skillopt-legacy.bak"
    if (Test-Path $Backup) {
        $DateStr = Get-Date -Format "yyyyMMddHHmmss"
        $Backup = "${LegacyPrompt}.skillopt-legacy.${DateStr}.bak"
    }
    Move-Item $LegacyPrompt $Backup -Force
    Write-Output "[install] legacy prompt  -> $Backup"
}

# 3) record the repo location so the runner is found from anywhere
Write-Output "[install] add to your environment variables:"
Write-Output "    [System.Environment]::SetEnvironmentVariable('SKILLOPT_SLEEP_REPO', '$RepoRoot', 'User')"
Write-Output "    Or set it via System Properties."

# 4) optional: append an AGENTS.md hint (only if the user opts in)
Write-Output ""
Write-Output "[install] Optional — add this to ~/.codex/AGENTS.md so Codex always knows the tool:"
Write-Output ""
Write-Output "  ## SkillOpt-Sleep"
Write-Output "  Use the skillopt-sleep skill when I ask to run a sleep/dream/offline"
Write-Output "  self-improvement cycle. The runner is:"
Write-Output "  \`powershell -File `"$RepoRoot\plugins\run-sleep.ps1`" status --project `"\$(pwd)\`"\`."
Write-Output ""
Write-Output "Done. Try asking Codex:"
Write-Output "  Use the skillopt-sleep skill to run status for this project."
