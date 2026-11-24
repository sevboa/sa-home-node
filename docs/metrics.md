# Метрики хаба (`sa_home_node.metrics`)

Стек метрик хаба: служба **metrics-hub**, **VictoriaMetrics** (хранилище), **Grafana** (визуализация).
Служба `metrics-hub` запускается, когда в `[metrics]` включён `hub = true`.

## Конфиг `[metrics]`

Поля из `metrics/config.py`:

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `export` | `bool` | `false` | Включить встроенный экспортёр ноды (`promexport`) на `exporter_port`. |
| `exporter_port` | `int` | `9100` | Порт встроенного экспортёра. |
| `hub` | `bool` | `false` | Запускать службу `metrics-hub` и ставить фиксапы VictoriaMetrics + Grafana. |
| `data_dir` | `str` | `/mnt/data/metrics` | Каталог данных VictoriaMetrics (владелец `_victoria-metrics`). |
| `runtime_dir` | `str` | `/var/lib/sa-home-metrics` | Каталог пользователя ноды для `targets/` и `dashboards/` (владелец — пользователь ноды). |
| `retention` | `str` | `1y` | Период хранения VictoriaMetrics (`-retentionPeriod`). |
| `scrape_interval` | `str` | `30s` | Интервал скрейпа VictoriaMetrics (`global.scrape_interval`). |
| `grafana_port` | `int` | `3000` | Порт Grafana (`server.http_port`). |
| `vm_port` | `int` | `8428` | Порт VictoriaMetrics (`-httpListenAddr=127.0.0.1:...`). |
| `grafana_public_url` | `str` | `""` | Публичный URL Grafana (пока не используется). |
| `overlay_unit` | `str` | `""` | Имя юнита оверлейной сети (для `After=`/`Wants=` в drop-in Grafana). |

## Безопасность: адреса биндинга

- **VictoriaMetrics** слушает только `127.0.0.1:vm_port` (не выходит в сеть).
- **Grafana** слушает **только оверлейный адрес** ноды (`http_addr` в `grafana.ini` задаёт фиксап `grafana-config`). Порт — `grafana_port`.
- **Встроенный экспортёр** (`promexport`, при `export = true`) слушает только оверлейный адрес на `exporter_port`.

## Цели скрейпа (scrape targets)

VictoriaMetrics скрейпит цели из файлов `file_sd` в `runtime_dir/targets/` (правило — `metrics/filesd.py`).

Источник целей — функция `scrape_targets` (`metrics/targets.py`):
1. Собственная нода: `own_id` + `own_host` (адрес из `promexport.overlay_address()`).
2. Пиры из роя: для каждого пира выбирается лучший адрес функцией `peer_host` (`metrics/peerhost.py`).

### Правило выбора адреса пира (`peer_host`)

Кандидаты: `peer.endpoint` и все `peer.endpoints`. Парсятся как TCP-эндпоинты.
Отбрасываются: loopback (`127.*`, `::1`) и unspecified (`0.0.0.0`, `::`).
Сортировка по ключу `(not overlay, rank, index)`:
- Сначала адреса с рангом **overlay** (оверлейная сеть).
- Затем по возрастанию ранга (см. `proto/endpoints.py`: `RANK_OVERLAY`, `RANK_PUBLIC`, `RANK_PRIVATE` и т.д.).
- При равенстве — по порядку в списке пира.

Выбранный `host` попадает в `targets/*.json` как `host:port` с лейблом `node`.

## Динамика без sudo: `file_sd` и дашборды в `runtime_dir`

Служба `metrics-hub` (класс `MetricsHub` в `metrics/hub.py`) каждые 60 секунд:
1. Получает свой оверлейный адрес и список пиров из роя.
2. Строит словарь `Path -> str` файлов через `runtime_files` (`metrics/runtime.py`):
   - `targets/nodes.json` — все ноды (свой хост + пиры) на `exporter_port`.
   - `targets/<plugin>.json` — для каждого `Plugin.scrape_jobs` (фильтр по нодам, свой порт).
   - `dashboards/*.json` — дашборды из `Plugin.dashboards` (рендер через `metrics/panels.py`).
3. Записывает **только изменившиеся** файлы (`write_changed` в `metrics/writeout.py`).

Фиксапы создают каталоги и конфиги VictoriaMetrics/Grafana от root; служба работает от пользователя ноды и пишет в свои каталоги без `sudo`.

## Семь фиксапов (по порядку из `fixups/metrics.py`)

Все фиксапы имеют `needed = metrics_hub` (секция `[metrics]` с `hub = true`).

