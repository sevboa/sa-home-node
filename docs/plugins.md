# Плагины (`sa_home_node.plugins`)

Плагин — расширение ядра: объявляет конфиг, службы и ключи контента.
Ядро не знает о конкретных плагинах; они подключаются через точки входа
группы `sa_home_node.plugins` (`ENTRY_POINT_GROUP`).

## Модели

### `Plugin`

- `name` — имя плагина (совпадает с именем точки входа);
- `config_model` — pydantic-модель секции конфига `[<имя плагина>]` или `None`;
- `services` — кортеж `ServiceSpec`;
- `content_keys` — ключи контента вида `"<плагин>.<имя>"`, которые плагин требует;
- `node_actions` — кортеж `NodeAction(spec, handler)`: действия службы `node`, регистрируются при старте ноды,
  видны в `describe` и вызываются с других нод через `command` на службу `node`;
- `power_blockers` — кортеж асинхронных функций `() -> list[str]`: что мешает выключить машину (см. ниже);
- `replication_hooks` — `ReplicationHooks` или `None` (см. ниже);
- `fixups` — фабрика фиксапов хоста `Callable[[FixupContext], Sequence[Fixup]]` или `None` (см. ниже);
- `backup` — `BackupSpec` или `None`: что плагин отдаёт в бэкап (identity, снапшот БД, применение при восстановлении),
  см. [backup](backup.md) и `BackupSpec` ниже;
- `task_kinds` — кортеж `TaskKind`: виды отложенных задач, которые исполняет служба `tasks` (к ним добавляется
  встроенный `timer`), см. [tasks](tasks.md) и `TaskKind` ниже.
- `sections` — кортеж пар `(имя_секции, модель)`: дополнительные секции конфига, которые плагин хочет получать
  в `ServiceContext.sections`. Ядро валидирует их при создании контекста и кладёт по имени.
- `sensor_providers` — кортеж фабрик `Callable[[ServiceContext], SensorProvider]`: поставщики датчиков для
  плагина `monitor` (см. [monitor](monitor.md)). Ядро не использует их напрямую, плагин `monitor` собирает их
  из всех плагинов и выбирает по платформе хоста.

Плагин `monitor` встроен и всегда идёт первым, затем `tasks`; имена `monitor` или `tasks` в `[node].plugins` —
ошибка «Повтор имени плагина».

### `BackupSpec`

Описывает, что именно плагин кладёт в бэкап и как это восстанавливается (`sa_home_node.backup.spec.BackupSpec`):

- `service` — имя службы с БД, снапшот которой бэкапится;
- `identity_service` — имя службы пакетов identity;
- `identity_format`, `snapshot_format`, `bundle_format` — строки форматов (версионирование при смене структуры);
- `tables` — кортеж имён таблиц, которые выгружаются в снапшот;
- `skip_tables` — таблицы, которые есть в снапшоте, но не применяются при восстановлении (например, служебные);
- `primary_table` — таблица, по наличию строк в которой снапшот считается непустым;
- `trigger_actions` — действия службы, после которых снапшот пересобирается вне очереди;
- `collect_identity(BackupContext) -> dict` — асинхронно собирает identity (ключи, настройки) для публикации;
- `apply(ApplyContext)` — асинхронно применяет бандл на целевой ноде (привилегированные операции через `PrivilegedIO`);
- `summarize(bundle) -> list[str]` — строки сводки для CLI (`backup restore`);
- `rows_extra(tables) -> dict[str, int]` — дополнительные счётчики строк для метаданных снапшота.

Подробнее: [backup](backup.md).

### `TaskKind`

Вид отложенной задачи, который служба `tasks` исполняет особым образом (`sa_home_node.tasks.kinds.TaskKind`):

- `action` — имя действия, под которым заказчик ставит задачу;
- `execute(TaskCall) -> dict` — асинхронное исполнение; `TaskCall` несёт `task_id`, адрес цели, `args`, `meta`,
  `timeout_s`, общий `deadline`, `link` (протокол) и `emit` (события в рой);
- `wait_ready` (по умолчанию `False`) — перед исполнением дождаться готовности цели (будить и прогреть);
- `prewake` (по умолчанию `True`) — начинать прогрев цели за `PREWAKE_LEAD_S` до срока;
- `retry(exc) -> bool` — стоит ли повторить после этой ошибки (по умолчанию никогда).

Встроенный вид `TIMER` (`timer`) — на сроке сразу отдаёт `{"ok": true, "result": {}}`, цель не будит.
Плагины объявляют свои виды в `Plugin.task_kinds`. Подробнее: [tasks](tasks.md).

### `ServiceSpec`

Описание одной службы. Как ядро её держит — [services](services.md).

