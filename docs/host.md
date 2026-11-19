# Слой платформы (`sa_home_node.host`)

Слой платформы отделяет ядро от ОС: только `host/__init__.py` проверяет
`sys.platform`, остальные модули платформы не знают.

## Интерфейс `Host` (`host/base.py`)

- `name` — имя платформы (`"linux"`, `"windows"`);
- `unix_sockets` — доступны ли unix-сокеты (решает, можно ли объявлять unix-endpoint);
- `enable_tcp_keepalive(sock)` — ставит TCP-опции на принятый или созданный сокет;
- `system_uptime_s()` — время работы машины в секундах или `None` (нужна для роя).

Константы `base.py`: `TCP_KEEPALIVE_IDLE_S = 20`, `TCP_KEEPALIVE_INTERVAL_S = 10`,
`TCP_KEEPALIVE_COUNT = 3`, `TCP_USER_TIMEOUT_S = 30`, `WINDOWS_TCP_MAXRT = 5` — номер
опции `TCP_MAXRT` из `mstcpip.h` (в модуле `socket` такой константы нет).

## Выбор реализации

`select(platform)` возвращает `WindowsHost` для `win32`, иначе `LinuxHost`;
`current()` — тот же вызов по `sys.platform`, закэширован `functools.cache`, поэтому
вызывается один раз на процесс. В тестах подменяют именно `current`:
`monkeypatch.setattr(host, "current", lambda: FakeHost())` — все пользователи слоя
(`proto`, `endpoints`) идут через `host.current()`.

## `LinuxHost`

- `unix_sockets = True`;
- `enable_tcp_keepalive`: `SO_KEEPALIVE`, затем `TCP_USER_TIMEOUT` (30 с, в опцию — миллисекунды) и
  `TCP_KEEPIDLE`/`TCP_KEEPINTVL`/`TCP_KEEPCNT` из констант; отсутствующие опции
  пропускаются (`getattr`), `OSError` глушится;
- `system_uptime_s` — читает `/proc/uptime` (первое поле), при недоступности — `None`.

## `WindowsHost`

- `unix_sockets = False` — unix-endpoint на Windows недоступен;
- `enable_tcp_keepalive`: `SO_KEEPALIVE`, затем `SIO_KEEPALIVE_VALS` (idle и
  interval в мс из констант) и `TCP_MAXRT` = `TCP_USER_TIMEOUT_S` (в секундах);
- `system_uptime_s` — `GetTickCount64` через ctypes (секунды).

## Что добавят следующие области

Слой платформы будет расти: питание (сон/пробуждение) и процессы — тоже через
интерфейс `Host` и выбор по платформе, детали появятся с соответствующими областями.
