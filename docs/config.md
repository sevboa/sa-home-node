# Конфиг ноды (`sa_home_node.config`)

Конфиг ноды — TOML-файл. По умолчанию `~/.config/sa-home-bot/config.toml`
(`DEFAULT_CONFIG`); путь можно передать в `load_config(path)`.

## Пример config.toml

```toml
[node]
id = "имя-ноды"
kind = "workstation"             # по умолчанию workstation
data_dir = "data"                # по умолчанию data
content_dir = "content"          # по умолчанию <папка конфига>/content
plugins = ["плагин-1", "плагин-2"]
socket = "./data/node.sock"      # по умолчанию ./data/node.sock
listen = []                      # по умолчанию []
state_path = "./data/node-state.json"  # по умолчанию ./data/node-state.json

[служба-1]
# любые поля — секция читается плагином через cfg.section()
```

## Правила путей

- Относительные `content_dir` считаются от папки конфига: `<папка конфига>/<путь>`.
- Относительные `data_dir`, `socket` и `state_path` считаются от текущего каталога
  процесса (`Path.cwd()`): код берёт `data_dir` и путь сокета/файла состояния от
  запуска, а контент держит рядом с конфигом.
- Абсолютные пути используются как есть.
- `NodeConfig.data_dir`, `content_dir`, `socket_path` и `state_file` возвращают уже
  развёрнутые пути; `content_dir` по умолчанию — `<папка конфига>/content`.

## Ошибки

`ConfigError` (наследник `ValueError`) при:

- отсутствии файла конфига — в тексте путь;
- отсутствии секции `[node]` или пустом `id` — в тексте путь;
- секция не прошла проверку модели (`NodeConfig.section(name, model)`) —
  в тексте имя секции.

`NodeSection` — pydantic-модель: `id`, `kind`, `data_dir`, `content_dir`,
`plugins`, `socket`, `listen`, `state_path`. `listen` из строки превращается в
список из одного элемента. Прочие секции TOML собираются в `NodeConfig.sections`
(без `[node]`) и валидируются по месту через `section(name, model)`.
