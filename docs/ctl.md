# Консоль управления и мастер инициализации (`sa_home_node.ctl`, `sa_home_node.wizard.run`)

Две подкоманды CLI: `ctl` — консоль управления работающей нодой, `init` — мастер первичной настройки.

---

## `sa-home-node ctl` — консоль управления

Точка входа: `sa_home_node.ctl.cli:build_parser` → `sa_home_node.ctl.run:run(args) -> int`.
Глобальные флаги (добавляются в `build_parser` до подпарсеров):

- `--config`, `-c PATH` — путь к `config.toml` (нужен для токена роя и сокета по умолчанию).
- `--socket`, `-s ENDPOINT` — endpoint своей ноды: путь к Unix-сокету (`/run/.../node.sock`) или `tcp://host:port`. Если не задан — берётся из `config.toml` (`[node].socket`).
- `--node`, `-n ID` — id удалённой ноды роя: запрос пойдёт через свою ноду (маршрутизация роем).

Коды возврата:
- `0` — ок.
- `1` — нода недоступна (соединение/протокол/таймаут).
- `2` — ошибка аргументов `call` (парсинг `ключ=значение`) или ошибка `argparse` (нет подкоманды).

### Подкоманды

Все подкоманды, кроме `events`, делают один запрос и завершаются.

| Подкоманда | Аргументы | Описание |
|---|---|---|
| `status` | — | Статус ноды и её служб (имя, состояние, PID, рестарты, время старта, пиры, предупреждение об обновлении). |
| `start NAME` | `name` — имя службы | Запустить службу. |
| `stop NAME` | `name` — имя службы | Остановить службу. |
| `restart NAME` | `name` — имя службы | Перезапустить службу. |
| `assign NAME` | `name` — имя службы | Назначить службу текущей ноде (без правки конфига). |
| `unassign NAME` | `name` — имя службы | Снять назначение службы. |
| `join ENDPOINT` | `endpoint` — `tcp://host:port` соседа | Присоединиться к рою через существующую ноду. Печатает список новых пиров. |
| `restart_node [--force]` | `--force` — не ждать занятых служб (по умолчанию рестарт откладывается до 30 мин) | Перезапустить саму ноду (супервизор). Без `--force` может вернуть «рестарт отложен». |
| `poweroff` | — | Выключить машину ноды. |
| `reboot` | — | Перезагрузить машину ноды. |
| `suspend` | — | Усыпить машину ноды. |
| `events` | — | Живой хвост событий ноды (Ctrl+C — выход). Печатает приветствие, потом строки `HH:MM:SS event key=val...`. |
| `call SERVICE ACTION [KEY=VAL...] [--timeout SEC]` | `service` — имя службы; `action` — id действия (см. `describe`); `args` — `ключ=значение` (true/false/числа распознаются автоматически); `--timeout` — сек. ждать ответа | Вызвать любое умение любой службы роя. Результат — JSON. |
| `describe SERVICE` | `service` — имя службы | Какие умения объявляет служба и с какими параметрами. Вывод: таблица `action title (params)`. |
| `check_update` | — | Сверить работающую/установленную/доступную версии. |
| `update` | — | `pipx install --force` до последнего тега (файлы на диск, процесс не трогает). Требует `restart_node` после. |

### Примеры

```bash
# Статус своей ноды
sa-home-node ctl status

# Статус пира через свою ноду
sa-home-node ctl -n peer1 status

# Явный сокет и конфиг
sa-home-node ctl --socket tcp://10.0.0.1:8710 --config /etc/sa-home-node.toml status

# Управление службами
sa-home-node ctl start monitor
sa-home-node ctl restart apps
sa-home-node ctl assign backup
sa-home-node ctl unassign monitor

# Рой
sa-home-node ctl join tcp://192.168.1.5:8710

# Перезапуск ноды
sa-home-node ctl restart_node
sa-home-node ctl restart_node --force

# Питание
sa-home-node ctl poweroff
sa-home-node ctl reboot

# События
sa-home-node ctl events

# Вызов действия
sa-home-node ctl call monitor scan 'interval=30' 'targets=auto'
sa-home-node ctl call tasks list --timeout 5

# Описание действий службы
sa-home-node ctl describe monitor

# Обновление
sa-home-node ctl check_update
sa-home-node ctl update
```