| id | Название | Что делает (по коду) |
|---|---|---|
| `install-victoria-metrics` | Установить victoria-metrics (хранилище метрик хаба) | Ставит пакет `victoria-metrics` через пакетный менеджер. |
| `metrics-runtime-dir` | Создать каталог целей и дашбордов метрик `<runtime_dir>` (владелец — пользователь ноды) | `sudo install -d -m 0755 -o <user> -g <user>` для `runtime_dir`, `targets/`, `dashboards/`. |
| `victoria-metrics-config` | Настроить victoria-metrics (данные `<data_dir>`) | 1. Создаёт `data_dir` (0750, владелец `_victoria-metrics`).<br>2. Пишет `/etc/victoria-metrics/scrape.yml` из `scrape_config_text` (jobs + `scrape_interval`).<br>3. Пишет `/etc/default/victoria-metrics` (`ARGS="..."` с `-storageDataPath`, `-retentionPeriod`, `-httpListenAddr=127.0.0.1:vm_port`, `-promscrape.config=...`). Перезапускает юнит **только если изменился defaults**.<br>4. `systemctl enable --now victoria-metrics.service`. |
| `grafana-repo` | Подключить репозиторий apt.grafana.com | Скачивает GPG-ключ `https://apt.grafana.com/gpg.key` в `/etc/apt/keyrings/grafana.asc`, пишет `/etc/apt/sources.list.d/grafana.list` с `deb [signed-by=...] https://apt.grafana.com stable main`, делает `apt-get update`. |
| `install-grafana` | Установить grafana | Ставит пакет `grafana`. |
| `grafana-config` | Настроить grafana (слушает только оверлейный адрес, анонимный вход выключен) | 1. Читает `/etc/grafana/grafana.ini`, задаёт секции: `server.http_addr` = оверлейный адрес, `server.http_port` = `grafana_port`; `auth.anonymous.enabled = false`; `analytics.* = false`; `plugins.preinstall_disabled = true`, `preinstall_auto_update = false`.<br>2. Пишет drop-in `/etc/systemd/system/grafana-server.service.d/50-sa-home-overlay.conf` с `After=`/`Wants=` на `overlay_unit` (если задан) и `Restart=on-failure`.<br>3. `daemon-reload`, `enable --now`, `restart grafana-server.service`. |
| `grafana-provisioning` | Выложить источник данных и провайдер дашбордов grafana | 1. Пишет `/etc/grafana/provisioning/datasources/sa-home.yaml`: datasource `VictoriaMetrics` (uid `vm`, type `prometheus`, `url: http://127.0.0.1:vm_port`, `isDefault: true`).<br>2. Пишет `/etc/grafana/provisioning/dashboards/sa-home.yaml`: провайдер `type: file`, `path: <runtime_dir>/dashboards`, `updateIntervalSeconds: 30`, `foldersFromFilesStructure: true`.<br>3. **Удаляет старый каталог** `/etc/grafana/sa-home-dashboards` (иначе дублируются uid дашбордов).<br>4. `systemctl restart grafana-server.service`. |

> **Находка**: drop-in Grafana даёт только порядок запуска после оверлейного юнита (`After=`/`Wants=`). Адрес биндинга (`http_addr`) задаётся в `grafana.ini` фиксапом `grafana-config`. Без оверлейного адреса фиксап падает с ошибкой «у ноды нет адреса оверлейной сети — поднимите оверлейную сеть».

> **Находка**: старый каталог `/etc/grafana/sa-home-dashboards` удаляет фиксап `grafana-provisioning` при применении. Если его оставить, Grafana видит дашборды из двух провайдеров — дублируются `uid`. Удаление вручную не нужно — фиксап делает это сам.

## API плагина: `Plugin.scrape_jobs` и `Plugin.dashboards`

Плагин объявляет в `Plugin` (см. `metrics/plugin.py` и `metrics/spec.py`):

```python
from sa_home_node.metrics.spec import ScrapeJobSpec, DashboardSpec
from sa_home_node.metrics.config import MetricsConfig
from collections.abc import Collection

Plugin(
    ...,
    scrape_jobs=(
        ScrapeJobSpec(
            name="my-job",                    # job_name в scrape.yml
            targets_file="my-targets.json",   # файл в runtime_dir/targets/
            port=lambda cfg: cfg.exporter_port,  # или cfg.vm_port и т.д.
            nodes=lambda: {"node-a", "node-b"},  # опционально: фильтр нод
        ),
    ),
    dashboards=(
        DashboardSpec(
            file="my-dashboard.json",         # файл в runtime_dir/dashboards/
            build=lambda: {                   # возвращает dict дашборда Grafana
                "title": "My Dashboard",
                "panels": [...],
                "uid": "my-dashboard",
            },
        ),
    ),
)
```

- `scrape_jobs` собираются в `scrape_jobs()` (`metrics/runtime.py`) → в `scrape.yml` (пишет фиксап `victoria-metrics-config`).
- `dashboards` рендерятся `render_dashboard` (`metrics/panels.py`) → JSON в `runtime_dir/dashboards/` (пишет служба `metrics-hub`).

### Хелперы `metrics/panels.py`

Для построения дашбордов в `DashboardSpec.build`:

- `target(expr, legend, ref, *, table=False)` — таргет панели с datasource `vm`.
- `query_variable(name, label, query)` — переменная типа `query` (мультиселект, `refresh: 2`).
- `number_panels(panels)` — пронумеровать `panel.id` по порядку.
- `timeseries(title, series, position, *, unit="short", percent=False)` — панель временного ряда (12×8).
- `render_dashboard(dashboard)` → JSON-строка с отступом 2.

## Автостарт службы

В `metrics/plugin.py` служба `metrics-hub` регистрируется с `auto=lambda config: config.section("metrics", MetricsConfig).hub`.

- Если `[metrics].hub = true` — служба стартует автоматически при запуске ноды.
- Если `false` или секции нет — служба не стартует (фиксапы тоже не применяются, `needed = metrics_hub`).

## Экспорт метрик ноды (`promexport`)

При `export = true` поднимается встроенный HTTP-эндпоинт (`MetricsEndpoint` в `promexport.py`) на оверлейном адресе и `exporter_port` по пути `/metrics`. Формат — Prometheus text (`text/plain; version=0.0.4`).

Экспортируются:
- `sa_up{kind="...",component="..."} 1` — аптайм ноды/компонентов.
- `sa_host_*` — хост-метрики (CPU, память, диски, сеть, температуры) от датчиков `monitor`.
- `sa_temperature_celsius` — температуры.

VictoriaMetrics скрейпит эти метрики через `targets/nodes.json` (порт `exporter_port`).

> **Важно**: экспорт — это **встроенный экспортёр ноды** (`sa_home_node.promexport`), а не `node_exporter`. Он знает про рой, оверлейные адреса и лейблы `node`.
