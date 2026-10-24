"""Static metadata with cached ESI fallback; station and stargate navigation."""
import math
import sqlite3
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from esi_client import ESIClient
from trader_common import DATA_DIR, DB_PATH, HUBS, REGIONS, atomic_json, iso_now, read_json


def connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing static database: {DB_PATH}")
    return sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)


def type_info(type_ids, settings):
    out = {}
    with connect() as con:
        has_volumes = con.execute("SELECT 1 FROM sqlite_master WHERE name='invVolumes'").fetchone()
        ids = sorted(type_ids)
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            q = ','.join('?' for _ in chunk)
            if has_volumes:
                sql = f"""SELECT t.typeID, t.typeName, COALESCE(v.volume, CASE WHEN g.categoryID != 6 THEN t.volume END), t.mass
                       FROM invTypes t LEFT JOIN invVolumes v ON v.typeID=t.typeID
                       LEFT JOIN invGroups g ON g.groupID=t.groupID WHERE t.typeID IN ({q})"""
            else:
                sql = f"""SELECT t.typeID,t.typeName,CASE WHEN g.categoryID != 6 THEN t.volume END,t.mass
                       FROM invTypes t LEFT JOIN invGroups g ON g.groupID=t.groupID WHERE t.typeID IN ({q})"""
            for tid, name, volume, mass in con.execute(sql, chunk):
                if name and volume and math.isfinite(volume) and volume > 0:
                    out[tid] = dict(type_name=name, volume_m3=float(volume), mass_kg=mass, source="sqlite")
    cache_path = DATA_DIR / "type_metadata.json"
    try:
        cache = read_json(cache_path)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    for tid in type_ids - out.keys():
        item = cache.get(str(tid), {})
        volume = item.get("volume_m3")
        if isinstance(volume, (float, int)) and math.isfinite(volume) and volume > 0 and item.get("type_name"):
            out[tid] = item
    missing = sorted(type_ids - out.keys())
    errors = []
    if missing and settings["resolve_missing_types"]:
        print(f"Resolving metadata for {len(missing)} missing types...", flush=True)
        client = ESIClient({**settings, "http_retries": 2, "http_timeout_seconds": 10})

        def fetch(tid):
            try:
                data = client.get(f"universe/types/{tid}/", group="universe-type").json()
                volume = data.get("packaged_volume")
                if not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume <= 0 or not data.get("name"):
                    raise ValueError("No verified packaged volume")
                return tid, dict(type_name=data["name"], volume_m3=float(volume), mass_kg=data.get("mass"), source="esi", fetched_at_utc=iso_now()), None
            except Exception as exc:
                return tid, None, str(exc)

        with ThreadPoolExecutor(max_workers=settings["download_workers"]) as pool:
            for tid, item, error in pool.map(fetch, missing):
                if item:
                    out[tid] = item
                    cache[str(tid)] = item
                else:
                    errors.append({"type_id": tid, "error": error})
        atomic_json(cache_path, cache)
    return out, sorted(type_ids - out.keys()), errors


class Navigation:
    def __init__(self, settings):
        self.settings = settings
        self.stations, self.systems = {}, {}
        self.contraband = set()
        self.graph = defaultdict(list)
        self._paths = {}
        with connect() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='invContrabandTypes'").fetchone():
                self.contraband = {row[0] for row in con.execute('SELECT DISTINCT typeID FROM invContrabandTypes')}
            for sid, name, sec, region in con.execute("SELECT solarSystemID,solarSystemName,security,regionID FROM mapSolarSystems"):
                self.systems[sid] = {"system_id": sid, "system_name": name, "security": sec, "region_id": region}
            for sid, name, system, region in con.execute("SELECT stationID,stationName,solarSystemID,regionID FROM staStations"):
                self.stations[sid] = {"station_id": sid, "station_name": name, "region_id": region, **self.systems[system]}
            for a, b in con.execute("SELECT fromSolarSystemID,toSolarSystemID FROM mapSolarSystemJumps"):
                if self.allowed_system(a) and self.allowed_system(b):
                    self.graph[a].append(b)
        start = self.stations.get(settings["start_station_id"])
        if not start or start["region_id"] != REGIONS["The_Forge"] or not self.allowed_system(start["system_id"]):
            raise ValueError("start_station_id must be a permitted NPC station in The_Forge")
        self.start_system = start["system_id"]
        self.reachable = set(self.distances(self.start_system))

    def allowed_system(self, sid):
        info = self.systems.get(sid)
        return bool(info and info["security"] >= self.settings["min_system_security"])

    def permitted_station(self, station_id, region):
        info = self.stations.get(station_id)
        return bool(info and info["region_id"] == REGIONS[region] and info["system_id"] in self.reachable
                    and (self.settings["station_scope"] == "region" or station_id == HUBS[region]))

    def distances(self, start):
        parents = {start: None}
        distances = {start: 0}
        queue = deque([start])
        while queue:
            a = queue.popleft()
            for b in self.graph[a]:
                if b not in parents:
                    parents[b] = a
                    distances[b] = distances[a] + 1
                    queue.append(b)
        self._paths[start] = parents
        return distances

    def path(self, start, end):
        if start not in self._paths:
            self.distances(start)
        parents = self._paths[start]
        if end not in parents:
            raise ValueError(f"No permitted gate route: {start} -> {end}")
        out = []
        while end is not None:
            out.append(end)
            end = parents[end]
        return [self.systems[s] for s in reversed(out)]