---

## `sa-home-node init` — мастер инициализации

Точка входа: `sa_home_node.wizard.run:add_init_parser` → `sa_home_node.wizard.run:run_init(args, *, reply, interactive, host, run_step, fix, executable) -> int`.

### Флаги (добавляет `add_init_parser`)

| Флаг | Описание |
|---|---|
| `--config PATH` | Путь к конфигу (по умолчанию `~/.config/sa-home-bot/config.toml`). |
| `--data-dir PATH` | Каталог данных (по умолчанию `~/sa-home-node`). |
| `--node-id STR` | Имя ноды в рое (по умолчанию — hostname). |
| `--kind KIND` | Тип машины: `server`, `workstation`, `vps` (по умолчанию `workstation`). |
| `--assignments STR` | Назначения через запятую (например `monitor,tasks`). |
| `--listen STR` | Адреса для входящих соединений через запятую. |
| `--join IP:PORT` | IP:порт существующего соседа для входа в рой. |
| `--token STR` | Токен роя (обязателен вместе с `--join` в неинтерактивном режиме). |
| `--non-interactive` | Не спрашивать, использовать дефолты и переданные флаги. |
| `--force` | Перезаписать существующие файлы конфига и юнита. |
| `--no-unit` | Не писать системный юнит (systemd-user). |
| `--no-start` | Не запускать службу после настройки (только печатать команды). |

### Зависимости `run_init` (передаются из `cli.main`)

| Параметр | Тип | Источник в `cli.main` |
|---|---|---|
| `args` | `argparse.Namespace` | Разобранные аргументы `init`. |
| `reply` | `Callable[[str], str]` | Встроенный `input`. |
| `interactive` | `bool` | `sys.stdin.isatty() and not args.non_interactive`. |
| `host` | `Any` | `sa_home_node.host.current()` (платформозависимая реализация). |
| `run_step` | `Callable[[list[str]], bool]` | Запускает `subprocess.run(cmd, check=False)`, возвращает `returncode == 0`. |
| `fix` | `Callable[[Path], int]` | Запускает `fix_command(path, dry_run=False, only=(), list_only=False)`. |
| `executable` | `str` | `Path(sys.argv[0]).resolve()` — абсолютный путь к запущенному исполняемому файлу. |

### Логика `run_init` (кратко)

1. Если конфиг существует и нет `--force` — ошибка, код `1`.
2. Опрашивает/берёт из флагов: `node_id`, `kind`, `assignments`, `listen`, `join`, `token`.
3. В неинтерактивном режиме без `--token` при заданном `--join` — ошибка, код `2`.
4. Пишет конфиг (`render_config`) и создаёт каталог данных.
5. Если не `--no-unit` и формат юнита `systemd-user` — пишет юнит (`render_systemd_unit`); существующий без `--force` не трогает. На других платформах печатает команду запуска вручную.
6. Если юнит записан и не `--no-start` — делает `daemon-reload` и `enable --now`, затем `loginctl enable-linger` через `sudo` (интерактивно) или печатает эту команду; с `--no-start` печатает команды.
7. Если интерактивно — запускает `fix(config_path)` (фиксапы хоста), иначе просит запустить отдельно.
8. Печатает токен роя (если нода не вступает через `--join`) и возвращает `0`.

### Коды возврата

Возвращает то, что вернула `run_init`:
- `0` — успешно.
- `1` — конфиг существует, нет `--force`.
- `2` — не хватает значений для `--non-interactive` (нет `--token` при `--join`).

Код `fix` на результат не влияет: фиксапы печатают свой отчёт сами.

### Примеры

```bash
# Интерактивно (спросит всё, чего нет в флагах)
sa-home-node init

# Полностью неинтерактивно (для скриптов/автоматизации)
sa-home-node init --non-interactive \
  --node-id myserver --kind server --assignments monitor,tasks \
  --listen tcp://0.0.0.0:8710 --join 192.168.1.5:8710 --token SWARM_TOKEN \
  --config /etc/sa-home-node.toml --data-dir /var/lib/sa-home-node

# Только конфиг, без юнита и без старта
sa-home-node init --no-unit --no-start --config ./config.toml

# Перезаписать существующий конфиг
sa-home-node init --force
```
