from __future__ import annotations

from sa_home_node.fixups.core import Fixup, FixupContext, FixupError
from sa_home_node.fixups.privileged import find_executable, run_user
from sa_home_node.lifecycle.update import (
    PACKAGE_NAME,
    installed_extras,
    origin_repo_url,
    pipx_home,
)

TASK_NAME = "sa-home-node-autoupdate"
SCRIPT_NAME = "sa-home-node-autoupdate.ps1"
RUN_TIME = "04:00"
ENCODING = "utf-8-sig"

SCRIPT = r'''$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$RepoUrl = '@REPO@'
$GitExe = '@GIT@'
$PipxExe = '@PIPX@'
$PipxHome = '@PIPXHOME@'
$Package = '@PACKAGE@'
$Spec = '@SPEC@'
$ServiceName = 'sa-home-node'
$StopTimeoutSec = 120
$env:PIPX_HOME = $PipxHome

function Log($msg) {
    Write-Output "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
}

$tagRefs = & $GitExe ls-remote --tags --refs $RepoUrl 2>$null
if (-not $tagRefs) {
    Log "git ls-remote не вернул тегов — пропускаю."
    exit 0
}
$tags = $tagRefs | ForEach-Object { ($_ -split "refs/tags/")[-1] } | Where-Object { $_ -match '^v\d+(\.\d+)*$' }
if (-not $tags) {
    Log "Тегов формата vX.Y.Z нет — пропускаю."
    exit 0
}
$latestTag = $tags | Sort-Object { [version]($_ -replace '^v', '') } | Select-Object -Last 1
$latestVersion = $latestTag.TrimStart('v')

$pipxJson = & $PipxExe list --json | ConvertFrom-Json
$installed = $pipxJson.venvs.$Package.metadata.main_package.package_version
if (-not $installed) {
    Log "ОШИБКА: $Package не найден в pipx list."
    exit 1
}
Log "Установлено: $installed / В репозитории: $latestVersion"
if ($installed -eq $latestVersion) {
    Log "Уже последняя версия."
    exit 0
}

Log "Останавливаю службу $ServiceName..."
Stop-Service -Name $ServiceName -ErrorAction Stop
$deadline = (Get-Date).AddSeconds($StopTimeoutSec)
while ((Get-Service $ServiceName).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if ((Get-Service $ServiceName).Status -ne 'Stopped') {
    Log "ОШИБКА: служба не остановилась за ${StopTimeoutSec}s, обновление не выполнено."
    exit 1
}

Log "pipx install --force до $latestTag..."
$installOk = $false
$installOutput = $null
try {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $installOutput = & $PipxExe install --force "$Spec@$latestTag" 2>&1
        $installOk = $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $prevEap
    }
} catch {
    $installOutput = $_
}
Log ($installOutput -join "`n")

Log "Запускаю службу $ServiceName..."
Start-Service -Name $ServiceName

if (-not $installOk) {
    Log "ОШИБКА: pipx install завершился неуспешно, служба запущена на прежней версии."
    exit 1
}
Log "Обновление до $latestTag установлено."
exit 0
'''


def _quote(value: str) -> str:
    return value.replace("'", "''")


def _tool(name: str) -> str:
    found = find_executable(name)
    if found is None:
        raise FixupError(f"{name} не найден в PATH")
    return found


def render_script() -> str:
    repo = origin_repo_url()
    if repo is None:
        raise FixupError("адрес репозитория неизвестен: пакет поставлен не из git (pipx install git+...)")
    extras = installed_extras()
    spec = f"{PACKAGE_NAME}[{extras}] @ git+{repo}" if extras else f"{PACKAGE_NAME} @ git+{repo}"
    values = {
        "@REPO@": repo,
        "@GIT@": _tool("git"),
        "@PIPX@": _tool("pipx"),
        "@PIPXHOME@": pipx_home(),
        "@PACKAGE@": PACKAGE_NAME,
        "@SPEC@": spec,
    }
    text = SCRIPT
    for key, value in values.items():
        text = text.replace(key, _quote(value))
    return text.replace("\n", "\r\n")


def _task_registered() -> bool:
    try:
        run_user(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True)
    except FixupError:
        return False
    return True


def autoupdate_fixups(ctx: FixupContext) -> list[Fixup]:
    script = ctx.config.path.parent / "service" / SCRIPT_NAME

    def check() -> bool:
        try:
            current = script.read_bytes().decode(ENCODING)
        except FileNotFoundError:
            return False
        return current == render_script() and _task_registered()

    def apply() -> None:
        content = render_script()
        script.parent.mkdir(parents=True, exist_ok=True)
        with script.open("w", encoding=ENCODING, newline="") as handle:
            handle.write(content)
        command = f'powershell.exe -ExecutionPolicy Bypass -NoProfile -File "{script}"'
        run_user(
            [
                "schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", command,
                "/SC", "DAILY", "/ST", RUN_TIME, "/RU", "SYSTEM", "/RL", "HIGHEST",
            ]
        )

    return [
        Fixup(
            id="node-autoupdate-task",
            title="Задача автообновления ноды (планировщик Windows)",
            check=check,
            apply=apply,
            platforms=("windows",),
        )
    ]
