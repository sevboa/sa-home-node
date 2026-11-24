# Протокол роя v0 (`sa_home_node.proto`)

Контракт для человека и нейросети: по нему можно написать совместимую ноду на другом
языке. Протокол — NDJSON поверх unix-сокета или TCP; каждая строка — один JSON-объект
конверта. Маршрутизация и discovery описаны в `swarm.md` (появится с областью роя).

## Транспорт и кадрирование

Адреса конечных точек (`proto/endpoints.py`):

- unix-путь — локальный сокет; сервер ставит на файл права `0600` и доверяет таким
  клиентам без `auth` (unix доступен только на платформах с unix-сокетами, проверяет
  `host.current().unix_sockets`).
- `tcp://host:port` — TCP; требует первым сообщением `auth` с токеном (токен из
  `[swarm].token` конфига; TCP-endpoint без токена — ошибка при создании сервера).
- Нода может иметь несколько адресов; в `hello.endpoints` объявляются только
  «рекламуемые» (`advertisable`): unix и loopback не объявляются, `0.0.0.0`
  раскрывается в адреса интерфейсов, виртуальные интерфейсы (vethernet, docker,
  virbr, vmnet, br-, veth) не объявляются — адрес хоста в NAT-сети контейнеров
  снаружи недостижим. Ранги: unix < приватная LAN < оверлейная сеть (CGNAT
  `100.64.0.0/10`) < прочее. Ранжирование по `endpoint_rank`.

Кадрирование: NDJSON, лимит строки `MAX_MESSAGE_BYTES = 1 МиБ` (лимит буфера
`StreamReader`). JSON компактный (`separators=(",", ":")`), кириллица пишется
байтами UTF-8, без `\u` экранирования (`ensure_ascii=False`).

## Конверт

```json
{"v":0,"id":"a1b2…","type":"get_state","src":{"node":"node-a","service":"svc"},
"dst":{"node":null,"service":"node"},"payload":{}}
```

- `v` — версия протокола (0); чужая версия → `unsupported_proto`.
- `id` — уникальный id сообщения; ответ несёт `id` исходного запроса.
- `src`/`dst` — адреса `{node, service}`; `node: null` — локальная нода. Поля
  необязательны; `Address.from_dict` требует, чтобы они были объектом и строки.
- `payload` — объект; пустой по умолчанию.
- `ok`/`error` — только в ответах (`error: {code, message}`).
- `timeout_s` — необязательный тайм-аут ожидания ответа, едет с конвертом через все
  хопы (число; `decode` принимает только число).
- `hops` — только у событий: сколько ретрансляций пережил; неотрицательное целое.

Пустые поля в JSON не пишутся (`encode` опускает `src/dst/ok/error/payload/timeout_s/hops`
пустыми), порядок ключей как в примере выше.

## Типы сообщений

Запросы (все обрабатываются сервером, ответ — `response`):

| тип | payload запроса | payload ответа |
|---|---|---|
| `auth` | `{token}` (только TCP, первым; опционально `node`, `incarnation`) | `{authenticated: true}`; неверный токен — `unauthorized` и соединение закрывается |
| `hello` | `{}` (опционально `dst`) | `ServiceInfo`: `node`, `service`, `version`, `node_kind`, `wake`, `endpoints`, `proto`, опционально `links`, `busy`, `working` |
| `describe` | `{}` | hello + `capabilities: [строка]` + `actions: [ActionSpec]` + extra-поля |
| `get_state` | `{}` | dict состояния |
| `command` | `{action, args}` | dict результата |

`ActionSpec`: `{"id": "scan_now", "title": "Запустить скан", "params":
[{"name": "force", "type": "bool", "required": false, "title": "Форсировать"}]}`.
Параметр: `name`, `type` (по умолчанию `string`), `required` (по умолчанию true),
`title`, `choices`. Фронтенды строят UI по этому списку, ничего не хардкодят.
`command` проверяется по описанию: неизвестное действие → `unknown_action`,
отсутствие обязательного параметра → `bad_request`; неизвестный тип запроса →
`unknown_type`.

