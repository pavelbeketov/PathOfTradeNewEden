"""Integer route planning: shared order liquidity, cash flow and cargo on every leg."""
import itertools
import math
import time
from collections import defaultdict

from Calculate_profit import Candidate, cents
from trader_common import HUBS

SCALE = 10000  # model currency unit = 100 ISK; execution checks use integer cents


def aggregate_equivalent_orders(candidates):
    """Merge identical price/region liquidity; retain actual stations for execution.

    Profit and the cash/cargo constraints do not depend on station identity.
    Orders with minimum quantities > 1 are deliberately kept separate.
    """
    grouped_orders = {}
    memberships = {}
    group_counter = 0
    def grouped(order, side):
        nonlocal group_counter
        minimum = min(order['min_volume'],order['volume_remain'])
        key = (side,order['type_id'],order['region'],order['price'],order['order_id'] if minimum > 1 else None)
        if key not in grouped_orders:
            group_counter += 1
            grouped_orders[key] = {**order,'order_id':-group_counter,'volume_remain':0,'components':{}}
        g = grouped_orders[key]
        if order['order_id'] not in g['components']:
            g['components'][order['order_id']] = order
            g['volume_remain'] += order['volume_remain']
        memberships[(side,order['order_id'])] = g
        return g
    for c in candidates:
        grouped(c.source,'source');grouped(c.destination,'destination')
    edges = {}
    for c in candidates:
        a,b = memberships[('source',c.source['order_id'])],memberships[('destination',c.destination['order_id'])]
        key = (a['order_id'],b['order_id'])
        if key not in edges:
            edges[key] = Candidate(a,b,c.meta,c.cost,c.net,0)
        edges[key].max_units = max(edges[key].max_units,c.max_units)
    for c in edges.values():
        # Individual pairs may have smaller liquidity than the combined level.
        # Cargo is bounded again for every leg in the model.
        c.max_units = min(c.source['volume_remain'],c.destination['volume_remain'])
    return list(edges.values())


def expand_selection(selected, settings):
    remaining = {}
    expanded = []
    for c,q,a,b in selected:
        sources = list(c.source.get('components',{c.source['order_id']:c.source}).values())
        destinations = list(c.destination.get('components',{c.destination['order_id']:c.destination}).values())
        for side,orders in [('source',sources),('destination',destinations)]:
            for o in orders:
                remaining.setdefault((side,o['order_id']),o['volume_remain'])
        si=di=0
        while q:
            so,bo=sources[si],destinations[di]
            n=min(q,remaining[('source',so['order_id'])],remaining[('destination',bo['order_id'])])
            if n:
                cap=min(so['volume_remain'],bo['volume_remain'],math.floor(settings['cargo_m3']/c.volume))
                expanded.append((Candidate(so,bo,c.meta,c.cost,c.net,cap),n,a,b))
                q-=n
                remaining[('source',so['order_id'])]-=n
                remaining[('destination',bo['order_id'])]-=n
            if not remaining[('source',so['order_id'])]:si+=1
            if not remaining[('destination',bo['order_id'])]:di+=1
    return expanded


def direction(candidate, route):
    start = {r:i for i,r in enumerate(route[:-1])}
    end = {r:i for i,r in enumerate(route[1:], 1)}
    a, b = start[candidate.source['region']], end[candidate.destination['region']]
    return (a,b) if a < b else None


