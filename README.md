# sa-home-node

Ядро домашнего роя машин. Каждая машина запускает ноду: она следит за собой, держит службы и
разговаривает с соседями. Одна из нод держит Telegram-бота, через которого рой управляется.
Всё, что специфично для владельца (ассистент, VPN, модели, тексты), подключается плагинами и контентом.

Статус: ранний каркас, идёт перенос из прототипа.

## Устройство
- `src/sa_home_node/` — пакет ядра.
- `docs/` — описание модулей.
- Фиксапы хоста (донастройка машины под назначения, команда `sa-home-node fix`) — [docs/fixups.md](docs/fixups.md).
- Плагины — Python-пакеты с точкой входа `sa_home_node.plugins`; службы плагина, назначения и аренда — [docs/plugins.md](docs/plugins.md), [docs/services.md](docs/services.md).
- Мониторинг (плагин `monitor`) — датчики, политики, экспорт метрик — [docs/monitor.md](docs/monitor.md).
- Хаб метрик (плагин `metrics`) — VictoriaMetrics и Grafana на хабе, цели и дашборды от плагинов — [docs/metrics.md](docs/metrics.md).
- Бэкап — запечатанные копии identity и снапшоты БД на напарнике — [docs/backup.md](docs/backup.md).
- Wake-on-LAN — будит спящую ноду и доводит службу до готовности — [docs/wake.md](docs/wake.md).
- Отложенные задачи (плагин `tasks`) — очередь команд с таймерами и ожиданием событий — [docs/tasks.md](docs/tasks.md).

## Зависимости
- Python 3.11+. Основные: `aiogram`, `aiohttp-socks`, `aiosqlite`, `certifi`, `cryptography`, `psutil`,
  `pydantic-settings`. `cryptography` обязателен: команду `backup` грузит общий CLI.
- На Python 3.11 `aiogram` закреплён на 3.32: с 3.33 он не импортируется (`TypeError: unhashable type:
  'FailFast'` в `aiogram.types`), проверено 2026-11-26.

## Лицензия
[PolyForm Noncommercial 1.0.0](LICENSE): пользоваться, изменять и распространять можно в некоммерческих
целях; продавать и использовать коммерчески нельзя.