Ответы:

```json
{"v":0,"id":"<id запроса>","type":"response","ok":true,"payload":{…}}
{"v":0,"id":"…","type":"response","ok":false,"error":{"code":"unknown_action","message":"нет такого действия: x"}}
```

Событие: `{"v":0,"id":"…","type":"event","src":{"node":"node-a","service":"svc"},
"payload":{"event":"node_up","data":{…}}}`. Доставка — только текущим
аутентифицированным подключениям, at-most-once; гарантированная доставка — на слое
выше. Сервер не ждёт ответа на событие.

## Коды ошибок (`ERR_*`)

- `bad_request` — невалидное сообщение (JSON, поля, адрес, args, отсутствует обязательный параметр);
- `unsupported_proto` — версия протокола не 0;
- `unknown_type` — тип сообщения не является запросом;
- `unknown_action` — в `command` нет такого действия в описании службы;
- `unauthorized` — нет или неверный токен; после ответа соединение закрывается;
- `unknown_dst` — адресат (нода/служба) неизвестен;
- `unavailable` — адресат известен, но связи нет (быстрый честный отказ вместо зависания);
- `timeout` — не пришёл вовремя;
- `internal` — внутренняя ошибка сервера;
- `needs_privilege` — действие требует привилегии (используется над слоем протокола).

`unknown_dst`, `unavailable` и `timeout` в ядре не выбрасываются — их возвращают роутер
и плагины; `is_service_unavailable` распознаёт первые два как «служба недоступна».

## Порционная загрузка (`proto/chunks.py`)

Большие файлы качаются командой порциями: `command` с `offset` и `length`
(`CHUNK_BYTES = 700 КиБ`) возвращает `{"data_b64": …, "eof": bool}`; качают по `offset`
пока не придёт `eof`. Целостность — sha256 от собранного файла (`expected_sha256`),
несовпадение — ошибка целостности. `data_b64` — стандартный base64.

## Служба `node`: действия, состояние, события

Службу `node` отдаёт каждая нода; её действия видны в `describe`, порядок кнопок задаёт `NODE_ACTION_ORDER`
(`swarm/service.py`): `start`, `stop`, `restart`, `assign`, `unassign`, `send_wol`, `report_ready`, `trigger_peers`,
`swarm_join`, `join`, `restart_node`, `check_update`, `update`, `poweroff`, `reboot`, `suspend`,
`close_ssh_sessions`, `list_instances`, `get_instance_config`. Действия, которых на ноде нет, пропускаются; действия
плагинов (`Plugin.node_actions`) и прочих вкладов, не названные в списке, идут после, в порядке регистрации.
Действия роя (`send_wol`, `trigger_peers`, `swarm_join`, `join`) описаны в [swarm](swarm.md).

### Действия

| действие | параметры | ответ | условие объявления |
|---|---|---|---|
| `start`, `stop`, `restart` | `name` — ключ слота (выбор из слотов супервизора) | `{service: <слот>}` | всегда |
| `assign` | `name` — строка назначения (выбор из назначаемых служб) | `{service: <слот>}` | всегда |
| `unassign` | `name` — ключ слота | `{unassigned: name}` | всегда |
| `report_ready` | `name` — слот, `ready` — bool | `{name, ready}` | всегда |
| `list_instances` | `service` | `{service, instances: [meta]}` | есть каталог пакетов |
| `get_instance_config` | `service`, `instance` | `{meta, content}`, `content` — текст пакета UTF-8 | есть каталог пакетов |
| `send_wol` | `mac` — строка (обязательный) | `{sent: true, mac: ...}` | есть проводной интерфейс с WoL (`wake` в `hello`) |
| `poweroff`, `reboot`, `suspend` | нет (у `poweroff` допустим `if_idle: true`) | `{scheduled, delay_s: 1.0}`; `poweroff` с `if_idle` — `{done, blockers}` | `[node].power_controllable` или тип ноды |
| `restart_node` | нет (допустим `force: true`) | `{scheduled: "restart_node", delay_s: 1.0}` или `{deferred, max_wait_s, message}` | всегда |
| `check_update` | нет | `{repo, running, installed, latest}` | есть источник обновления |
| `update` | нет | `{up_to_date, version, restart_required}` или `{scheduled, target_version}` (на Windows ещё `via: "scheduled_task"`) | есть источник обновления |

