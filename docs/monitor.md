# Монитор: датчики, задания, экспорт метрик

Служба `monitor` (плагин `monitor/plugin.py::PLUGIN`, встроенный, грузится первым) снимает температуры
CPU/GPU/дисков, SMART-снимки, host-метрики ноды и историю отключений, гоняет показания через политики с
гистерезисом и рассылает события подписчикам. Работает отдельным процессом (`process=True`), слушает сокет
`[monitor].socket`.

## Назначение и границы
- Парсеры — чистые функции (тесты на фикстурах без железа); блокирующие вызовы идут через executor
  (`SensorSource`). Отсутствие программы или прав объясняется через `requirements_registry` в `get_state`, а не
  строкой в логе каждого скана.
- `SensorProvider` (`providers.py`) — точка расширения платформы: `platform`, `read_cpu`, `read_disks`,
  `read_disk_summaries`, `problem`. Поставщики приходят из `Plugin.sensor_providers`; `SensorSource` выбирает
  поставщика по `host.current().name`. Для Linux и прочих платформ с поставщиком совпадения нет — работают
  встроенные чтения (psutil/`sensors -j`, `smartctl`, `nvidia-smi`, `/proc`).
- Windows: ни psutil, ни WMI температур не дают, а в ядре поставщика нет. Без плагина-поставщика CPU и диски на
  Windows пусты (окно без поставщика), история отключений и SMART-снимки там тоже не снимаются; host-метрики
  читаются из `/proc`, то есть только на Linux.

## Модули (`src/sa_home_node/monitor/`)
| Модуль | Назначение |
|---|---|
| `plugin.py` | `PLUGIN`: секции `sensors`, `schedule`, `metrics`, служба `monitor` |
| `run.py` | `make_handler`, `run_monitor`: расписание, очередь, воркер, экспорт; чистый останов |
| `service.py` | `MonitorService`: `describe`, `get_state`, `run_command` |
| `config.py` | `MonitorConfig`, `SensorsConfig`, `ScheduleConfig`, `MetricsConfig`, `apply_node_kind` |
| `cron.py` | `CronScheduler`, `parse_cron`, `next_fire` |
| `jobbase.py` | `DedupQueue`, `JobWorker`, `JobContext`, `JobResult`, `EventDispatcher` |
| `scan.py` | `SensorScanJob`: температуры, политики, события перегрева |
| `scanhost.py` | `scan_host`: host-метрики, события `host_degraded`/`host_recovered` |
| `smartscan.py`, `smartdiff.py` | `SmartScanJob`, `diff_smart`: снимки SMART и смена здоровья |
| `housekeeping.py` | `HousekeepingJob`: потолки истории |
| `health.py`, `policy.py` | `compute_health_diff`, `reconcile_band`; `FixedThresholdPolicy`, `BaselinePolicy` |
| `metrics.py`, `hostmodels.py` | `METRICS` (пороговые), `GAUGES` (графиковые), `compute_host_diff`, модели host-метрик |
| `models.py`, `payloads.py` | модели состояний и событий; `BroadcastDispatcher`, словари для провода |
| `db.py`, `store.py`, `store_states.py`, `store_data.py` | `SCHEMA`, `apply_schema`, `MonitorStore` (`Database` — общий, `sa_home_node.db`) |
| `exposition.py` | `render_metrics`, `store_renderer` — текст `/metrics` из БД |
| `sensors/` | `source.py` (`SensorSource`), `cpu.py`, `gpu.py`, `disks.py`, `smartctl.py`, `blockdev.py`, `hostscan.py`, `procfs.py`, `power.py` |
| `../promexport.py` | формат Prometheus и `MetricsEndpoint` (общий слой, не только монитор) |
| `../fixups/monitor.py` | `monitor_fixups` |

## Поток данных
расписание (`CronScheduler`) -> `DedupQueue` -> `JobWorker` (одно задание за раз) -> задание -> `SensorSource`
(сенсор) -> политика -> `MonitorStore` (БД) -> `BroadcastDispatcher` (рассылка подписчикам).