def solve_route(candidates, settings, route, incumbent_isk=None, bound_only=False):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, linprog, milp
    from scipy.sparse import coo_matrix, vstack

    choices = [(c, direction(c, route)) for c in candidates]
    choices = [(c,d) for c,d in choices if d is not None]
    if not choices:
        return [], dict(status='optimal', profit_isk=0, upper_bound_isk=0, gap=0)
    n = len(choices)
    objective = [-c.profit / SCALE for c,_ in choices]
    upper = [min(c.max_units,math.floor(settings['cargo_m3']/c.volume)) for c,_ in choices]
    lower = [0] * n
    rr, cc, vv, lo, hi = [], [], [], [], []

    def constraint(terms, low=-np.inf, high=np.inf):
        row = len(lo)
        for col, value in terms:
            rr.append(row); cc.append(col); vv.append(value)
        lo.append(low); hi.append(high)

    groups = defaultdict(list)
    for j,(c,_) in enumerate(choices):
        groups[('purchase',c.source['order_id'])].append(j)
        groups[('sale',c.destination['order_id'])].append(j)
    for (side,_), indexes in groups.items():
        c = choices[indexes[0]][0]
        order = c.source if side == 'purchase' else c.destination
        cap = min(order['volume_remain'], math.floor(settings['cargo_m3']/c.volume))
        minimum = min(order['min_volume'], order['volume_remain'])
        if minimum > 1:
            z = len(objective)
            objective.append(0); upper.append(1); lower.append(0)
            constraint([(j,1) for j in indexes] + [(z,-cap)], high=0)
            constraint([(j,1) for j in indexes] + [(z,-minimum)], low=0)
        else:
            constraint([(j,1) for j in indexes], high=cap)
    for step in range(len(route)-1):
        constraint([(j,c.volume) for j,(c,(a,b)) in enumerate(choices) if a <= step < b], high=settings['cargo_m3'])
        cash_terms = []
        for j,(c,(a,b)) in enumerate(choices):
            value = (c.cost if a <= step else 0) - (c.net if b <= step else 0)
            if value:
                cash_terms.append((j,value/SCALE))
        constraint(cash_terms, high=(cents(settings['budget_isk'])-cents(settings['cash_reserve_isk']))/SCALE)
    matrix = coo_matrix((vv,(rr,cc)),shape=(len(lo),len(objective))).tocsc()
    objective, lower, upper = np.asarray(objective), np.asarray(lower), np.asarray(upper)
    lo, hi = np.asarray(lo), np.asarray(hi)
    began = time.perf_counter()
    limit = settings['solver_time_limit_seconds']
    finite_hi, finite_lo = np.isfinite(hi), np.isfinite(lo)
    relaxation = linprog(objective, A_ub=vstack([matrix[finite_hi],-matrix[finite_lo]],format='csc'),
                         b_ub=np.concatenate([hi[finite_hi],-lo[finite_lo]]),
                         bounds=np.column_stack([lower,upper]),method='highs',
                         options={'time_limit':limit})
    best, best_value, bound_value = [], 0.0, None
    if relaxation.status == 0:
        bound_value = max(0,-float(relaxation.fun))
        if bound_only:
            return [],dict(status='relaxation',profit_isk=0,upper_bound_isk=bound_value*SCALE/100,gap=None)
        if incumbent_isk is not None and bound_value*SCALE/100 <= incumbent_isk + 1e-6:
            return [],dict(status='dominated',profit_isk=0,
                           upper_bound_isk=bound_value*SCALE/100,gap=None)
    elif bound_only:
        return [],dict(status='relaxation_limit',profit_isk=0,upper_bound_isk=None,gap=None)

    def integer_solve(indexes, seconds):
        nonlocal best, best_value
        if not len(indexes):
            return None
        result = milp(objective[indexes],integrality=np.ones(len(indexes)),
                      bounds=Bounds(lower[indexes],upper[indexes]),
                      constraints=LinearConstraint(matrix[:,indexes],lo,hi),
                      options={'time_limit':max(0.1,seconds),'mip_rel_gap':settings['solver_relative_gap'],'presolve':True})
        if result.status not in (0,1):
            raise RuntimeError(f'Route optimizer failed: {result.message}')
        selected = []
        if result.x is not None:
            for k,j in enumerate(indexes):
                if j >= n:
                    continue
                qty = int(round(result.x[k]))
                if abs(result.x[k]-qty) > 1e-4:
                    raise RuntimeError('Optimizer returned noninteger quantities')
                if qty > 0:
                    c,(a,b)=choices[j]
                    selected.append((c,qty,a,b))
            validate_selection(selected,settings,route)
            value=sum(c.profit*q for c,q,_,_ in selected)/SCALE
            if value > best_value:
                best,best_value=selected,value
        return result

    if relaxation.status == 0:
        # A small integer problem on the LP support provides a feasible lower
        # bound. Its optimal status alone is NOT a certificate for the full set.
        support=np.flatnonzero(relaxation.x > 1e-8)
        integer_solve(support,min(2,limit/3))
        gap=bound_value-best_value
        epsilon=max(1e-6,abs(bound_value)*1e-10)
        if gap > max(epsilon,settings['solver_relative_gap']*best_value):
            # Exact reduced-cost fixing: activating any excluded integer variable
            # would lose more than the entire gap to the known feasible plan.
            discard=(relaxation.x < 1e-8) & (relaxation.lower.marginals > gap+epsilon)
            active=np.flatnonzero(~discard)
            result=integer_solve(active,limit-(time.perf_counter()-began))
            if result is not None:
                bound=getattr(result,'mip_dual_bound',None)
                if bound is not None and math.isfinite(bound):
                    bound_value=min(bound_value,max(best_value,-float(bound)))
    else:
        result=integer_solve(np.arange(len(objective)),limit-(time.perf_counter()-began))
        if result is not None:
            bound=getattr(result,'mip_dual_bound',None)
            if bound is not None and math.isfinite(bound):
                bound_value=max(best_value,-float(bound))
    selected=best
    profit=best_value*SCALE/100
    upper_bound=max(profit,bound_value*SCALE/100) if bound_value is not None else None
    gap = (upper_bound-profit)/max(profit,0.01) if upper_bound is not None else None
    return selected, dict(status='optimal' if gap is not None and gap <= 1e-7 else 'bounded' if gap is not None and gap <= settings['solver_relative_gap']+1e-7 else 'time_limit',
                         profit_isk=profit, upper_bound_isk=upper_bound, gap=gap)