Замечания:

- `force` и `if_idle` в `describe` не объявляются (панели рисуют кнопки только для действий без параметров), но
  принимаются; `if_idle` — только строго `true`.
- Источник обновления — git-установка не в режиме разработки. Без каталога пакетов `list_instances` и
  `get_instance_config` остаются в обработчиках, но в `describe` не видны и отвечают `bad_request` «на этой ноде
  нет каталога пакетов настроек».
- Ответ действий питания уходит раньше самой команды (задержка 1 с).
- Ошибки `bad_request`: `нет такой службы: 'x' (есть: …)` (или `нет служб`), ошибки разбора назначения (список в
  [services](services.md)), `нет пакета настроек s@i на ноде n`, `обновление уже выполняется`. `internal`:
  `не удалось проверить обновления (сеть?)`.
- Устройство: [services](services.md), [lifecycle](lifecycle.md).

### `get_state` службы `node`

Помимо полей роя ([swarm](swarm.md)) вклады:

- `services` — слоты `{name, status, pid, restarts, last_exit_code, started_at, service, instance, role}`;
  `status` — `running`, `restarting` или `stopped`; у встроенной службы `pid: null`. Внешние службы роутера —
  с `"external": true`, `instance` и `role` — `null`;
- `instances` — список `meta` пакетов настроек: `{service, instance, rev, hash, updated_at, origin_node}`;
- `singletons` — `{слот: {role, running, claiming, ready, rank: [роль, приоритет, нода], fenced}}`;
- `update` — `{running, installed, restart_required, last}` (только при источнике обновления).

### Служба бэкапа (хранитель копий)

Действия объявляются только при заданном `[backup].partner` (см. [backup](backup.md)).

| действие | параметры | ответ |
|---|---|---|
| `backup_store_list` | `node` — id ноды | `{node, identity: [{label, meta}], snapshot: [{label, meta}]}` |
| `backup_store_get` | `node`, `kind=identity\|snapshot`, `label=latest\|МЕТКА` | `{kind, label, meta, sealed}` (base64) |
| `backup_snapshot_get` | `have_hash` — хеш открытого текста, который есть у приёмника | `{unchanged, hash}` или `{hash, meta, sealed}` |
| `backup_snapshot_poke` | (нет) | `{ok: true}` — уведомление, что новый снапшот готов |

Звать может любой участник роя с токеном. Блобы запечатаны, в открытом виде только метаданные;
отдаётся лишь копия заданного напарника. Ошибки — `bad_request` / `internal`.

### Служба `tasks` (`tasks/protocol.py`, `tasks/service.py`)

Процессная служба на `[tasks].socket`. Действия:

| действие | параметры | ответ |
|---|---|---|
| `create` | `due_at` (ISO 8601, обязат.), `dst_node`, `dst_service`, `action` (обязат.), `args` (объект), `meta` (объект), `timeout_s` (число), `await_event` (`{node, event_type}`) | `{task_id: int}` |
| `fire_now` | `task_id` (int, обязат.) | `{fired: bool, reason?}` |
| `match_event` | `node`, `event_type` (оба обязат.) | `{matched: bool, task_id?}` |
| `timer` | (нет; действие-будильник для встроенного вида `TIMER`) | `{}` |

События (через `broadcast`):
- `task_prewake {task_id, meta, status}` — `status`: `waking` (идёт подъём цели) или `ready`;
- `task_result {task_id, meta, ok, result?, error?}` — итог исполнения на сроке.

Подробнее — [tasks](tasks.md).

