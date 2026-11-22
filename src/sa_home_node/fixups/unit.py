import re
import shlex
from collections.abc import Callable
from xml.sax.saxutils import escape, unescape

PATH_LINE_PREFIX = "Environment=PATH="
EXEC_LINE_PREFIX = "ExecStart="

_ENTITIES = {"&quot;": '"', "&apos;": "'"}
_EXECUTABLE = re.compile(r"(<executable>)(.*?)(</executable>)", re.DOTALL)
_ARGUMENTS = re.compile(r"(<arguments>)(.*?)(</arguments>)", re.DOTALL)
_ARGUMENT = re.compile(r'"[^"]*"|\S+')


def _map_lines(text: str, prefix: str, change: Callable[[str], str]) -> str | None:
    out = []
    found = False
    for line in text.splitlines(keepends=True):
        if line.startswith(prefix):
            found = True
            body = line.rstrip("\r\n")
            out.append(prefix + change(body[len(prefix) :]) + line[len(body) :])
        else:
            out.append(line)
    return "".join(out) if found else None


def _first_value(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def rewrite_unit_path_line(content: str, wrapper_dir: str) -> str | None:
    def change(rest: str) -> str:
        return ":".join([wrapper_dir, *(d for d in rest.split(":") if d and d != wrapper_dir)])

    return _map_lines(content, PATH_LINE_PREFIX, change)


def unit_path_first_dir(content: str) -> str | None:
    value = _first_value(content, PATH_LINE_PREFIX)
    return value.split(":", 1)[0] or None if value is not None else None


def _winsw_quote(arg: str) -> str:
    return f'"{arg}"' if not arg or re.search(r"\s", arg) else arg


def _systemd_exec(text: str) -> list[str] | None:
    value = _first_value(text, EXEC_LINE_PREFIX)
    return shlex.split(value) if value is not None else None


def _winsw_exec(text: str) -> list[str] | None:
    executable = _EXECUTABLE.search(text)
    if executable is None:
        return None
    arguments = _ARGUMENTS.search(text)
    found = _ARGUMENT.findall(unescape(arguments.group(2), _ENTITIES)) if arguments else []
    return [unescape(executable.group(2), _ENTITIES), *(a.strip('"') for a in found)]


def _winsw_with_exec(text: str, argv: list[str]) -> str | None:
    if _EXECUTABLE.search(text) is None:
        return None
    if argv[1:] and _ARGUMENTS.search(text) is None:
        return None
    arguments = escape(" ".join(_winsw_quote(a) for a in argv[1:]))
    text = _EXECUTABLE.sub(lambda m: m.group(1) + escape(argv[0]) + m.group(3), text, 1)
    return _ARGUMENTS.sub(lambda m: m.group(1) + arguments + m.group(3), text, 1)


_FORMATS = {
    "systemd-user": (
        _systemd_exec,
        lambda text, argv: _map_lines(text, EXEC_LINE_PREFIX, lambda _: shlex.join(argv)),
    ),
    "winsw": (_winsw_exec, _winsw_with_exec),
}


def exec_command(unit_text: str, format: str) -> list[str] | None:
    return _FORMATS[format][0](unit_text)


def with_exec_command(unit_text: str, format: str, argv: list[str]) -> str | None:
    return _FORMATS[format][1](unit_text, argv)
