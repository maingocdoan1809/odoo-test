param(
    [string]$Config = 'C:\Users\maido\odoo-dev\odoo.conf',
    [string]$OdooPython = 'C:\Users\maido\odoo-venv\Scripts\python.exe',
    [string]$OdooBin = 'C:\Users\maido\odoo\odoo-bin',
    [string]$Url = 'http://127.0.0.1:8079',
    [string]$OutputDirectory = '',
    [switch]$AgentPilot,
    [switch]$Matrix,
    [switch]$Complex,
    [switch]$Replay,
    [string]$Cases = ''
)
$ErrorActionPreference = 'Stop'
$addonsLine = Get-Content -LiteralPath $Config | Where-Object { $_ -match '^\s*addons_path\s*=' } | Select-Object -First 1
$addons = (($addonsLine -split '=', 2)[1]).Trim() + ',' + (Join-Path $PSScriptRoot 'addons')
$variables = @(
    'OQA_SMOKE_PASSWORD', 'OQA_SMOKE_MAPPER', 'OQA_SMOKE_URL', 'OQA_SMOKE_ITEM_ACTION',
    'OQA_SMOKE_NODE_MODULES', 'OQA_SMOKE_OUT', 'OQA_SMOKE_DISPATCH', 'OQA_MATRIX_CASES', 'OQA_MATRIX_REPLAY'
)
$previous = @{}
foreach ($variable in $variables) { $previous[$variable] = [Environment]::GetEnvironmentVariable($variable) }
try {
    $env:OQA_SMOKE_PASSWORD = [guid]::NewGuid().ToString('N')
    $env:OQA_SMOKE_MAPPER = 'oqa-smoke-mapper'
    $env:OQA_SMOKE_URL = $Url
    $env:OQA_SMOKE_OUT = $OutputDirectory
    $env:OQA_MATRIX_CASES = $Cases
    $env:OQA_MATRIX_REPLAY = if ($Replay) { '1' } else { '0' }
    $env:OQA_SMOKE_DISPATCH = if ($AgentPilot) { '1' } else { '0' }
    $npmRoot = npm.cmd root -g
    if ($LASTEXITCODE) { throw 'Could not find the installed Playwright CLI runtime.' }
    $env:OQA_SMOKE_NODE_MODULES = Join-Path $npmRoot '@playwright/cli/node_modules'
    $result = @'
import os
from odoo import Command

users = env["res.users"].with_context(no_reset_password=True)
for role in ("manager", "mapper", "operator"):
    login = f"oqa-smoke-{role}"
    group = "operator" if role == "operator" else "manager"
    groups = [env.ref("base.group_user").id, env.ref(f"oqa_harness.group_{group}").id]
    if role == "mapper":
        groups.append(env.ref("base.group_system").id)
    if role == "manager":
        groups.append(env.ref("base.group_allow_export").id)       # Odoo grants Export only to this group (not to internal users): T01 and T04 export as Manager
    values = {
        "name": f"OQA Smoke {role.title()}", "login": login,
        "password": os.environ["OQA_SMOKE_PASSWORD"], "group_ids": [Command.set(groups)],
    }
    existing = users.search([("login", "=", login)])
    existing.write(values) if existing else users.create(values)
env.cr.commit()
print(env.ref("oqa_harness.item_action").id)
'@
    $accountScript = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($accountScript, $result, [Text.UTF8Encoding]::new($false))      # a pipe from Windows PowerShell 5.1 starts with a byte-order mark that Odoo's shell rejects
    $result = cmd.exe /c "`"$OdooPython`" `"$OdooBin`" shell -c `"$Config`" --addons-path `"$addons`" -d oqa_harness --no-http --log-level error < `"$accountScript`""
    Remove-Item -LiteralPath $accountScript -ErrorAction SilentlyContinue
    if ($LASTEXITCODE) { throw 'Could not prepare the smoke accounts in oqa_harness.' }
    $env:OQA_SMOKE_ITEM_ACTION = $result | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1
    if (-not $env:OQA_SMOKE_ITEM_ACTION) { throw 'Install oqa_harness before running the browser smoke.' }
    if ($AgentPilot) { Remove-Item Env:OQA_SMOKE_MAPPER }
    $runner = if ($Complex) { 'run_complex.py' } else { if ($Matrix) { 'run_matrix.py' } else { 'smoke_browser.py' } }
    & python (Join-Path $PSScriptRoot $runner)
    if ($LASTEXITCODE) { throw 'Odoo browser smoke failed.' }
} finally {
    foreach ($variable in $variables) { [Environment]::SetEnvironmentVariable($variable, $previous[$variable]) }
}