### События

| событие | данные |
|---|---|
| `service_started` | `{name, pid}` (`pid: null` у встроенной) |
| `service_failed` | `{name, exit_code}` или `{name, error}` |
| `service_stopped` | `{name}` |
| `service_fenced` | `{name}` |
| `singleton_activated` | `{slot, node}`, при захвате из-за молчания старшей ноды ещё `reason: "superior_ready_timeout"` |
| `singleton_yielded` | `{slot, node}` |
| `instance_config_changed` | `meta` пакета, без содержимого |
| `update_finished` | `{ok, version, error}` |

### Коды выхода

`11` — служба вытеснена другим экземпляром (`FENCED_EXIT_CODE`); `10` — самоперезапуск на Windows
(`RESTART_EXIT_CODE`, возвращает хост) и принудительный выход сторожа останова (`HARD_EXIT_CODE`).

## Обвязка

- `ProtoServer(endpoint(s), handler, token=, router=, on_peer_connect=)` — слушает
  все конечные точки, отдаёт `hello`/`describe`/`get_state`/`command` из handler'а
  (`ServiceHandler`), роутер может перехватить запрос и вернуть ответ или `None`
  (тогда обычная диспетчеризация). Публичное: `start`, `stop`, `endpoint(s)`,
  `bound_endpoints`, `advertised_endpoints`, `broadcast_event(type, data)` /
  `broadcast_envelope(env)` — рассылка текущим аутентифицированным подключениям.
- `ProtoClient(endpoint, token=, src=, incarnation=, on_event=, timeout=)` —
  `connect` открывает соединение (для TCP ставит keepalive-опции и первым шлёт
  `auth` с токеном; при неаутентификации закрывает соединение), `hello`/`describe`/
  `get_state`/`command` — обёртки над `request`, `close` закрывает соединение и
  роняет ожидающие запросы `ConnectionError`, `abort` — жёсткий обрыв без ожидания
  закрытия, `join` — дожидается конца читателя и очереди событий. События клиента
  разбирает отдельная задача из очереди, по одному и в порядке прихода: обработчик
  в цикле чтения не мог дождаться ответа на собственный запрос по той же связи;
  порядок важен для пар «открыть/закрыть статус». Обрыв соединения роняет
  ожидающие запросы `ConnectionError`.

## Почему так

- keepalive и `TCP_USER_TIMEOUT` ставятся на обеих сторонах соединения: keepalive не
  покрывает непустую очередь отправки (Send-Q) — ядро гоняет ретрансмиссии ~15 минут;
  `TCP_USER_TIMEOUT` обрезает это. Windows-аналог — `TCP_MAXRT`
  (подробности в [host](host.md)). Принятым сервером сокетам keepalive тоже нужен.
- `SEND_TIMEOUT_S = 10` — сервер не ждёт записи клиенту дольше этого: полумёртвый
  клиент не держит рассылку событий, соединение закрывается.
- `AUTH_TIMEOUT_S = 10` — TCP-клиент обязан прислать `auth` за это время, иначе
  соединение закрывается; до auth строки разбираются по очереди (сервер ждёт каждое
  сообщение и обрабатывает его сразу), иначе тайм-аут мог закрыть только что
  прошедшее auth соединение.
- `CLOSE_TIMEOUT_S = 5` — останов сервера не зависит от чужого сокета: закрытие
  соединений и серверов ограничено этим тайм-аутом.
- Дозахват адресов: адрес оверлейной сети может появиться через минуту после старта;
  без единого адреса сервер ждёт `BIND_RETRY_TIMEOUT_S = 180` с (интервалы 3 с, после
  дедлайна 30 с), `EADDRINUSE` — сразу ошибка.
- Тайм-ауты клиента: `DEFAULT_TIMEOUT = 10` — по умолчанию для ответа,
  `timeout_s` конверта перекрывает его; `CLOSE_TIMEOUT_S = 5` — то же ограничение
  закрытия, что у сервера.
