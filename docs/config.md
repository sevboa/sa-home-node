# Конфиг ноды (`sa_home_node.config`)

Конфиг ноды — TOML-файл. По умолчанию `~/.config/sa-home-node/config.toml`
(`DEFAULT_CONFIG`); путь можно передать в `load_config(path)`.

## Пример config.toml

```toml
[node]
id = "имя-ноды"
data_dir = "data"                # по умолчанию data
content_dir = "content"          # по умолчанию <папка конфига>/content
plugins = ["плагин-1", "плагин-2"]

[служба-1]
# любые поля — секция читается плагином через cfg.section()
```

## Правила путей

- Относительные `data_dir` и `content_dir` считаются от папки конфига:
  `<папка конфига>/<путь>`. Абсолютные используются как есть.
- `NodeConfig.data_dir` и `NodeConfig.content_dir` возвращают уже
  развёрнутые пути; `content_dir` по умолчанию — `<папка конфига>/content`.

## Ошибки

`ConfigError` (наследник `ValueError`) при:

- отсутствии файла конфига — в тексте путь;
- отсутствии секции `[node]` или пустом `id` — в тексте путь;
- секция не прошла проверку модели (`NodeConfig.section(name, model)`) —
  в тексте имя секции.

`NodeSection` — pydantic-модель: `id`, `data_dir`, `content_dir`,
`plugins`. Прочие секции TOML собираются в `NodeConfig.sections`
(без `[node]`) и валидируются по месту через `section(name, model)`.
