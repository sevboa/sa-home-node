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
assignments = []                 # по умолчанию []: строки "служба[@инстанс][:active|:standby][:prio=N]"
restart_delay_s = 5.0            # по умолчанию 5.0 (> 0)
stop_timeout_s = 90.0            # по умолчанию 90.0 (> 0)
# power_controllable = true      # по умолчанию не задано: решает тип ноды

[swarm]
token = "общий-секрет-роя"       # общий секрет роя
join = ""                        # по умолчанию "" — endpoint соседа для присоединения при старте
node_down_alert_after_s = 300.0  # по умолчанию 300.0
failover_grace_s = 120.0         # по умолчанию 120.0 (> 0)
superior_ready_timeout_s = 45.0  # по умолчанию 45.0 (> 0)
lease_poll_interval_s = 10.0     # по умолчанию 10.0 (> 0)
lease_settle_s = 15.0            # по умолчанию 15.0 (> 0)
replication_poll_interval_s = 30.0  # по умолчанию 30.0 (> 0)

[[swarm.nodes]]                  # соседи из конфига, по умолчанию нет; выученные добавляются в состояние сами
id = "имя-соседа"
endpoint = "tcp://192.168.1.10:9000"
endpoints = []                   # по умолчанию []
kind = ""                        # по умолчанию ""

[swarm.discovery]
enabled = true                   # по умолчанию true
port = 32167                     # по умолчанию 32167
idle_interval_s = 300.0          # по умолчанию 300.0
active_interval_s = 15.0         # по умолчанию 15.0

["служба-1"]
# любые поля — секция читается плагином через cfg.section()

[monitor]
socket = "./data/monitor.sock"
db_path = "./data/monitor.sqlite"

[schedule]
scan_cron = "*/1 * * * *"
smart_cron = "0 * * * *"
housekeeping_cron = "0 3 * * *"

[sensors.cpu]
enabled = true
warn_c = 80.0
crit_c = 90.0
hysteresis_delta_c = 5.0
consecutive_to_alert = 3
consecutive_to_clear = 3
mode = "fixed"
baseline_window = 240
baseline_min_samples = 30
baseline_k_sigma = 4.0
baseline_min_std_c = 3.0

[sensors.gpu]
enabled = false
warn_c = 80.0
crit_c = 90.0
hysteresis_delta_c = 5.0
consecutive_to_alert = 3
consecutive_to_clear = 3
mode = "fixed"
baseline_window = 240
baseline_min_samples = 30
baseline_k_sigma = 4.0
baseline_min_std_c = 3.0

[sensors.disks]
enabled = true
warn_c = 55.0
crit_c = 65.0
hysteresis_delta_c = 5.0
consecutive_to_alert = 2
consecutive_to_clear = 2
devices = []
mode = "fixed"
baseline_window = 240
baseline_min_samples = 30
baseline_k_sigma = 4.0
baseline_min_std_c = 3.0

[sensors.host]
enabled = false
sample_window_s = 10.0
consecutive_to_alert = 3
consecutive_to_clear = 3
thresholds = {}

[metrics]
export = false
exporter_port = 9100

[backup]
recipient_public_key = ""
private_key_file = ""
partner = ""
snapshot_interval_s = 600.0
snapshot_debounce_s = 15.0
snapshot_poll_s = 300.0

[tasks]
socket = "./data/tasks.sock"
db_path = "./data/tasks.sqlite"
default_timeout_s = 240.0
warmup_timeout_s = 360.0
```

## Поля `[node]` для роя

- `kind` — тип машины ноды; тип соседей берётся из конфига и состояния роя.
- `socket` — локальный unix-сокет для служб ноды и локальных служб роя.
- `listen` — TCP-адреса, на которых рой принимает соседей (их адреса объявляются соседям).
- `state_path` — файл состояния роя (`NodeState`: пиры, адреса, версия).

Устройство роя — [swarm](swarm.md).

## Поля `[node]` для служб и жизненного цикла

- `assignments` — службы, которые нода держит с самого старта. К ним добавляются строки из файла состояния
  (`state_path`), которые дописывает действие `assign`; действующий набор — их объединение. Формат строки и
  роли — [services](services.md).
- `restart_delay_s` — пауза перед перезапуском упавшей службы.
- `stop_timeout_s` — сколько ждать остановки каждой службы (потом `kill` или снятие задачи); от него же
  зависит общий бюджет останова ноды: `stop_timeout_s + 20` с ([lifecycle](lifecycle.md)).
- `power_controllable` — объявлять ли действия `poweroff`, `reboot`, `suspend`. Не задано: решает тип ноды
  (у постоянно включённых типов питание не объявляется).

## Поля `[swarm]` для служб

Времена в секундах, все больше нуля.

- `failover_grace_s` — сколько резервная служба-синглтон ждёт тишины старших нод, прежде чем занять место;
- `superior_ready_timeout_s` — сколько уступившая нода ждёт `report_ready` старшей, прежде чем забрать службу
  обратно;
- `lease_poll_interval_s` — период решений аренды;
- `lease_settle_s` — сколько первое решение аренды ждёт, пока выяснится состояние линков соседей;
- `replication_poll_interval_s` — период опроса соседей на новые ревизии пакетов настроек.

Что они значат — [services](services.md).

## Поля `[backup]`

Бэкап identity и снапшотов БД на напарнике. Без `partner` бэкапа нет.
Подробнее — [backup](backup.md).

- `recipient_public_key` — публичный ключ получателя (base64); без него нода не публикует копии;
- `private_key_file` — путь к приватному ключу (0600), нужен только там, где расшифровывают бандл восстановления;
- `partner` — id ноды-напарника: источник копий для этой ноды и их хранитель;
- `snapshot_interval_s` (600) — как часто пересобирать снапшот, если ничего не менялось;
- `snapshot_debounce_s` (15) — задержка внеочередной пересборки после изменяющей команды (`trigger_actions`);
- `snapshot_poll_s` (300) — период опроса источника приёмником снапшотов.

Остальные ключи секции разрешены (`extra="allow"`) — их читает плагин.

## Поля `[tasks]`

Встроенная служба отложенных задач (`tasks/plugin.py`). Подробнее — [tasks](tasks.md).

- `socket` ("./data/tasks.sock") — unix-сокет службы;
- `db_path` ("./data/tasks.sqlite") — SQLite-файл задач;
- `default_timeout_s` (240) — бюджет исполнения задачи без своего `timeout_s`;
- `warmup_timeout_s` (360) — потолок прогрева цели перед срабатыванием.

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
`plugins`, `socket`, `listen`, `state_path`, `assignments`, `restart_delay_s`, `stop_timeout_s`, `power_controllable`. `listen` из строки превращается в
список из одного элемента. Прочие секции TOML собираются в `NodeConfig.sections`
(без `[node]`) и валидируются по месту через `section(name, model)`.
