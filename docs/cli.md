# Команда `sa-home-node` (`sa_home_node.cli`)

Точка входа `sa_home_node.cli:main` (`[project.scripts]` в
`pyproject.toml`). `main(argv=None) -> int` — код выхода.

## Подкоманды

- `run [--config PATH]` — запуск ноды (`run_node` из `sa_home_node.node`
  через `asyncio.run`); перед запуском `logging.basicConfig(level=INFO)`. Если `run_node` вернул `True`
  (самоперезапуск), вызывается `host.current().self_restart(sys.argv)`.
  `ConfigError`, `ContentError`, `PluginError` печатаются в stderr строкой
  `ошибка: <текст>`, код 2; остальные исключения не ловятся.
- `plugins` — имена из `plugins.available()` по одному в строке; если
  плагинов нет — строка `плагины не установлены`; код 0.
- без подкоманды — справка (`parser.print_help()`) и код 2.

## Пример

```bash
sa-home-node run --config config.toml
sa-home-node plugins
```
