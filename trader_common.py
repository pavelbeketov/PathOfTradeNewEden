"""Shared settings, safe file writes and snapshot validation."""
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
DB_PATH = ROOT / "sqlite-latest.sqlite"
SETTINGS_PATH = ROOT / "settings.json"
REGIONS = {"The_Forge": 10000002, "Sinq_Laison": 10000032,
           "Metropolis": 10000042, "Heimatar": 10000030, "Domain": 10000043}
HUBS = {"The_Forge": 60003760, "Sinq_Laison": 60011866,
        "Metropolis": 60005686, "Heimatar": 60004588, "Domain": 60008494}


def utcnow():
    return datetime.now(timezone.utc)


def iso_now():
    return utcnow().isoformat()


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, delete=False, suffix=".tmp") as f:
            name = f.name
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def settings_fingerprint(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def load_settings(path=None):
    s = read_json(path or SETTINGS_PATH)
    if not isinstance(s, dict):
        raise ValueError("settings.json: ожидается объект")
    positive = ["budget_isk", "cargo_m3", "max_snapshot_age_minutes",
                "max_snapshot_skew_seconds", "http_timeout_seconds", "solver_time_limit_seconds"]
    nonnegative = ["cash_reserve_isk", "sales_tax_pct", "buy_fee_pct", "sell_fee_pct",
                   "min_profit_pct", "min_profit_per_unit_isk", "solver_relative_gap"]
    for k in positive + nonnegative + ["min_system_security"]:
        v = s.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"settings.json: {k} должен быть конечным числом")
        if (k in positive and v <= 0) or (k in nonnegative and v < 0):
            raise ValueError(f"settings.json: недопустимое значение {k}")
    for k in ["top_n", "download_workers", "http_retries", "start_station_id"]:
        if type(s.get(k)) is not int or s[k] <= 0:
            raise ValueError(f"settings.json: {k} должен быть положительным целым")
    for k in ["optimize_region_order", "resolve_missing_types", "exclude_contraband"]:
        if type(s.get(k)) is not bool:
            raise ValueError(f"settings.json: {k} должен быть true/false")
    if s["download_workers"] > 16 or s["http_retries"] > 20:
        raise ValueError("settings.json: download_workers <= 16, http_retries <= 20")
    if not 0 <= s["min_system_security"] <= 1:
        raise ValueError("settings.json: min_system_security от 0 до 1")
    if s["cash_reserve_isk"] >= s["budget_isk"]:
        raise ValueError("settings.json: резерв должен быть меньше бюджета")
    if s["sales_tax_pct"] + s["sell_fee_pct"] >= 100 or s["buy_fee_pct"] >= 100:
        raise ValueError("settings.json: комиссии должны быть меньше 100%")
    if s["solver_relative_gap"] >= 1:
        raise ValueError("settings.json: solver_relative_gap должен быть меньше 1")
    route = s.get("route")
    if not isinstance(route, list) or len(route) != 6 or route[0] != "The_Forge" or route[-1] != route[0] or set(route[:-1]) != set(REGIONS):
        raise ValueError("settings.json: route должен содержать пять регионов по одному разу и возврат в The_Forge")
    if s.get("station_scope") not in ("region", "hubs"):
        raise ValueError("settings.json: station_scope = region или hubs")
    return s


def dump_path(region):
    return DATA_DIR / f"region_{REGIONS[region]}_{region}_all.json"


def validate_dump(payload, region):
    if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
        raise ValueError(f"{region}: неверный формат дампа")
    m, orders = payload.get("meta", {}), payload["orders"]
    if m.get("region_id") != REGIONS[region] or m.get("region_name") != region or m.get("order_type") != "all" or m.get("count") != len(orders):
        raise ValueError(f"{region}: несовпадение метаданных дампа")
    ids = set()
    for o in orders:
        if not isinstance(o, dict) or type(o.get("order_id")) is not int or o["order_id"] in ids:
            raise ValueError(f"{region}: неверный или повторяющийся order_id")
        ids.add(o["order_id"])
        for k in ["type_id", "location_id", "system_id", "volume_remain", "volume_total", "min_volume", "duration"]:
            if type(o.get(k)) is not int or o[k] < 0:
                raise ValueError(f"{region}: неверное поле {k}")
        if type(o.get("is_buy_order")) is not bool or not isinstance(o.get("price"), (int, float)) or not math.isfinite(o["price"]) or o["price"] <= 0:
            raise ValueError(f"{region}: неверная цена или тип ордера")
        if o["volume_remain"] > o["volume_total"]:
            raise ValueError(f"{region}: остаток больше исходного объёма")
        parse_time(o["issued"])
    return payload


def load_snapshot(settings):
    path = DATA_DIR / "scan_manifest.json"
    if not path.exists():
        raise ValueError("Сначала запусти Orders_in_a_region.py: нужен подтверждённый полный снимок")
    manifest = read_json(path)
    if manifest.get("success") is not True or set(manifest.get("regions", {})) != set(REGIONS):
        raise ValueError("Последнее сканирование не завершено успешно. Повтори Orders_in_a_region.py")
    now, times, dumps = utcnow(), [], {}
    for region in REGIONS:
        payload = validate_dump(read_json(dump_path(region)), region)
        expected = manifest["regions"][region]
        if payload["meta"].get("snapshot_id") != manifest.get("snapshot_id") or payload["meta"].get("count") != expected.get("count"):
            raise ValueError(f"{region}: файл не принадлежит последнему сканированию")
        checked = parse_time(payload["meta"]["checked_at_utc"])
        if not -60 <= (now - checked).total_seconds() <= settings["max_snapshot_age_minutes"] * 60:
            raise ValueError(f"{region}: снимок устарел; повтори Orders_in_a_region.py")
        times.append(checked)
        dumps[region] = payload
    if (max(times) - min(times)).total_seconds() > settings["max_snapshot_skew_seconds"]:
        raise ValueError("Слишком большой разброс времени регионов: повтори сканирование")
    return dumps, manifest