- `name` — имя службы, уникально среди всех плагинов ноды;
- `run` — `async def run(ctx: ServiceContext) -> None`: тело службы;
- `handler` — `Callable[[ServiceContext], ServiceHandler]`: обработчик протокола (`describe`, `get_state`,
  `run_command`); служба с ним отвечает на запросы роя;
- `process` — `True`: служба работает отдельным процессом (`sa-home-node service <имя>`), иначе — задачей
  внутри ноды;
- `endpoint` — `Callable[[config], str]`: адрес сервера протокола службы (unix-сокет или `tcp://…`). Аргумент —
  валидированная секция конфига: по умолчанию секция плагина (`config_model`), если задано `section` —
  соответствующая секция из `Plugin.sections`. Процессная служба сама поднимает на нём сервер с `handler`,
  нода держит к нему линк;
- `section` (по умолчанию `""`) — имя секции из `Plugin.sections`, которую `endpoint` получит как аргумент.
  Если пусто — используется секция плагина (`config_model`). Секция должна быть объявлена в `Plugin.sections`,
  иначе при загрузке плагина `PluginError`.
- `assignable` (по умолчанию `True`) — служба предлагается в списке действия `assign`;
- `external` — служба живёт вне ноды: супервизор её не запускает и не назначает, нода только держит линк на
  `endpoint` и показывает её в состоянии с `"external": true`;
- `singleton` — служба должна работать в рое ровно на одной ноде; `start_all` её не стартует, запуск решает
  аренда;
- `instanced` — служба работает в нескольких экземплярах (`служба@инстанс`), настройки экземпляра — пакет в
  `instances/`; флаг описательный, ядро поведение по нему не меняет;
- `satellites` — суффиксы сателлит-файлов экземпляра (например `(".extra",)`): файлы `<служба>.<инстанс><суффикс>.toml`
  реплицируются вместе с экземпляром, но пишет их сама служба.

### `ServiceContext`

- `node_id` — `config.node.id`;
- `data_dir` — `config.data_dir / <имя плагина>`;
- `config` — валидированная секция конфига или `None`;
- `content` — `ContentStore` ноды;
- `swarm` — `SwarmApi` ноды или `None`: `node_id`, `emit` (событие в рой), `subscribe`,
  `command(dst, action, args, service=…, timeout_s=…)`, `get_state(dst)`, `describe(dst)`;
- `instance` — имя экземпляра из назначения (пусто, если без инстанса);
- `package_path` — путь пакета экземпляра в `instances/` рядом с конфигом (`None` без инстанса). Ядро только
  отдаёт путь, разбор пакета — дело службы. Конфиг службе неизменяем: чтобы применить новый пакет, ядро
  перезапускает слот;
- `node_kind` — `config.node.kind` (`workstation`, `vps`, …);
- `sections` — словарь секций конфига, объявленных плагином через `Plugin.sections`: `{имя: валидированная модель}`;
- `plugins` — кортеж всех загруженных `Plugin` (ядро + плагины), в порядке загрузки;
- `broadcast` — `Callable[[str, dict], Awaitable[int]] | None`: отправить событие подписчикам через протокол роя;
  заполнено для процессных служб с `handler` (см. [services](services.md));
- `handler` — `ServiceHandler | None`: обработчик протокола службы, если есть `ServiceSpec.handler`;
- `node_socket` — путь сокета службы `node` этой ноды (`config.socket_path`): через него процессная служба говорит с
  роем;
- `swarm_token` — токен роя из секции `[swarm]`, для клиентов протокола;
- `state_file` — файл состояния ноды (`config.state_file`), для бэкапа службы через `backup.snapsync.build_backup`;
- `assigned_services` — `frozenset[str]`: службы, назначенные на ноду на момент старта (конфиг `[node].assignments` и
  `state.json`, та же `services.assignments.effective_assignments`, что у `FixupContext`); `ctx.assigned("vpn")` —
  есть ли хоть одна из названных.

### Вытеснение: `ServiceFenced`

Служба, обнаружившая, что её место занял другой экземпляр (общий токен, порт), бросает `ServiceFenced` из `run`.
Встроенная служба остановится без перезапуска; процессная завершится с кодом 11. В обоих случаях слот не
перезапускается, шлётся событие `service_fenced`, а аренда молчит по этому слоту `FENCED_HOLDOFF_S` (300 с), чтобы
два экземпляра не дрались.

### `SensorProvider`

Протокол поставщика датчиков для платформы (определяет `platform: str` — имя из `host.current().name`). Плагины
объявляют фабрики в `Plugin.sensor_providers`; плагин `monitor` выбирает поставщика по платформе хоста и
использует его методы. В ядре встроенный поставщик для Linux (psutil, `sensors -j`, `smartctl`, `nvidia-smi`,
`/proc`); для Windows в ядре поставщика нет — без плагина CPU/диски пусты, host-метрики и история отключений
не снимаются.

