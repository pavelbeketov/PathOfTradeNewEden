"""Build executable arbitrage candidates and a cargo/cash constrained round trip."""
import bisect
import csv
import io
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from market_data import Navigation, type_info
from trader_common import (REGIONS, RESULTS_DIR, atomic_json, atomic_text, iso_now,
                           load_settings, load_snapshot, parse_time, settings_fingerprint, utcnow)


def cents(value):
    return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_CEILING))


def order_money(price, settings, buy):
    rate = (Decimal(1) + Decimal(str(settings['buy_fee_pct'])) / 100 if buy else
            Decimal(1) - (Decimal(str(settings['sales_tax_pct'])) + Decimal(str(settings['sell_fee_pct']))) / 100)
    rounding = ROUND_CEILING if buy else ROUND_FLOOR
    return int((Decimal(str(price)) * rate * 100).to_integral_value(rounding=rounding))


@dataclass(slots=True)
class Candidate:
    source: dict
    destination: dict
    meta: dict
    cost: int
    net: int
    max_units: int

    @property
    def profit(self):
        return self.net - self.cost

    @property
    def volume(self):
        return self.meta['volume_m3']

    def row(self, settings, nav, units=None):
        a, b = self.source, self.destination
        cargo_units = math.floor(settings['cargo_m3'] / self.volume)
        budget_units = (cents(settings['budget_isk']) - cents(settings['cash_reserve_isk'])) // self.cost
        cap = min(self.max_units, cargo_units, budget_units)
        if units is None:
            units = cap
        sa, sb = nav.stations[a['location_id']], nav.stations[b['location_id']]
        return dict(type_id=a['type_id'], type_name=self.meta['type_name'], volume_m3=self.volume,
                    mass_kg=self.meta.get('mass_kg'), buy_region=a['region'], buy_region_id=REGIONS[a['region']],
                    buy_location_id=a['location_id'], buy_station=sa['station_name'], buy_system=sa['system_name'],
                    buy_system_id=sa['system_id'], buy_order_id=a['order_id'], buy_price=a['price'],
                    buy_cost_per_unit=self.cost / 100, buy_volume_remain=a['volume_remain'],
                    sell_region=b['region'], sell_region_id=REGIONS[b['region']], sell_location_id=b['location_id'],
                    sell_station=sb['station_name'], sell_system=sb['system_name'], sell_system_id=sb['system_id'],
                    sell_order_id=b['order_id'], sell_price_gross=b['price'], sell_price_net=self.net / 100,
                    sell_volume_remain=b['volume_remain'], min_sell_units=min(b['min_volume'],b['volume_remain']),
                    profit_per_unit=self.profit / 100, profit_pct=100 * self.profit / self.cost,
                    max_units_by_orders=min(a['volume_remain'], b['volume_remain']), max_units_by_cargo=cargo_units,
                    max_units_by_budget=budget_units, max_units_final=units, max_profit_est=self.profit * units / 100)


