import logging
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

KNOWN_SECTIONS = ("telegram", "invites", "wake", "subscriptions", "guest_subscriptions", "about_name")


class TelegramConfig(BaseModel):
    token: str = ""
    proxy: str = ""
    proxy_mode: Literal["fixed", "auto"] = "fixed"
    extra_proxies: list[str] = []
    prefer_direct: bool = True


class SubscriptionConfig(BaseModel):
    name: str
    chat_id: int
    event_types: list[str] = Field(default_factory=lambda: ["*"])
    except_event_types: list[str] = []
    allowed_commands: list[str] = []


class GuestSubscriptionConfig(SubscriptionConfig):
    invited_by_chat_id: int = 0
    invited_at: str | datetime = ""
    invited_user: str = ""


class InvitesConfig(BaseModel):
    enabled: bool = True
    ttl_s: float = Field(default=3600.0, gt=0)
    grant_commands: list[str] = []
    grant_events: list[str] = []
    max_attempts_per_hour: int = Field(default=5, gt=0)


class WakeConfig(BaseModel):
    mac: str = ""
    ip: str = ""
    broadcast: str = "255.255.255.255"
    port: int = Field(default=9, ge=1, le=65535)
    wait_timeout_s: float = Field(default=120.0, gt=0)


class BotServiceConfig(BaseModel):
    config_path: Path
    guests_path: Path | None = None
    db_path: Path = Path("./data/bot.sqlite")
    monitor_socket: str = "./data/monitor.sock"
    apps_socket: str = "./data/apps.sock"
    egress_state_path: Path = Path("./data/egress.json")


class BotConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    invites: InvitesConfig = Field(default_factory=InvitesConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    subscriptions: list[SubscriptionConfig] = []
    guest_subscriptions: list[GuestSubscriptionConfig] = []
    about_name: str = "sa-home-node"


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
        else:
            dst[key] = value


def _merge_configs(main: dict[str, Any], package: dict[str, Any], guests: Path | None) -> dict[str, Any]:
    shadowed = sorted(key for key in package if key in main)
    if shadowed:
        logger.warning("Пакет перекрывает секции основного конфига: %s", " ".join(f"[{s}]" for s in shadowed))
    _merge(main, package)
    if guests is not None and Path(guests).is_file():
        with guests.open("rb") as f:
            guest_data = tomllib.load(f)
        _merge(main, guest_data)
    return main


def load_bot_config(config_path: Path, package_path: Path, guests_path: Path | None) -> BotConfig:
    main = _load_toml(config_path)
    package = _load_toml(package_path)
    merged = _merge_configs(main, package, guests_path)
    data = {key: merged[key] for key in merged if key in KNOWN_SECTIONS}
    return BotConfig.model_validate(data)
