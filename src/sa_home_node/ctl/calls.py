from typing import Any

from sa_home_node.proto.describe import ServiceDescription


def parse_call_args(pairs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise ValueError(f"аргумент без «=»: {pair!r}")
        args[key] = _guess_type(raw)
    return args


def _guess_type(raw: str) -> Any:
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


def render_describe(desc: ServiceDescription) -> str:
    lines = [f"Служба {desc.info.service} на {desc.info.node} (v{desc.info.version})"]
    for action in desc.actions:
        params = ", ".join(
            f"{p.name}{'' if p.required else '?'}:{p.type}"
            + (f" [{'|'.join(p.choices)}]" if p.choices else "")
            for p in action.params
        )
        lines.append(f"  {action.id:16} {action.title}" + (f"  ({params})" if params else ""))
    return "\n".join(lines)
