# Пробуждение и готовность служб (`wol.py`, `wake/`)

Модуль будит спящую ноду пакетом Wake-on-LAN и доводит нужную службу до готовности. Он не зависит от Telegram и
чат-слоя: им пользуются и службы ядра (например, [tasks](tasks.md)), и плагины.

## `wol.py`: пакет и интерфейс

- `normalize_mac` приводит MAC к виду `aa:bb:cc:dd:ee:ff` (разделители `:`, `-`, `.`, пробелы допустимы), иначе `ValueError`.
- `build_magic_packet`: 6 байт `0xFF` + MAC x 16 (102 байта).
- `send_magic_packet(mac, broadcast, port, repeats, bind_ip)`: UDP-broadcast (по умолчанию `255.255.255.255:9`),
  несколько копий (лишние безвредны). `bind_ip` нужен многодомной ноде: без привязки broadcast может уйти не в тот сегмент.
- `ping_host` делает один ICMP-запрос (аргументы строит `host.current().ping_argv`), `wait_host_up` опрашивает до
  таймаута и возвращает прошедшее время или `None`.
- `detect_local_wake_iface` определяет интерфейс по умолчанию трюком с UDP `connect()` (пакет не уходит, сеть не нужна)
  и отбраковывает непригодное: виртуальные и туннельные (`lo`, docker, veth, br-, virbr, wg, tun, tap, vmware,
  virtualbox, hyper-v, признак `tun_flags` в sysfs) и беспроводные (sysfs `wireless`, а вне Linux эвристика по имени).
  Это шире, чем `proto/endpoints.is_virtual_iface`.
- `detect_local_wake_info` возвращает `LocalWakeInfo(mac, ip, broadcast)` или `None`; broadcast считается из адреса
  и маски интерфейса. Умение WoL объявляется только при наличии такого проводного интерфейса.

## Действие `send_wol` (`wake/actions.py`)

`register_wake` добавляет в службу `node` действие `send_wol` (параметр `mac`, обязательный):

1. MAC нормализуется; ошибка даёт `ERR_BAD_REQUEST`.
2. Пакет уходит с `bind_ip` локального wake-интерфейса (если он определён); `OSError` даёт `ERR_INTERNAL`.
3. Ответ: `{"sent": true, "mac": ...}`; отправка пишется в лог на уровне предупреждения.

`local_wake_payload` отдаёт `{mac, ip, broadcast}` для объявления в hello и в состоянии ноды (ключ `wake`): так соседи
помнят реквизиты ноды, даже когда она уснула.

## Опрос соседей (`wake/peers.py`)

Работает через протокол `NodeLink` (`get_state`, `command`) и кэш реквизитов `WakeCache` (`MemoryWakeCache` хранит их
в памяти процесса; ключи постоянных кэшей начинаются с `WAKE_KEY_PREFIX = "wake_info:"`).

- `fetch_state` — `get_state` с коротким таймаутом (`PEER_TIMEOUT_S` = 3 с, если не задан свой); сбой связи даёт `None`.
- `collect_reports(link, own_state, with_monitor=...)` — веерный опрос: свой `NodeReport`, затем отчёты по живым пирам
  параллельно; с `with_monitor` добавляется состояние службы `monitor`.
- `resolve_wake_info` — реквизиты из кэша, а при промахе спрашивается локальная нода (она держит `wake` соседей из их hello)
  и ответ сразу кладётся в кэш.
- `find_lan_waker` — живая нода в том же сегменте сети (совпал объявленный `broadcast` с целью); будит она, потому что
  вызывающий может быть вне локальной сети. Заодно кэш пополняется реквизитами всех опрошенных нод.

## Готовность службы (`wake/ready.py`)

`wake_swarm_node_core(link, cache, node_id, force=False, guard=None)` возвращает `WakeOutcome(ok, detail)`, где
`detail` — готовый HTML-текст для чата. Порядок: `guard` (необязательная проверка права; ответ-отказ возвращается
как есть, `force` её пропускает) -> реквизиты (`resolve_wake_info`) -> нода-будильщик (`find_lan_waker`) -> действие
`send_wol` на её службе `node`. Отказы: нет реквизитов, некому отправить, ошибка протокола, будильщик перестал отвечать.

`ensure_service_ready(link, cache, node_id, service, ...)` возвращает один из исходов:

- `READY` — служба отвечает и не спит (или прогрелась);
- `UNREACHABLE` — ноду или службу не удалось поднять за `wake_timeout_s`;
- `CRASHED` — нода отвечает, а именно эта служба нет: WoL бесполезен, magic packet не шлётся;
- `WARMUP_FAILED` — служба поднялась, но прогрев не удался.

Сценарий: присутствие службы (`PRESENCE_TIMEOUT_S`) -> если не отвечает, различаем сон и аварию (ответила ли служба
`node`) -> WoL и ожидание службы (`wait_for_service`) -> прогрев. Если служба отвечает с `asleep`, сразу прогрев;
иначе `READY` без вызова фаз. Прогрев (`try_warmup`) — команда `warmup` с таймаутом; `ERR_UNKNOWN_ACTION`
(у службы нет прогрева) не ошибка, любой другой отказ или молчание — неудача, причина уходит в лог.

Фазы `PHASE_WAKING`, `PHASE_WOKEN`, `PHASE_WARMING` передаются в `on_phase_change` до начала соответствующего ожидания.

Таймауты (меняются только с причиной):

| Константа | Значение | Смысл |
|---|---|---|
| `PEER_TIMEOUT_S` | 3 с | обзорные опросы соседей |
| `PRESENCE_TIMEOUT_S` | 6 с | присутствие перед запросом: ложное «недоступна» дороже, чем ожидание (лишний WoL) |
| `WAKE_POLL_TIMEOUT_S` | 180 с | ожидание службы после magic packet |
| `WAKE_POLL_INTERVAL_S` | 3 с | шаг опроса |
| `WARMUP_TIMEOUT_S` | 360 с | прогрев, должен покрывать холодную загрузку модели с диска |

## Фиксап `wol-enable` (`fixups/wol.py`)

Только Linux и только на ноде с признаком `wakeable` (`needs.wakeable`). Проверка: существует ли
`/etc/systemd/system/sa-home-wol.service`. Применение: найти проводной интерфейс (`detect_local_wake_iface`, иначе
`FixupError`), найти или установить `ethtool`, записать юнит (`wol_unit_content`, oneshot `ethtool -s <iface> wol g`),
`daemon-reload` и `enable --now`. Подробнее о механике фиксапов — [fixups](fixups.md).