def validate_selection(selected, settings, route):
    """Check the returned plan independently using integer money and real quantities."""
    cash = cents(settings['budget_isk'])
    reserve = cents(settings['cash_reserve_isk'])
    used = defaultdict(int)
    orders = {}
    for c,q,a,b in selected:
        if type(q) is not int or q <= 0 or q > c.max_units or direction(c,route) != (a,b):
            raise ValueError('Invalid selected trade')
        for side,o in [('purchase',c.source),('sale',c.destination)]:
            key = (side,o['order_id'])
            used[key] += q
            orders[key] = o
    for key,q in used.items():
        o = orders[key]
        if not min(o['min_volume'],o['volume_remain']) <= q <= o['volume_remain']:
            raise ValueError('Shared order volume or minimum trade quantity violated')
    cargo = 0.0
    for step in range(len(route)):
        for c,q,a,b in selected:
            if b == step:
                cash += c.net*q
                cargo -= c.volume*q
        if cargo < -1e-6:
            raise ValueError('Selling inventory before purchase')
        for c,q,a,b in selected:
            if a == step:
                cash -= c.cost*q
                cargo += c.volume*q
        if cash < reserve or cargo > settings['cargo_m3'] + 1e-6:
            raise ValueError(f'Cash/cargo constraints violated at {route[step]}')
    if abs(cargo) > 1e-5 or cash != cents(settings['budget_isk']) + sum(c.profit*q for c,q,_,_ in selected):
        raise ValueError('Route does not settle to cash with empty cargo')
    return True


