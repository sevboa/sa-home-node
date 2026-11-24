from pydantic import BaseModel, ConfigDict, Field


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    export: bool = False
    exporter_port: int = Field(default=9100, ge=1, le=65535)
    hub: bool = False
    data_dir: str = "/mnt/data/metrics"
    runtime_dir: str = "/var/lib/sa-home-metrics"
    retention: str = "1y"
    scrape_interval: str = "30s"
    grafana_port: int = Field(default=3000, ge=1, le=65535)
    vm_port: int = Field(default=8428, ge=1, le=65535)
    grafana_public_url: str = ""
    overlay_unit: str = ""
