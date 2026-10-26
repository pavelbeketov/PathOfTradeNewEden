"""Small, cash-settled station chains with at most three item types per stop.

Each leg is an integer cargo/budget problem. A bounded beam search connects
these legs; the resulting station route is deliberately a heuristic.
"""
import math
import time
from collections import defaultdict
from dataclasses import dataclass

from Calculate_profit import cents

MAX_STATIONS = 6
MAX_PURCHASE_TYPES = 3
BEAM_WIDTH = 8
BRANCH_WIDTH = 6
SEED_LIMIT = 32


def _remaining(order, side, used):
    return max(0, order['volume_remain'] - used.get((side, order['order_id']), 0))


def _minimum(order):
    return min(order['min_volume'], order['volume_remain'])


def _validate_leg(selected, settings, cash, used):
    orders, amounts = {}, defaultdict(int)
    types = set()
    spent, cargo = 0, 0.0
    for c, q in selected:
        if type(q) is not int or not 0 < q <= c.max_units or c.profit <= 0:
            raise ValueError('Invalid short-route quantity or profit')
        types.add(c.source['type_id'])
        spent += c.cost * q
        cargo += c.volume * q
        for side, order in [('purchase', c.source), ('sale', c.destination)]:
            key = (side, order['order_id'])
            amounts[key] += q
            orders[key] = order
    if len(types) > MAX_PURCHASE_TYPES:
        raise ValueError('More than three purchase positions at a station')
    if spent > cash - cents(settings['cash_reserve_isk']) or cargo > settings['cargo_m3'] + 1e-6:
        raise ValueError('Short-route cash or cargo limit exceeded')
    for key, q in amounts.items():
        if not _minimum(orders[key]) <= q <= _remaining(orders[key], key[0], used):
            raise ValueError('Short-route order liquidity or minimum violated')
    return True


