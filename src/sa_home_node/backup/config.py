from pydantic import BaseModel, ConfigDict


class BackupConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    recipient_public_key: str = ""
    private_key_file: str = ""
    partner: str = ""
    snapshot_interval_s: float = 600.0
    snapshot_debounce_s: float = 15.0
    snapshot_poll_s: float = 300.0
