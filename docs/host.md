# Слой платформы (`sa_home_node.host`)

Слой платформы отделяет ядро от ОС: только `host/__init__.py` проверяет `sys.platform`, остальные модули
платформы не знают. Всё платформозависимое — TCP-опции, uptime, процессы и сигналы, питание, самоперезапуск,
обновление — идёт через интерфейс `Host`.

## Интерфейс `Host` (`host/base.py`)

Данные:

- `name` — имя платформы (`"linux"`, `"windows"`);
- `unix_sockets` — доступны ли unix-сокеты (решает, можно ли объявлять unix-endpoint);
- `update_via_task` — обновление ставит внешняя задача ОС, а не нода (см. [lifecycle](lifecycle.md)).

Сеть и сведения о машине:

- `enable_tcp_keepalive(sock)` — ставит TCP-опции на принятый или созданный сокет;
- `system_uptime_s()` — время работы машины в секундах или `None` (нужна для роя).

Процессы и сигналы:

- `spawn_kwargs()` — дополнительные аргументы `asyncio.create_subprocess_exec` для дочерней службы;
- `terminate(proc)` — мягкая просьба дочернему процессу остановиться;
- `stop_signals()` — кортеж сигналов, которые означают «останови ноду».

Питание и жизненный цикл:

- `power_argv(action)` — argv команды для `poweroff`, `reboot` или `suspend` (иначе `ValueError`; допустимые
  действия — `POWER_ACTIONS`);
- `self_restart(argv)` — самоперезапуск процесса ноды; возвращает код, если сам не заменил процесс;
- `trigger_update_task()` — запускает внешнюю задачу обновления, возвращает `(успех, вывод)`.

Донастройка машины ([fixups](fixups.md)):

- `elevate_argv(argv)` — argv с повышением прав или `None`, если платформа его не поддерживает;
- `package_install_argv(package)` — argv установки пакета или `None`, если менеджера нет;
- `node_unit(config_path)` — описание юнита ноды: `NodeUnit(format, path, reload_argv)`.

`NodeUnit` (`host/base.py`) — неизменяемая запись: `format` — `"systemd-user"` или `"winsw"`, `path` —
файл юнита, `reload_argv` — команда перечитывания после правки файла или `None`. Объект возвращается всегда;
существует ли файл, проверяет фиксап.

Константы `base.py`: `TCP_KEEPALIVE_IDLE_S = 20`, `TCP_KEEPALIVE_INTERVAL_S = 10`, `TCP_KEEPALIVE_COUNT = 3`,
`TCP_USER_TIMEOUT_S = 30`, `WINDOWS_TCP_MAXRT = 5` — номер опции `TCP_MAXRT` из `mstcpip.h` (в модуле
`socket` такой константы нет), `RESTART_EXIT_CODE = 10`.

## `install_stop_handlers(loop, signals, callback)`

Вешает `callback` на каждый сигнал из `signals` и возвращает функцию снятия. Сначала пробует
`loop.add_signal_handler`; там, где цикл событий этого не умеет (`NotImplementedError`, цикл Windows), ставит
`signal.signal` и при снятии возвращает прежний обработчик. Нода вызывает его с `host.current().stop_signals()`
до `swarm.start()`, а процессная служба — в `run_service`.

## Выбор реализации

`select(platform)` возвращает `WindowsHost` для `win32`, иначе `LinuxHost`; `current()` — тот же вызов по
`sys.platform`, закэширован `functools.cache`, поэтому вызывается один раз на процесс. В тестах подменяют
именно `current`: `monkeypatch.setattr(host, "current", lambda: FakeHost())` — все пользователи слоя идут
через `host.current()`. Слот процессной службы принимает хост параметром (для тестов), остальные берут `current()`.

## `LinuxHost`

- `unix_sockets = True`, `update_via_task = False`;
- `enable_tcp_keepalive`: `SO_KEEPALIVE`, затем `TCP_USER_TIMEOUT` (30 с, в опцию — миллисекунды) и
  `TCP_KEEPIDLE`/`TCP_KEEPINTVL`/`TCP_KEEPCNT` из констант; отсутствующие опции пропускаются (`getattr`),
  `OSError` глушится;
- `system_uptime_s` — первое поле `/proc/uptime`, при недоступности `None`;
- `spawn_kwargs` — пусто; `terminate` — `SIGTERM`; `stop_signals` — `SIGINT`, `SIGTERM`;
- `power_argv` — `["systemctl", action]`;
- `self_restart` — `os.execv(argv[0], argv)`: процесс заменяется на месте, PID тот же (важно под systemd);
- `trigger_update_task` — задачи нет: `(False, "нет задачи обновления")`;
- `elevate_argv` — `["sudo", *argv]`;
- `package_install_argv` — первый найденный в `PATH` из `apt`, `pacman`, `dnf`, `zypper`, `apk`, `brew`
  (таблица `PACKAGE_INSTALL_ARGV`: `apt-get install -y`, `pacman -S --noconfirm`, `dnf install -y`,
  `zypper --non-interactive install`, `apk add`, `brew install`), к его argv добавляется имя пакета; нет менеджера — `None`;
- `node_unit` — `NodeUnit("systemd-user", ~/.config/systemd/user/sa-home-node.service,
  ["systemctl", "--user", "daemon-reload"])`.

## `WindowsHost`

- `unix_sockets = False` — unix-endpoint на Windows недоступен; `update_via_task = True`;
- `enable_tcp_keepalive`: `SO_KEEPALIVE`, затем `SIO_KEEPALIVE_VALS` (idle и interval в мс из констант) и
  `TCP_MAXRT` = `TCP_USER_TIMEOUT_S` (в секундах);
- `system_uptime_s` — `GetTickCount64` через ctypes (секунды);
- `spawn_kwargs` — `creationflags=CREATE_NEW_PROCESS_GROUP`: дочерняя служба в своей группе, и `Ctrl+C` ноды
  не прилетает ей напрямую;
- `terminate` — `CTRL_BREAK_EVENT` (получатель ловит `SIGBREAK`); `stop_signals` — `SIGINT`, `SIGTERM` и
  `SIGBREAK` (если есть);
- `power_argv` — `shutdown /s /t 5`, `shutdown /r /t 5`, `rundll32.exe powrprof.dll,SetSuspendState 0,1,0`;
- `self_restart` — сам ничего не делает, возвращает `RESTART_EXIT_CODE` (10): перезапуск за обёрткой службы;
- `trigger_update_task` — `schtasks /run /tn sa-home-node-autoupdate` с тайм-аутом 30 с; ошибка запуска
  или ненулевой код дают `(False, вывод)`;
- `elevate_argv` и `package_install_argv` — `None`: повышение прав и пакетный менеджер не поддержаны;
- `node_unit` — `NodeUnit("winsw", <каталог config.toml>/service/sa-home-node.xml, None)`: WinSW читает xml при
  старте службы, перезагрузки нет.