- Расписание кладёт задания в `DedupQueue`: ключ освобождается на `get()`, повторный `put` занятого ключа
  возвращает `False`. Три записи: `sensor_scan` (`scan_cron`), `smart_scan` (`smart_cron`), `housekeeping`
  (`housekeeping_cron`). На старте сразу ставятся `SmartScanJob` и `SensorScanJob`.
- `JobWorker` строго последовательный; падение задания не валит воркер; запись `job_runs` создаётся до и
  закрывается после (`ok`/`error`, `metrics_json` — `JobResult`).
- `SensorScanJob`: состояние коммитится одной транзакцией (`apply_diff`), отправка отдельным шагом;
  `notified_*` ставится только когда диспетчер принял событие (`handled`) — падение между записью и отправкой
  не теряет и не дублирует событие. Статистика baseline считается по прошлым показаниям. После температур —
  `scan_host` и обновление кэша сводок дисков.
- `SmartScanJob`: снимок (baseline) сдвигается только после доставки (или когда слать некому — `handled`);
  доставка целиком провалилась — деградацию повторим на следующем прогоне. Задание read-only (`-H -A`),
  self-test не запускается.
- `HousekeepingJob`: потолки — 500 запусков, 5000 показаний на компонент, 15000 host-показаний на метрику
  (~10 суток при `*/1`, чтобы поймать «steal подрос за неделю»).
- Политики: `FixedThresholdPolicy` (пороги `warn_c`, гистерезис `hysteresis_delta_c`) и `BaselinePolicy`
  (`mode = "baseline"`: эффективный порог — меньшее из `warn_c` и `mean + k_sigma * max(std, min_std_c)`;
  пока образцов меньше `baseline_min_samples`, действует `warn_c`). Переход в `alerting`/`ok` — после
  `consecutive_to_alert`/`consecutive_to_clear` подряд срезов в полосе.

## База `monitor.sqlite`
Путь — `[monitor].db_path`, WAL, схема `db.SCHEMA` (создаётся `apply_schema`).

| Таблица | Ключ | Содержимое |
|---|---|---|
| `job_runs` | `id` | `job_type`, `started_at`, `finished_at`, `status`, `error`, `metrics_json` |
| `app_state` | `key` | строковое значение |
| `health_states` | `component_id` | состояние температурного компонента: `kind`, `label`, `status`, `last_temperature_c`, счётчик, `alerting_since`, `notified_alert_at`, `notified_cleared_at` |
| `readings` | `id`, индекс `(component_id, id)` | история температур |
| `host_metric_states` | `component_id` | то же для host-метрик: `metric`, `unit`, `last_value` |
| `host_readings` | `id`, индекс `(component_id, taken_at)` | история host-метрик |
| `smart_snapshots` | `component_id` | `health`, `attrs_json`, `taken_at` |

Ключи `app_state`: `disk_summaries_cache` (кэш сводок дисков, JSON), `host:kernel_scan_at` (курсор окна
`journalctl -k`; сдвигается после успешного среза host-метрик). Экземпляры host-метрик кодируются в
`component_id`: `host:<metric>:<экземпляр>` (`host:disk_pct:/mnt/data`).

## Провод
Описание службы (`describe`): возможности `temperature`, `smart`, `power`, `host`; действия:

| Действие | Параметры | Результат |
|---|---|---|
| `scan_now` | — | `{sensor_queued, smart_queued}` — поставлены ли задания (`False`, если такое уже в очереди) |
| `downtime` | `offset`, `limit` (по умолчанию 10, максимум 50) | `{events, offset, has_next}`; команда-представление, кнопкой не рисуется |
| `host_trend` | `metric` (обязателен), `hours` (24, максимум 336) | `{metric, hours, buckets}`; неизвестная метрика — `{error: "unknown_metric", known}` |

