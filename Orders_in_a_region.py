"""Download a complete, checked snapshot. Run: py Orders_in_a_region.py."""
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import parsedate_to_datetime

from esi_client import ESIClient
from trader_common import (DATA_DIR, REGIONS, atomic_json, dump_path, iso_now,
                           load_settings, read_json, utcnow, validate_dump)


def fetch_region(client, region, pool, cached=None):
    path = f"markets/{REGIONS[region]}/orders/"
    for attempt in range(3):
        if attempt == 0 and cached:
            expires = cached["meta"].get("headers", {}).get("expires")
            if expires and parsedate_to_datetime(expires) > utcnow() + timedelta(seconds=5):
                return cached
        # Page-one ETag does not validate a whole paginated snapshot. Refetch all
        # pages after expiry; reuse a complete validated local dump before expiry.
        first = client.get(path, {"order_type": "all", "page": 1})
        if first.status_code != 200:
            raise ValueError(f"{region}: unexpected HTTP {first.status_code}")
        pages = int(first.headers["X-Pages"])
        if pages < 1:
            raise ValueError(f"{region}: invalid X-Pages")
        expires = first.headers.get("Expires")
        if expires and (parsedate_to_datetime(expires) - utcnow()).total_seconds() < 15:
            if attempt == 2:
                raise ValueError(f"{region}: cache repeatedly expires during download")
            time.sleep(max(1, (parsedate_to_datetime(expires) - utcnow()).total_seconds() + 1))
            continue
        modified = first.headers.get("Last-Modified")
        if pages > 1 and not modified:
            raise ValueError(f"{region}: cannot verify pages without Last-Modified")

        def get_page(page):
            response = client.get(path, {"order_type": "all", "page": page})
            if response.status_code != 200 or response.headers.get("Last-Modified") != modified or int(response.headers.get("X-Pages", pages)) != pages:
                raise ValueError(f"{region}: page cache changed during download")
            return response.json()

        try:
            orders = first.json()
            if not isinstance(orders, list):
                raise ValueError("ESI response must be an array")
            for chunk in pool.map(get_page, range(2, pages + 1)):
                if not isinstance(chunk, list):
                    raise ValueError("ESI page must be an array")
                orders.extend(chunk)
            payload = {"meta": {"region_name": region, "region_id": REGIONS[region],
                       "order_type": "all", "downloaded_at_utc": iso_now(), "count": len(orders),
                       "headers": {"x_pages": pages, "etag": first.headers.get("ETag"),
                                   "last_modified": modified, "expires": expires}}, "orders": orders}
            validate_dump(payload, region)
            return payload
        except ValueError:
            if attempt == 2:
                raise
    raise ValueError(f"{region}: failed to obtain consistent snapshot")


def main():
    settings = load_settings()
    snapshot_id = str(uuid.uuid4())
    manifest_path = DATA_DIR / "scan_manifest.json"
    manifest = {"snapshot_id": snapshot_id, "started_at_utc": iso_now(), "success": False, "regions": {}}
    atomic_json(manifest_path, manifest)
    client = ESIClient(settings)
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=settings["download_workers"]) as pool:
            for region in REGIONS:
                print(f"Downloading {region}...", flush=True)
                cached = None
                try:
                    cached = validate_dump(read_json(dump_path(region)), region)
                except (OSError, ValueError, TypeError, KeyError):
                    pass
                payload = fetch_region(client, region, pool, cached)
                payload["meta"].update(snapshot_id=snapshot_id, checked_at_utc=iso_now())
                atomic_json(dump_path(region), payload)
                manifest["regions"][region] = {"count": len(payload["orders"]),
                                                   "checked_at_utc": payload["meta"]["checked_at_utc"]}
                print(f"{region}: {len(payload['orders']):,} orders", flush=True)
        manifest.update(success=True, completed_at_utc=iso_now())
        atomic_json(manifest_path, manifest)
    except Exception as exc:
        manifest.update(error=str(exc), completed_at_utc=iso_now())
        atomic_json(manifest_path, manifest)
        raise
    print(f"Snapshot ready in {time.perf_counter() - started:.1f}s. Run Calculate_profit.py.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SCAN FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
