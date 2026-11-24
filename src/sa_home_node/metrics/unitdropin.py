DROPIN_NAME = "50-sa-home-overlay.conf"


def overlay_dropin_content(overlay_unit: str) -> str:
    lines = ["[Unit]"]
    if overlay_unit:
        lines.append(f"After={overlay_unit}")
        lines.append(f"Wants={overlay_unit}")
    lines.append("StartLimitIntervalSec=0")
    lines.append("")
    lines.append("[Service]")
    lines.append("Restart=on-failure")
    lines.append("RestartSec=5")
    lines.append("")
    return "\n".join(lines)