Неизвестное действие — `ProtoError` `ERR_UNKNOWN_ACTION`. `get_state` отдаёт: `node`, `service`, `now`,
`uptime_s`, `health`, `host_health` (пуст, если `sensors.host.enabled` выключен), `disks` (из кэша
`SensorScanJob`), `last_outage`, `job_counts`, `recent_runs`, `thresholds` (cpu/gpu/disk), `host_thresholds`
(с учётом переопределений), `requirements` (smartctl и nvidia-smi — только если датчик включён, `last` и
`journalctl`, журнал ядра — только при включённом host-датчике, плюс `problem()` поставщиков).

События (рассылка `broadcast`): `overheat_started`, `overheat_cleared` (`component_id`, `kind`, `label`,
`temperature_c`, `at`); `smart_degraded`, `smart_recovered` (`health_from`, `health_to`, `attr_changes`);
`host_degraded`, `host_recovered` (`metric`, `value`, `unit`, `hint`). `BroadcastDispatcher`: `handled` равен
`delivered` — без подписчиков событие остаётся неотправленным и повторится на следующем срезе.

## Конфиг
```toml
[monitor]
socket = "./data/monitor.sock"
db_path = "./data/monitor.sqlite"

[schedule]
scan_cron = "*/1 * * * *"
smart_cron = "0 * * * *"
housekeeping_cron = "0 3 * * *"

[sensors.cpu]    # warn_c 80, crit_c 90, hysteresis_delta_c 5, consecutive_to_alert/clear 3
[sensors.gpu]    # enabled = false по умолчанию, пороги как у cpu
[sensors.disks]  # warn_c 55, crit_c 65, consecutive 2, devices = ["/dev/sda:sat", ...]
[sensors.host]   # enabled, sample_window_s = 10, consecutive 3, [sensors.host.thresholds.<metric>] warn/crit

[metrics]
export = false
exporter_port = 9100
```
- Датчики cpu, gpu, disks принимают `mode = "fixed" | "baseline"` и параметры `baseline_*`.
- `apply_node_kind`: у ноды вида `vps` host-датчик включён (`enabled` не задан явно), а cpu и disks выключены,
  если не заданы явно; на остальных нодах host-датчик по умолчанию выключен.
- Диски: тип устройства отделяется последним двоеточием (`/dev/sda:sat`); без `devices` устройства берутся из
  `smartctl --scan -j`.

## Фиксапы монитора (`fixups/monitor.py`)
Семантика `check()`: `True` — уже настроено, применять не нужно; `needed` решает, относится ли фиксап к ноде.

| id | Нужен | `check()` истинен, когда |
|---|---|---|
| `install-smartmontools` | назначен `monitor` и включён датчик дисков | `smartctl` есть в PATH |
| `smartctl-sudoers` | то же | есть sudoers-снипет `50-sa-home-node-smartctl` и обёртка `~/.local/bin/smartctl` (`exec sudo -n <реальный smartctl>`) |
| `node-unit-smartctl-path` | то же | в юните ноды первым в `PATH` стоит `~/.local/bin` (нет юнита — истинно); применение переписывает строку и перезапускает юнит |
| `journalctl-group` | назначен `monitor` | пользователь в группе `systemd-journal` |

## Экспорт показателей
- Секция `[metrics]`: `export` (выкл. по умолчанию) и `exporter_port` (9100). При `export = true` `run_monitor`
  поднимает `MetricsEndpoint`: `GET /metrics` на оверлейном адресе ноды (`promexport.overlay_address`); другие
  пути — 404. Нет адреса — привязка повторяется каждые 15 с.
- `/metrics` отдаёт ПОСЛЕДНИЕ значения из БД (`health_states`, `host_metric_states`), скан не учащается
  (`exposition.render_metrics`): `sa_up 1`, `sa_host_<метрика>`, `sa_temperature_celsius{kind,component}`.