def _greedy_leg(candidates, settings, cash, used):
    """Keep a feasible incumbent even when the integer solver runs out of time."""
    reserve = cents(settings['cash_reserve_isk'])
    budget, cargo = cash - reserve, settings['cargo_m3']
    local, types, selected = dict(used), set(), []
    def capacity(c):
        return min(c.max_units, _remaining(c.source, 'purchase', used),
                   _remaining(c.destination, 'sale', used),
                   max(0, budget // c.cost), math.floor(cargo / c.volume))
    ranked = sorted(candidates, key=lambda c: (
        -c.profit * capacity(c), c.source['order_id'], c.destination['order_id']))
    for c in ranked:
        tid = c.source['type_id']
        if tid not in types and len(types) == MAX_PURCHASE_TYPES:
            continue
        q = min(c.max_units, _remaining(c.source, 'purchase', local),
                _remaining(c.destination, 'sale', local),
                max(0, budget // c.cost), math.floor(max(0, cargo) / c.volume))
        if q < max(1, _minimum(c.source), _minimum(c.destination)):
            continue
        selected.append((c, q))
        types.add(tid)
        budget -= c.cost * q
        cargo -= c.volume * q
        for side, order in [('purchase', c.source), ('sale', c.destination)]:
            key = (side, order['order_id'])
            local[key] = local.get(key, 0) + q
    return selected


def select_short_leg(candidates, settings, cash_cents, used, time_limit=1.0):
    """Choose up to three types between one pair of actual stations.

    Money is fee-adjusted integer cents. Minimum fills apply to pooled orders
    at this visit, and order availability is reduced by all earlier legs.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    budget = cash_cents - cents(settings['cash_reserve_isk'])
    choices, caps = [], []
    for c in candidates:
        if c.profit <= 0 or c.cost <= 0 or c.volume <= 0:
            continue
        if (_remaining(c.source, 'purchase', used) < _minimum(c.source) or
                _remaining(c.destination, 'sale', used) < _minimum(c.destination)):
            continue
        cap = min(c.max_units, _remaining(c.source, 'purchase', used),
                  _remaining(c.destination, 'sale', used),
                  max(0, budget // c.cost), math.floor(settings['cargo_m3'] / c.volume))
        if cap > 0:
            choices.append(c)
            caps.append(cap)
    if not choices:
        return []
    best = _greedy_leg(choices, settings, cash_cents, used)
    if time_limit <= 0:
        return best
    n = len(choices)
    objective = [-c.profit / 100 for c in choices]
    upper = list(caps)
    rr, cc, vv, lo, hi = [], [], [], [], []
    def constraint(terms, low=-np.inf, high=np.inf):
        row = len(lo)
        for j, value in terms:
            rr.append(row)
            cc.append(j)
            vv.append(value)
        lo.append(low)
        hi.append(high)
    def binary():
        j = len(objective)
        objective.append(0)
        upper.append(1)
        return j
    groups, types = defaultdict(list), defaultdict(list)
    for j, c in enumerate(choices):
        groups[('purchase', c.source['order_id'])].append(j)
        groups[('sale', c.destination['order_id'])].append(j)
        types[c.source['type_id']].append(j)
    for (side, _), indexes in groups.items():
        c = choices[indexes[0]]
        order = c.source if side == 'purchase' else c.destination
        cap = _remaining(order, side, used)
        terms = [(j, 1) for j in indexes]
        if _minimum(order) > 1:
            z = binary()
            constraint(terms + [(z, -cap)], high=0)
            constraint(terms + [(z, -_minimum(order))], low=0)
        else:
            constraint(terms, high=cap)
    activations = []
    for indexes in types.values():
        z = binary()
        activations.append(z)
        constraint([(j, 1) for j in indexes] + [(z, -sum(caps[j] for j in indexes))], high=0)
    constraint([(z, 1) for z in activations], high=MAX_PURCHASE_TYPES)
    constraint([(j, c.cost / 100) for j, c in enumerate(choices)], high=budget / 100)
    constraint([(j, c.volume) for j, c in enumerate(choices)], high=settings['cargo_m3'])
    matrix = coo_matrix((vv, (rr, cc)), shape=(len(lo), len(objective))).tocsc()
    result = milp(np.asarray(objective), integrality=np.ones(len(objective)),
                  bounds=Bounds(np.zeros(len(objective)), np.asarray(upper)),
                  constraints=LinearConstraint(matrix, np.asarray(lo), np.asarray(hi)),
                  options={'time_limit': max(0.001, time_limit),
                           'mip_rel_gap': settings['solver_relative_gap'], 'presolve': True})
    if result.status not in (0, 1):
        raise RuntimeError(f'Short-route optimizer failed: {result.message}')
    if result.x is not None and all(abs(x - round(x)) <= 1e-4 for x in result.x):
        selected = [(c, int(round(result.x[j]))) for j, c in enumerate(choices) if round(result.x[j]) > 0]
        try:
            _validate_leg(selected, settings, cash_cents, used)
        except ValueError:
            # Reject numerical infeasibility using the exact execution checks.
            selected = []
        if sum(c.profit * q for c, q in selected) > sum(c.profit * q for c, q in best):
            best = selected
    _validate_leg(best, settings, cash_cents, used)
    return best


def validate_short_selection(selected, settings, station_route):
    """Replay every complete leg independently; there is no carried inventory."""
    if len(station_route) > MAX_STATIONS:
        raise ValueError('Short route exceeds six station visits')
    if not selected:
        if station_route:
            raise ValueError('Empty short route contains unnecessary station visits')
        return True
    if len(station_route) < 2:
        raise ValueError('Short trades need purchase and sale stations')
    legs = defaultdict(list)
    for c, q, a, b in selected:
        if (type(a) is not int or type(b) is not int or not 0 <= a < b < len(station_route) or
                b != a + 1 or c.source['location_id'] != station_route[a] or
                c.destination['location_id'] != station_route[b] or station_route[a] == station_route[b]):
            raise ValueError('Invalid short-route station sequence')
        legs[a].append((c, q))
    cash, used = cents(settings['budget_isk']), {}
    for step in range(len(station_route) - 1):
        if not legs[step]:
            raise ValueError('Short route contains a leg without trades')
        _validate_leg(legs[step], settings, cash, used)
        cash += sum(c.profit * q for c, q in legs[step])
        for c, q in legs[step]:
            for side, order in [('purchase', c.source), ('sale', c.destination)]:
                key = (side, order['order_id'])
                used[key] = used.get(key, 0) + q
    return True


@dataclass
class _Chain:
    stations: tuple
    selected: list
    used: dict
    cash: int
    jumps: int


def _itinerary(selected, station_route, settings, nav):
    cash, cargo, steps, jumps = cents(settings['budget_isk']), 0.0, [], 0
    for i, sid in enumerate(station_route):
        station, events = nav.stations[sid], []
        path = nav.path(nav.stations[station_route[i - 1]]['system_id'], station['system_id']) if i else []
        leg_jumps = max(0, len(path) - 1)
        jumps += leg_jumps
        for side in ['sell', 'buy']:
            grouped = {}
            for c, q, a, b in selected:
                if (b if side == 'sell' else a) != i:
                    continue
                order = c.destination if side == 'sell' else c.source
                key = order['order_id']
                if key not in grouped:
                    grouped[key] = dict(action=side, order_id=key, type_id=order['type_id'],
                        type_name=c.meta['type_name'], quantity=0, unit_price=order['price'],
                        unit_cash_isk=(c.net if side == 'sell' else c.cost) / 100,
                        volume_per_unit_m3=c.volume, station_id=sid, station=station,
                        minimum_quantity=_minimum(order), deliveries=[])
                grouped[key]['quantity'] += q
                if side == 'buy':
                    dest = nav.stations[c.destination['location_id']]
                    grouped[key]['deliveries'].append(dict(region=c.destination['region'],
                        station=dest['station_name'], quantity=q, expected_profit_isk=c.profit * q / 100))
            for event in sorted(grouped.values(), key=lambda e: (e['type_name'], e['order_id'])):
                sign = 1 if side == 'sell' else -1
                amount = cents(event['unit_cash_isk']) * event['quantity']
                cash += sign * amount
                cargo -= sign * event['volume_per_unit_m3'] * event['quantity']
                event.update(total_cash_isk=amount / 100, cash_after_isk=cash / 100,
                             cargo_after_m3=max(0, cargo), flight_systems=path if not events else [],
                             jumps=leg_jumps if not events else 0)
                events.append(event)
        onboard = [dict(type_name=c.meta['type_name'], quantity=q, volume_m3=c.volume*q,
                        sell_region=c.destination['region'],
                        sell_station=nav.stations[c.destination['location_id']]['station_name'])
                   for c, q, a, b in selected if a == i]
        region = next((c.source['region'] if a == i else c.destination['region']
                       for c, _, a, b in selected if a == i or b == i), '')
        steps.append(dict(index=i, region=region, station=station, events=events, onboard=onboard,
                          cash_after_isk=cash/100, cargo_after_m3=max(0, cargo)))
    return steps, jumps


def optimize_short_route(candidates, settings, nav):
    """Find a profitable chain of at most six visits, starting at any station."""
    began = time.perf_counter()
    deadline = began + settings['solver_time_limit_seconds']
    pairs = defaultdict(list)
    for c in candidates:
        a, b = c.source['location_id'], c.destination['location_id']
        if a != b and c.profit > 0:
            pairs[(a, b)].append(c)
    outbound = defaultdict(list)
    for a, b in pairs:
        outbound[a].append(b)
    empty = _Chain((), [], {}, cents(settings['budget_isk']), 0)
    best, beam, compared = empty, [], 0
    jump_cache, leg_cache = {}, {}
    def leg_jumps(a, b):
        key = (a, b)
        if key not in jump_cache:
            path = nav.path(nav.stations[a]['system_id'], nav.stations[b]['system_id'])
            jump_cache[key] = max(0, len(path) - 1)
        return jump_cache[key]
    def bound(pair, state):
        # Loose optimistic score used only to prune this heuristic's branches.
        available = max(0, state.cash - cents(settings['cash_reserve_isk']))
        potential = defaultdict(int)
        for c in pairs[pair]:
            qty = min(c.max_units, _remaining(c.source, 'purchase', state.used),
                      _remaining(c.destination, 'sale', state.used),
                      available // c.cost, math.floor(settings['cargo_m3'] / c.volume))
            if (_remaining(c.source, 'purchase', state.used) >= _minimum(c.source) and
                    _remaining(c.destination, 'sale', state.used) >= _minimum(c.destination)):
                potential[c.source['type_id']] += c.profit * qty
        return sum(sorted(potential.values(), reverse=True)[:MAX_PURCHASE_TYPES])
    def rank(state):
        profit = state.cash - empty.cash
        return (-profit / max(1, state.jumps), -profit, state.jumps,
                len(state.stations), state.stations)
    def optimistic_score(pair, state):
        try:
            jumps = state.jumps + leg_jumps(*pair)
        except ValueError:
            return -math.inf
        return (state.cash - empty.cash + bound(pair, state)) / max(1, jumps)
    def extend(state, pair):
        nonlocal compared
        a, b = pair
        relevant = {(side, order['order_id']) for c in pairs[pair]
                    for side, order in [('purchase', c.source), ('sale', c.destination)]}
        key = (pair, state.cash, tuple(sorted((key, state.used[key]) for key in relevant if key in state.used)))
        if key not in leg_cache:
            remaining = deadline - time.perf_counter()
            leg_cache[key] = select_short_leg(pairs[pair], settings, state.cash, state.used,
                                              time_limit=max(0, min(1.0, remaining)))
            compared += 1
        chosen = leg_cache[key]
        if not chosen:
            return None
        try:
            distance = leg_jumps(a, b)
        except ValueError:
            return None
        route = state.stations + (b,) if state.stations else (a, b)
        step = len(route) - 2
        used = dict(state.used)
        for c, q in chosen:
            for side, order in [('purchase', c.source), ('sale', c.destination)]:
                order_key = (side, order['order_id'])
                used[order_key] = used.get(order_key, 0) + q
        return _Chain(route, state.selected + [(c, q, step, step+1) for c, q in chosen], used,
                      state.cash + sum(c.profit*q for c, q in chosen), state.jumps + distance)
    seeds = sorted(pairs, key=lambda pair: (-optimistic_score(pair, empty), pair))[:SEED_LIMIT]
    for pair in seeds:
        state = extend(empty, pair)
        if state:
            beam.append(state)
            if rank(state) < rank(best):
                best = state
    beam = sorted(beam, key=rank)[:BEAM_WIDTH]
    for _ in range(MAX_STATIONS - 2):
        next_beam = []
        for state in beam:
            options = [(state.stations[-1], b) for b in outbound[state.stations[-1]]]
            options.sort(key=lambda pair: (-optimistic_score(pair, state), pair))
            for pair in options[:BRANCH_WIDTH]:
                child = extend(state, pair)
                if child:
                    next_beam.append(child)
                    if rank(child) < rank(best):
                        best = child
        beam = sorted(next_beam, key=rank)[:BEAM_WIDTH]
        if not beam:
            break
    validate_short_selection(best.selected, settings, best.stations)
    steps, jumps = _itinerary(best.selected, best.stations, settings, nav)
    profit = (best.cash - cents(settings['budget_isk'])) / 100
    print(f'Short route: {len(best.stations)} station visits, {jumps} jumps, '
          f'{profit:,.2f} ISK estimated profit', flush=True)
    return dict(route=[nav.stations[sid]['station_name'] for sid in best.stations],
                station_ids=list(best.stations), station_count=len(best.stations),
                profit_isk=profit, final_budget_isk=best.cash/100, trade_count=len(best.selected),
                purchase_count=sum(e['action'] == 'buy' for step in steps for e in step['events']),
                steps=steps, total_jumps=jumps, max_stations=MAX_STATIONS,
                max_purchase_types=MAX_PURCHASE_TYPES, objective='short_station_chain',
                selection_metric='profit_per_jump',
                profit_per_jump_isk=profit / jumps if jumps else None,
                optimality_proven=False, relative_gap=None, alternatives=[],
                compared_legs=compared, elapsed_seconds=time.perf_counter()-began,
                trades=[dict(**c.row(settings, nav, q), buy_step=a, sell_step=b)
                        for c, q, a, b in best.selected])
