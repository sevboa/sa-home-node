from pathlib import Path

from pydantic import BaseModel, Field


class TasksConfig(BaseModel):
    socket: str = "./data/tasks.sock"
    db_path: Path = Path("./data/tasks.sqlite")
    default_timeout_s: float = Field(default=240.0, gt=0)
    warmup_timeout_s: float = Field(default=360.0, gt=0)