- Гауги (`GAUGES`: `cpu_busy_pct`, `mem_used_pct`, `mem_available_bytes`, `load1/5/15`, `uptime_s`, `procs`,
  `disk_pct`, `net_rx_bps`, `net_tx_bps`) — без порогов, алертов не бывает. Экземпляры идут меткой:
  `disk_pct{mount}` (реальные ФС на `/` и `/mnt/*`), `net_*_bps{iface}` (физические интерфейсы и туннели без
  `lo`, `veth`, `vprobe`, `docker`, `br-`).
- Нода не VPS при `export = true` снимает host-метрики только для экспорта: пороги отключены
  (`without_thresholds`), алертов нет, `host_health` в `get_state` пуст при выключенном `sensors.host.enabled`.
- Метку `node` экспортёр не ставит: её добавляет scrape-конфиг сборщика.

## Почему так
- **USB-мосты.** Без явного типа `smartctl` отдаёт температуру `null`: тип берётся из `smartctl --scan -j` или
  задаётся как `/dev/sda:sat`. Недоступные для SMART устройства (eMMC, optical, loop, RAID, device-mapper, zram)
  не опрашиваются, иначе спам в лог каждый скан; таймаут одного вызова защищает скан от зависшего моста.
  `smartctl` возвращает ненулевой код при некритичных предупреждениях при валидном JSON — парсится stdout.
- **NVMe.** `lsblk` отдаёт namespace (`/dev/nvme0n1`), SMART работает с контроллером (`/dev/nvme0`) — пересчёт
  (`nvme_controller_path`) обязателен, иначе температура и здоровье всегда пустые.
- **`raw.string`.** `raw.value` у части дисков упаковывает несколько полей (значение `589855` при строке
  `'31 (0 9)'`) — берётся первый токен `raw.string`.
- **`[N/A]`.** `nvidia-smi` пишет `[N/A]` вместо температуры — такая строка пропускается; один вызов опрашивает все
  карты (с проприетарным драйвером hwmon температуру GPU не публикует).
- **`journalctl -k`.** Стойла ядра читаются из журнала ядра; нужен root либо группа `systemd-journal`/`adm`,
  иначе результат пуст без ошибки — членство проверяется заранее и выносится в `requirements`.
- **`errs` вместо `drop`.** На виртуальном NIC `rx_drop` — шум гипервизорного свитча, `tx_drop` — qdisc и
  туннели: ложные тревоги; считаются только ошибки. `guest`/`guest_nice` в сумму CPU не входят (двойной счёт).
  Нет PSI/conntrack — метрика не эмитится.
- **Порог-сенсоры LHM — в плагин.** Пороговые сенсоры типа Temperature (у NVMe 80 C) — не показания: если их
  принять за самое горячее, температура врёт. Всё Windows-специфичное (LibreHardwareMonitor) живёт в плагине
  как `SensorProvider`, ядро о нём не знает.
- **Баз-линия.** Фиксированный порог не ловит «диск обычно 35, стал 50»; `baseline` сравнивает с историей
  компонента, но не выше `warn_c`, и до накопления `baseline_min_samples` не работает.
- **`handled` против `delivered`.** `delivered` — событие дошло хотя бы до одного получателя, `handled` — оно
  принято и повторять не нужно. Пометка `notified_*` и сдвиг SMART-снимка ставятся по `handled`: пока никто
  не принял, событие повторится, а доставленное не дублируется.
- **Кэш сводок дисков.** Диски в `get_state` берутся из кэша, который обновляет `SensorScanJob`: вызов
  `smartctl` на каждый запрос состояния делал статус и веерные запросы по рою медленными. Иконка здоровья диска
  берётся из последних SMART-снимков БД, чтобы совпадать с алертами `SmartScanJob`; на ноде без дисков со SMART
  кэш затирается пустым списком.
- **Питание.** `last -xR --time-format iso reboot` классифицирует сессии: `unexpected` — не было штатного
  shutdown; у внезапного обрыва точного момента в wtmp нет, оценка берётся из `journalctl --list-boots` с допуском
  по времени старта. Страница запрашивается на одно событие больше — так определяется `has_next`.