- `read_cpu(now: datetime) -> list[SensorReading]` — показания температур CPU;
- `read_disks(now: datetime) -> list[SensorReading]` — показания температур дисков;
- `read_disk_summaries() -> list[DiskSummary]` — сводки SMART (здоровье, атрибуты);
- `problem() -> dict | None` — проблема поставщика (отсутствие программы/прав), для `requirements` в `get_state`.

Модели `SensorReading`, `DiskSummary` — см. [monitor](monitor.md).

### Блокеры выключения (`power_blockers`)

Каждый поставщик возвращает список строк — причин не выключать машину сейчас. Их опрашивает `poweroff` с
`if_idle: true` после встроенных проверок (службы, SSH-сессии, tmux); см. [lifecycle](lifecycle.md). Исключение
поставщика не глотается: выключение не выполняется, ошибка уходит вызывающему.

### Крючки репликации (`ReplicationHooks`)

Все поля необязательны; крючки всех плагинов вызываются подряд. Как работает репликация —
[services](services.md).

- `passive(service, instance) -> bool` — пакету нужна копия на ноде без назначения этого экземпляра;
- `before_announce() -> Awaitable` — перед объявлением ревизий: плагин может собрать/обновить пакет; ошибка
  пишется в журнал и цикл не ломает;
- `on_applied(meta, data)` — после записи принятого от соседа пакета (`InstanceMeta` и байты); ошибка только
  в журнал.

### Фиксапы хоста (`fixups`)

Фабрика вызывается один раз при сборке списка командой `sa-home-node fix` (не при старте ноды) и возвращает
фиксапы плагина; что именно строить, она решает по контексту: `ctx.assigned("svc")`, `ctx.section(имя, модель)`,
`ctx.traits`, `ctx.host`. Устройство фиксапов, порядок и правила — [fixups](fixups.md).

- фиксапы одной службы задают с `needed=needs.assigned("svc")`; тогда они появляются, только когда служба назначена
  на ноду (по конфигу или `assign`);
- семейства (по приложению, по слоту) фабрика разворачивает циклом; id уникальны среди ядра и всех плагинов, у
  семейства общий префикс;
- платформа задаётся `platforms` (по умолчанию только `linux`);
- повтор id или исключение фабрики — `PluginError`, `fix` завершается кодом 2 и ничего не применяет.

```python
from sa_home_node.fixups import needs
from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.privileged import install_sudoers_snippet, path_exists, SUDOERS_DIR


def svc_fixups(ctx: FixupContext) -> list[Fixup]:
    name = "50-svc-reload"
    content = "user-a ALL=(root) NOPASSWD: /usr/bin/systemctl reload svc\n"
    return [
        Fixup(
            id="svc-sudoers",
            title="Разрешить службе svc перезагрузку без пароля",
            needed=needs.assigned("svc"),
            check=lambda: path_exists(SUDOERS_DIR / name),
            apply=lambda: install_sudoers_snippet(name, content),
        )
    ]


PLUGIN = Plugin(name="svc", fixups=svc_fixups)
```

Привилегированные действия донастройки — только через примитивы `fixups/privileged.py`; `sudo` из кода службы
и ноды напрямую не вызывают.

### Своя БД плагина (`sa_home_node.db`)

- `Database(path)` — SQLite через aiosqlite: `open()` (WAL, внешние ключи, `busy_timeout` 5 с), `close()`,
  `conn`, `transaction()`, `fetch_one`, `fetch_all`. Схему плагин применяет сам (`conn.executescript`).
- `add_column_if_missing(db, table, column, decl)` — миграция «добавить столбец», `True`, если добавлен.

### Внешние команды из службы (`sa_home_node.process`)

- `run(argv, *, timeout, input, stdout, stderr, env, cwd)` → `ProcResult(returncode, stdout, stderr)`
  (`stdout_text`, `stderr_text` — текст, второй без пробелов по краям). Тайм-аут, отмена и любое исключение
  убивают процесс и пробрасываются дальше.
- `run_privileged(argv, *, needs_privilege_text, timeout)` → stdout: программа ищется в `PATH`, команда идёт
  через `host.current().elevate_noninteractive_argv` (`sudo -n`). Повышение не поддержано или sudo просит
  пароль / отказал в правах (`requirements.looks_like_sudo_denied`) — `ProtoError(ERR_NEEDS_PRIVILEGE,
  needs_privilege_text)`, в тексте подсказка, какой фиксап выдаёт право; иной ненулевой код —
  `ProtoError(ERR_INTERNAL, "<программа> <аргументы> завершился ошибкой: <stderr>")`.
