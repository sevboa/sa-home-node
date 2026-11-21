# Плагины (`sa_home_node.plugins`)

Плагин — расширение ядра: объявляет конфиг, службы и ключи контента.
Ядро не знает о конкретных плагинах; они подключаются через точки входа
группы `sa_home_node.plugins` (`ENTRY_POINT_GROUP`).

## Модели

- `Plugin` — описание плагина:
  - `name` — имя плагина (совпадает с именем точки входа);
  - `config_model` — pydantic-модель секции конфига или `None`;
  - `services` — кортеж `ServiceSpec`;
  - `content_keys` — ключи контента вида `"<плагин>.<имя>"`, которые
    плагин требует;
  - `node_actions` — кортеж `NodeAction(spec, handler)` действий службы
    `node`: регистрируются в ней при старте ноды, видны в `describe` и
    вызываются с других нод через `command` на службу `node`.
- `ServiceSpec` — одна служба: `name` и `run` — асинхронная функция
  `Callable[[ServiceContext], Awaitable[None]]`.
- `ServiceContext` — контекст службы:
  - `node_id` — `config.node.id`;
  - `data_dir` — `config.data_dir / <имя плагина>`: плагин держит свои
    данные в `data_dir / <имя плагина>`;
  - `config` — валидированная секция конфига или `None`;
  - `content` — `ContentStore` ноды;
  - `swarm` — `SwarmApi` ноды или `None`: `node_id`, `emit` (событие в рой),
    `subscribe`, `command(dst, action, args, service=…, timeout_s=…)`,
    `get_state(dst)`, `describe(dst)`.

## Загрузка

- `available()` — точки входа группы `ENTRY_POINT_GROUP` через
  `importlib.metadata.entry_points(group=...)`; ключ — имя точки входа.
- `load_plugins(names)` — порядок результата равен порядку `names`;
  `available()` вызывается один раз.

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

Запуск: `sa-home-node run --config config.toml` (см.
[architecture](architecture.md)).