def build_candidates(dumps, settings, nav):
    sells, buys = defaultdict(list), defaultdict(list)
    skipped = defaultdict(int)
    now = utcnow()
    for region, payload in dumps.items():
        print(f"{region}: {len(payload['orders']):,} orders", flush=True)
        for o in payload['orders']:
            if o['volume_remain'] <= 0:
                continue
            # min_volume is an execution constraint on buy orders. An active
            # order with less stock than its required fill cannot accept a sale.
            if o['is_buy_order'] and o['min_volume'] > o['volume_remain']:
                skipped['unfillable_min_volume'] += 1
                continue
            if settings['exclude_contraband'] and o['type_id'] in nav.contraband:
                skipped['contraband_orders'] += 1
                continue
            if not nav.permitted_station(o['location_id'], region):
                skipped['station_or_security'] += 1
                continue
            if parse_time(o['issued']) + timedelta(days=o['duration']) <= now:
                skipped['expired_orders'] += 1
                continue
            item = {k:o[k] for k in ['type_id','price','location_id','order_id','volume_remain','min_volume']}
            if not o['is_buy_order']:
                item['min_volume'] = 1
            item['region'] = region
            item['money'] = order_money(o['price'], settings, not o['is_buy_order'])
            (buys if o['is_buy_order'] else sells)[o['type_id']].append(item)
        # Release large input objects as soon as the compact order books exist.
        payload['orders'] = []
    possible = set()
    mult = 1 + settings['min_profit_pct'] / 100
    min_profit = cents(settings['min_profit_per_unit_isk'])
    for tid in sells.keys() & buys.keys():
        low = min(o['money'] for o in sells[tid])
        high = max(o['money'] for o in buys[tid])
        if high > low and high >= low * mult and high - low >= min_profit:
            possible.add(tid)
    info, missing, errors = type_info(possible, settings)
    skipped['unknown_type_ids'] = missing
    skipped['metadata_errors'] = errors
    out = []
    for tid in sorted(possible & info.keys()):
        meta = info[tid]
        capacity = math.floor(settings['cargo_m3'] / meta['volume_m3'])
        if capacity < 1:
            skipped['types_too_large'] += 1
            continue
        def relevant_levels(book, reverse):
            # Beyond one full hold of strictly better, unrestricted liquidity,
            # worse levels cannot improve any feasible trade at this region.
            # Orders with a minimum > 1 do not count as unrestricted liquidity.
            kept = []
            per_region = defaultdict(list)
            for order in book:
                per_region[order['region']].append(order)
            for orders in per_region.values():
                covered = 0
                for order in sorted(orders, key=lambda o:(o['money'],o['order_id']), reverse=reverse):
                    minimum = min(order['min_volume'],order['volume_remain'])
                    if minimum > capacity:
                        continue
                    if covered >= capacity:
                        break
                    kept.append(order)
                    if minimum <= 1:
                        covered += order['volume_remain']
            return kept

        bb = sorted(relevant_levels(buys[tid], True), key=lambda o:(o['money'],o['order_id']))
        prices = [o['money'] for o in bb]
        for a in relevant_levels(sells[tid], False):
            threshold = max(a['money'] + max(1, min_profit), math.ceil(a['money'] * mult - 1e-7))
            for b in bb[bisect.bisect_left(prices, threshold):]:
                if a['region'] == b['region']:
                    continue
                cap = min(a['volume_remain'],b['volume_remain'],capacity)
                if cap > 0:
                    out.append(Candidate(a,b,meta,a['money'],b['money'],cap))
    print(f"Eligible order pairs: {len(out):,}; unresolved types excluded: {len(missing)}", flush=True)
    return out, dict(skipped)


def standalone_rows(candidates, settings, nav):
    valid = []
    for c in candidates:
        cap = min(c.max_units, (cents(settings['budget_isk']) - cents(settings['cash_reserve_isk'])) // c.cost)
        required = max(min(c.source['min_volume'],c.source['volume_remain']),
                       min(c.destination['min_volume'],c.destination['volume_remain']))
        if cap >= max(1, required):
            valid.append((c.profit * cap, c.source['order_id'], c.destination['order_id'], c, cap))
    # Explicit scalar tie breakers; dictionaries are never compared.
    valid.sort(key=lambda x:x[:3], reverse=True)
    return [c.row(settings, nav, qty) for _,_,_,c,qty in valid[:settings['top_n']]]


def analyze(settings=None):
    settings = settings or load_settings()
    dumps, _ = load_snapshot(settings)
    nav = Navigation(settings)
    candidates, _ = build_candidates(dumps, settings, nav)
    return standalone_rows(candidates, settings, nav)


def save_csv(rows, path):
    fields = list(rows[0]) if rows else ['type_id','type_name','max_units_final','max_profit_est']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, stream.getvalue())


def main():
    from route_optimizer import optimize_loop
    started = time.perf_counter()
    settings = load_settings()
    dumps, manifest = load_snapshot(settings)
    snapshot_times = {r:{k:d['meta'].get(k) for k in ['downloaded_at_utc','checked_at_utc']} for r,d in dumps.items()}
    nav = Navigation(settings)
    candidates, skipped = build_candidates(dumps, settings, nav)
    rows = standalone_rows(candidates, settings, nav)
    plan = optimize_loop(candidates, settings, nav)
    analysis = dict(schema_version=2, generated_at_utc=iso_now(), snapshot_id=manifest['snapshot_id'],
                    snapshot_times=snapshot_times, settings=settings, settings_hash=settings_fingerprint(settings),
                    skipped=skipped, candidate_count=len(candidates), rows=rows, plan=plan)
    atomic_json(RESULTS_DIR / 'arbitrage_top.json', rows)
    save_csv(rows, RESULTS_DIR / 'arbitrage_top.csv')
    atomic_json(RESULTS_DIR / 'analysis.json', analysis)
    print(f"Plan: {' -> '.join(plan['route'])}")
    print(f"Estimated profit: {plan['profit_isk']:,.2f} ISK; {plan['trade_count']} trades")
    print(f"Calculated in {time.perf_counter()-started:.1f}s. Run Make_report.py.")


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'CALCULATION FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