- Право на команду выдаёт фиксап плагина (sudoers-фрагмент); служба только пользуется им.

## Загрузка

- `available()` — точки входа группы `ENTRY_POINT_GROUP` через
  `importlib.metadata.entry_points(group=...)`; ключ — имя точки входа.
- `load_plugins(names)` — встроенные плагины всегда идут первыми: `monitor`, затем `tasks`; далее плагины
  в порядке `names` (имена `monitor` или `tasks` в `names` — повтор, `PluginError`); `available()` вызывается
  один раз.

Ошибки `PluginError` (наследник `RuntimeError`, текст на русском) при:

- неизвестных именах — одна ошибка со списком всех неизвестных и списком
  установленных плагинов;
- повторе имени в `names`;
- точке входа, вернувшей не `Plugin`;
- имени плагина, не совпавшем с именем точки входа.

## Контекст

`make_context(plugin, config, content, swarm=None)`:

- если у плагина есть `config_model` — `config.section(plugin.name, model)`,
  иначе `config = None`;
- `content.require(plugin.content_keys)`;
- `ConfigError` и `ContentError` пробрасываются как есть;
- `swarm` — передаётся в `ServiceContext.swarm` как есть.

## Объявление точки входа в pyproject.toml плагина

```toml
[project.entry-points."sa_home_node.plugins"]
example = "my_pkg.plugin:PLUGIN"
```

`PLUGIN` — объект `Plugin` в модуле `my_pkg.plugin`. При загрузке
проверяются: наличие имени среди точек входа, отсутствие повторов,
тип результата `load()` и совпадение `plugin.name` с именем точки входа.

## Пример (`sa_home_node.example`)

Минимальный плагин, он же документация API (`src/sa_home_node/example/`,
точка входа `example = "sa_home_node.example:PLUGIN"` в `pyproject.toml`
ядра):

- `ExampleConfig` — pydantic-модель секции `[example]`: `interval_s`
  (по умолчанию 60.0) и `socket` (по умолчанию `./data/example.sock`) —
  адрес процессной службы `echo`;
- служба `hello` — бесконечный цикл: пишет в журнал приветствие из
  контента (`ctx.content.get("example.greeting")`) и, если рой запущен
  (`ctx.swarm is not None`), шлёт в рой событие `example_greeting` с
  этим текстом (`ctx.swarm.emit`); затем спит `interval_s`;
- процессная служба `echo` — супервизор поднимает её отдельным
  процессом (`python -m sa_home_node service echo`); сама служба ничего
  не делает и живёт вечно, а сервер протокола слушает юникс-сокет
  `config.socket` (`endpoint` в `ServiceSpec`) и обслуживает запросы
  handler'а `EchoHandler`: `describe()` отдаёт `ServiceInfo(node=<node_id>,
  service="echo", version="1")` и действие `echo` с обязательным
  параметром `text`; `run_command` отвечает `{"text": <текст>}` и считает
  выполненные эхо, `get_state()` отдаёт `{"count": <счёт>}`;
- действие `example_ping` — handler для `NodeAction`: возвращает словарь
  с ключом `pong` и текстом из `text`;
- `PLUGIN = Plugin(name="example", config_model=ExampleConfig,
  services=(ServiceSpec("hello", hello),
  ServiceSpec("echo", echo, handler=EchoHandler, process=True,
  endpoint=lambda config: config.socket),),
  content_keys=("example.greeting",),
  node_actions=(NodeAction(ActionSpec(...), example_ping),))` — требует ключ
  контента `example.greeting` из `content/example.toml`, объявляет действие
  `example_ping` (необязательный параметр `text`) в службе `node`.

Фрагмент кода плагина с роем:

```python
async def hello(ctx: ServiceContext) -> None:
    while True:
        text = ctx.content.get("example.greeting")
        log.info(text)
        if ctx.swarm is not None:
            await ctx.swarm.emit("example_greeting", {"text": text})
        await asyncio.sleep(ctx.config.interval_s)
```

Службу `echo` назначают действием `assign` (`echo`), после чего к ней можно обращаться командой `echo` с любой ноды
роя (`command` на службу `echo`). Запуск: `sa-home-node run --config config.toml` (см.
[architecture](architecture.md)).

Плагин `metrics` (встроен) объявляет `Plugin.scrape_jobs` и `Plugin.dashboards` для расширения скрейпа VictoriaMetrics
и набора дашбордов Grafana; формат `ScrapeJobSpec` и `DashboardSpec`, хелперы панелей — см. [metrics](metrics.md).

Плагин `bot` (встроен) — оболочка Telegram-бота; плагины расширяют её через `Plugin.bot` (`BotSpec`: команды, роутеры, права гостей, маршруты событий), см. [bot](bot.md).
