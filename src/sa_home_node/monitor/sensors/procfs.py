from __future__ import annotations

STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")

NetCounters = tuple[int, int, int, int]

_NETDEV_RX_ERRS, _NETDEV_RX_DROP = 2, 3
_NETDEV_TX_ERRS, _NETDEV_TX_DROP = 10, 11


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def parse_proc_stat_cpu(text: str) -> dict[str, int]:
    for line in text.splitlines():
        if line.startswith("cpu "):
            try:
                nums = [int(x) for x in line.split()[1:]]
            except ValueError:
                return {}
            return {name: nums[i] for i, name in enumerate(STAT_FIELDS) if i < len(nums)}
    return {}


def cpu_deltas(prev: dict[str, int], cur: dict[str, int]) -> tuple[float, float]:
    total = sum(cur.get(k, 0) - prev.get(k, 0) for k in STAT_FIELDS)
    if total <= 0:
        return 0.0, 0.0
    steal = (cur.get("steal", 0) - prev.get("steal", 0)) / total * 100
    iowait = (cur.get("iowait", 0) - prev.get("iowait", 0)) / total * 100
    return max(0.0, steal), max(0.0, iowait)


def parse_meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields:
            try:
                out[key.strip()] = int(fields[0])
            except ValueError:
                continue
    return out


def parse_loadavgs(text: str) -> tuple[float, float, float] | None:
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def parse_pressure_some_avg60(text: str) -> float | None:
    for line in text.splitlines():
        if line.startswith("some"):
            for tok in line.split():
                if tok.startswith("avg60="):
                    try:
                        return float(tok.split("=", 1)[1])
                    except ValueError:
                        return None
    return None


def parse_vmstat(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def parse_proc_net_dev(text: str) -> dict[str, NetCounters]:
    out: dict[str, NetCounters] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        iface = name.strip()
        if not sep or iface == "lo":
            continue
        nums = rest.split()
        if len(nums) < 16:
            continue
        try:
            vals = [int(x) for x in nums]
        except ValueError:
            continue
        out[iface] = (
            vals[_NETDEV_RX_ERRS],
            vals[_NETDEV_RX_DROP],
            vals[_NETDEV_TX_ERRS],
            vals[_NETDEV_TX_DROP],
        )
    return out


def net_err_delta(prev: dict[str, NetCounters], cur: dict[str, NetCounters]) -> int:
    total = 0
    for iface, (rx_errs, _, tx_errs, _) in cur.items():
        before = prev.get(iface)
        if before is None:
            continue
        total += max(0, rx_errs - before[0]) + max(0, tx_errs - before[2])
    return total


def parse_conntrack(count_text: str, max_text: str) -> float | None:
    try:
        count = int(count_text.strip())
        limit = int(max_text.strip())
    except ValueError:
        return None
    if limit <= 0:
        return None
    return count / limit * 100