def build_itinerary(selected, settings, nav, route):
    steps = []
    cash = cents(settings['budget_isk'])
    holdings = defaultdict(int)
    cargo = 0.0
    current = nav.start_system
    total_jumps = 0
    for i,region in enumerate(route):
        events = []
        for side in ['sell','buy']:
            grouped = {}
            for c,q,a,b in selected:
                if (side == 'sell' and b != i) or (side == 'buy' and a != i):
                    continue
                order = c.destination if side == 'sell' else c.source
                key = order['order_id']
                if key not in grouped:
                    grouped[key] = dict(action=side, order_id=key, type_id=c.source['type_id'],
                        type_name=c.meta['type_name'], quantity=0, unit_price=order['price'],
                        unit_cash_isk=(c.net if side == 'sell' else c.cost)/100,
                        volume_per_unit_m3=c.volume, station_id=order['location_id'],
                        minimum_quantity=min(order['min_volume'],order['volume_remain']),
                        station=nav.stations[order['location_id']], deliveries=[])
                grouped[key]['quantity'] += q
                if side == 'buy':
                    grouped[key]['deliveries'].append(dict(region=route[b],station=nav.stations[c.destination['location_id']]['station_name'],
                                                         quantity=q, expected_profit_isk=c.profit*q/100))
            # All sales precede purchases. Within a phase, visit nearby stations first.
            remaining = list(grouped.values())
            while remaining:
                dist = nav.distances(current)
                sid = min((e['station_id'] for e in remaining), key=lambda s:(dist[nav.stations[s]['system_id']],s))
                at_station = [e for e in remaining if e['station_id'] == sid]
                remaining = [e for e in remaining if e['station_id'] != sid]
                path = nav.path(current,nav.stations[sid]['system_id'])
                for k,event in enumerate(sorted(at_station,key=lambda e:(e['type_name'],e['order_id']))):
                    event['flight_systems'] = path if k == 0 else []
                    event['jumps'] = len(path)-1 if k == 0 else 0
                    total_jumps += event['jumps']
                    qty = event['quantity']
                    unit_cash = cents(event['unit_cash_isk'])
                    cash += unit_cash*qty*(1 if side == 'sell' else -1)
                    holdings[event['type_id']] += qty*(1 if side == 'buy' else -1)
                    cargo += event['volume_per_unit_m3']*qty*(1 if side == 'buy' else -1)
                    event.update(total_cash_isk=unit_cash*qty/100,cash_after_isk=cash/100,cargo_after_m3=max(0,cargo))
                    events.append(event)
                current = nav.stations[sid]['system_id']
        if not events or i == len(route)-1:
            sid = settings['start_station_id'] if i in (0,len(route)-1) else HUBS[region]
            path = nav.path(current,nav.stations[sid]['system_id'])
            events.append(dict(action='fly',station_id=sid,station=nav.stations[sid],flight_systems=path,
                               jumps=len(path)-1,cash_after_isk=cash/100,cargo_after_m3=max(0,cargo)))
            total_jumps += len(path)-1
            current = nav.stations[sid]['system_id']
        onboard = []
        for c,q,a,b in selected:
            if a <= i < b:
                onboard.append(dict(type_name=c.meta['type_name'],quantity=q,volume_m3=c.volume*q,
                                    sell_region=route[b],sell_station=nav.stations[c.destination['location_id']]['station_name']))
        steps.append(dict(index=i,region=region,events=events,cash_after_isk=cash/100,
                          cargo_after_m3=max(0,cargo),onboard=onboard))
    return steps,total_jumps


def optimize_loop(candidates, settings, nav):
    candidates = aggregate_equivalent_orders(candidates)
    print(f'Optimization price-level pairs: {len(candidates):,}',flush=True)
    original = settings['route']
    routes = [[original[0],*middle,original[0]] for middle in itertools.permutations(original[1:-1])] if settings['optimize_region_order'] else [original]
    best, best_route, best_profit = [], original, -1
    alternatives = []
    ranked=[]
    for i,route in enumerate(routes,1):
        _,result=solve_route(candidates,settings,route,bound_only=True)
        ranked.append((route,result))
        print(f"Bound {i}/{len(routes)}: {result['status']}, upper={result['upper_bound_isk']}",flush=True)
    ranked.sort(key=lambda item:item[1]['upper_bound_isk'] if item[1]['upper_bound_isk'] is not None else math.inf,reverse=True)
    for i,(route,estimate) in enumerate(ranked,1):
        if estimate['upper_bound_isk'] is not None and estimate['upper_bound_isk'] <= best_profit+1e-6:
            selected,result=[],{**estimate,'status':'dominated'}
        else:
            selected, result = solve_route(candidates, settings, route,incumbent_isk=best_profit if best_profit >= 0 else None)
        alternatives.append(dict(route=route,**result))
        print(f"Route {i}/{len(routes)}: {' -> '.join(route)} | {result['profit_isk']:,.2f} ISK | {result['status']}",flush=True)
        if result['profit_isk'] > best_profit:
            best, best_route, best_profit = selected, route, result['profit_isk']
    best = expand_selection(best,settings)
    validate_selection(best,settings,best_route)
    steps,jumps = build_itinerary(best,settings,nav,best_route)
    bounds = [a['upper_bound_isk'] for a in alternatives]
    upper = max(bounds) if all(b is not None for b in bounds) else None
    gap = (upper-best_profit)/max(best_profit,0.01) if upper is not None else None
    return dict(route=best_route,profit_isk=best_profit,final_budget_isk=settings['budget_isk']+best_profit,
                trade_count=len(best),steps=steps,total_jumps=jumps,alternatives=alternatives,
                upper_bound_isk=upper,relative_gap=gap,
                optimality_proven=gap is not None and gap <= 1e-7,
                within_tolerance=gap is not None and gap <= settings['solver_relative_gap']+1e-7,
                objective='max_profit_per_round_trip',
                trades=[dict(**c.row(settings,nav,q),buy_step=a,sell_step=b) for c,q,a,b in best])
