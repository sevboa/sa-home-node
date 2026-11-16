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
    плагин требует.
- `ServiceSpec` — одна служба: `name` и `run` — асинхронная функция
  `Callable[[ServiceContext], Awaitable[None]]`.
- `ServiceContext` — контекст службы:
  - `node_id` — `config.node.id`;
  - `data_dir` — `config.data_dir / <имя плагина>`: плагин держит свои
    данные в `data_dir / <имя плагина>`;
  - `config` — валидированная секция конфига или `None`;
  - `content` — `ContentStore` ноды.

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

`make_context(plugin, config, content)`:

- если у плагина есть `config_model` — `config.section(plugin.name, model)`,
  иначе `config = None`;
- `content.require(plugin.content_keys)`;
- `ConfigError` и `ContentError` пробрасываются как есть.

## Объявление точки входа в pyproject.toml плагина

```toml
[project.entry-points."sa_home_node.plugins"]
example = "my_pkg.plugin:PLUGIN"
```

`PLUGIN` — объект `Plugin` в модуле `my_pkg.plugin`. При загрузке
проверяются: наличие имени среди точек входа, отсутствие повторов,
тип результата `load()` и совпадение `plugin.name` с именем точки входа.
