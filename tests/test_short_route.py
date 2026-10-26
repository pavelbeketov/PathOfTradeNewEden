"""Short station chains must remain executable with a shared wallet and orders."""
import contextlib
import io
import re
import unittest

import Calculate_profit as calc
import Make_report as report
import trader_common as common


def settings(**overrides):
    return {**common.load_settings(), 'budget_isk': 100, 'cargo_m3': 10,
            'cash_reserve_isk': 0, 'sales_tax_pct': 0, 'buy_fee_pct': 0,
            'sell_fee_pct': 0, 'min_profit_pct': 0,
            'solver_time_limit_seconds': 3, 'solver_relative_gap': 0, **overrides}


class Navigation:
    """A tiny connected gate graph, with no database or external requests."""
    def __init__(self):
        regions = list(common.REGIONS)
        self.stations = {}
        self.systems = {}
        for sid in range(1, 10):
            region = regions[(sid - 1) % len(regions)]
            system = dict(system_id=100 + sid, system_name=f'System {sid}',
                          security=1.0, region_id=common.REGIONS[region])
            self.systems[100 + sid] = system
            self.stations[sid] = dict(station_id=sid, station_name=f'Station {sid}', **system)
        self.start_system = 101

    def distances(self, start):
        return {sid: int(sid != start) for sid in self.systems}

    def path(self, start, end):
        return [self.systems[start]] if start == end else [self.systems[start], self.systems[end]]


def candidate(oid, source, destination, cost=10, net=20, qty=1, volume=1,
              source_id=None, destination_id=None, minimum=1, tid=None,
              destination_volume=None):
    tid = tid or oid
    regions = list(common.REGIONS)
    ask = dict(type_id=tid, order_id=source_id or oid * 2,
               region=regions[(source - 1) % len(regions)], price=cost,
               volume_remain=qty, min_volume=1, location_id=source)
    bid = dict(type_id=tid, order_id=destination_id or oid * 2 + 1,
               region=regions[(destination - 1) % len(regions)], price=net,
               volume_remain=destination_volume or qty,
               min_volume=minimum, location_id=destination)
    return calc.Candidate(ask, bid, dict(type_name=f'Item {tid}', volume_m3=volume),
                          calc.cents(cost), calc.cents(net), qty)


class VariableFlightNavigation(Navigation):
    def __init__(self, jumps):
        super().__init__()
        self.jumps = jumps

    def path(self, start, end):
        jumps = self.jumps.get((start, end), 1)
        if start == end:
            return [self.systems[start]]
        transit = [dict(system_id=1000 + i, system_name=f'Transit {i}',
                        security=1.0, region_id=self.systems[start]['region_id'])
                   for i in range(jumps - 1)]
        return [self.systems[start], *transit, self.systems[end]]


def optimize(candidates, config=None, nav=None):
    from short_route_optimizer import optimize_short_route
    with contextlib.redirect_stdout(io.StringIO()):
        return optimize_short_route(candidates, config or settings(), nav or Navigation())


def leg(candidates, config=None, cash=None, used=None):
    from short_route_optimizer import select_short_leg
    config = config or settings()
    return select_short_leg(candidates, config, cash if cash is not None else calc.cents(config['budget_isk']),
                            used or {}, 2)


class ShortRouteTests(unittest.TestCase):
    def test_prefers_higher_profit_per_jump_over_higher_total_profit(self):
        nav = VariableFlightNavigation({(101, 102): 10, (103, 104): 1})
        plan = optimize([candidate(1, 1, 2, cost=1, net=101),
                         candidate(2, 3, 4, cost=1, net=41)], nav=nav)
        self.assertEqual(plan['station_ids'], [3, 4])
        self.assertEqual(plan['profit_isk'], 40)
        self.assertEqual(plan['total_jumps'], 1)

    def test_stops_before_a_leg_that_reduces_overall_profit_per_jump(self):
        nav = VariableFlightNavigation({(101, 102): 1, (102, 103): 10})
        plan = optimize([candidate(1, 1, 2, cost=1, net=41),
                         candidate(2, 2, 3, cost=1, net=11)], nav=nav)
        self.assertEqual(plan['station_ids'], [1, 2])
        self.assertEqual(plan['profit_isk'], 40)
        self.assertEqual(plan['total_jumps'], 1)

    def test_equal_profit_per_jump_prefers_more_total_profit(self):
        nav = VariableFlightNavigation({(101, 102): 1, (103, 104): 2})
        plan = optimize([candidate(1, 1, 2, cost=1, net=21),
                         candidate(2, 3, 4, cost=1, net=41)], nav=nav)
        self.assertEqual(plan['station_ids'], [3, 4])
        self.assertEqual(plan['profit_isk'], 40)
        self.assertEqual(plan['total_jumps'], 2)

    def test_free_start_and_reinvest_on_the_next_station(self):
        config = settings(budget_isk=40, cargo_m3=1)
        plan = optimize([candidate(1, 2, 3, cost=40, net=80),
                         candidate(2, 3, 4, cost=80, net=120)], config)
        self.assertEqual(plan['station_ids'], [2, 3, 4])
        self.assertEqual(plan['profit_isk'], 80)
        self.assertEqual(plan['final_budget_isk'], 120)
        self.assertEqual(plan['steps'][-1]['cargo_after_m3'], 0)
        self.assertEqual([event['action'] for event in plan['steps'][1]['events']], ['sell', 'buy'])
        self.assertEqual(plan['total_jumps'], 2)

    def test_station_visits_are_capped_at_six(self):
        plan = optimize([candidate(i, i, i + 1, cost=1, net=2) for i in range(1, 9)],
                        settings(cargo_m3=1))
        self.assertLessEqual(len(plan['station_ids']), 6)
        self.assertEqual(plan['station_count'], len(plan['station_ids']))
        self.assertEqual(plan['profit_isk'], 5)
        self.assertEqual(plan['steps'][-1]['cargo_after_m3'], 0)

    def test_no_forced_sixth_stop_or_return(self):
        plan = optimize([candidate(1, 4, 5)])
        self.assertEqual(plan['station_ids'], [4, 5])
        self.assertEqual(plan['station_count'], 2)
        self.assertEqual(plan['profit_isk'], 10)

    def test_at_most_three_item_types_bought_at_a_station(self):
        chosen = leg([candidate(i, 1, 2, cost=1, net=10) for i in range(1, 5)])
        self.assertEqual(len({c.source['type_id'] for c, qty in chosen if qty}), 3)
        self.assertEqual(sum(c.profit * qty for c, qty in chosen), calc.cents(27))

    def test_multiple_order_levels_count_as_one_item_type(self):
        chosen = leg([candidate(i, 1, 2, cost=1, net=10, tid=99) for i in range(1, 5)])
        self.assertEqual(sum(qty for _, qty in chosen), 4)
        self.assertEqual({c.source['type_id'] for c, _ in chosen}, {99})

    def test_shared_ask_and_bid_liquidity_is_not_reused(self):
        ask_pairs = [candidate(1, 1, 2, qty=3, source_id=50, tid=99),
                     candidate(2, 1, 2, net=18, qty=3, source_id=50, tid=99)]
        self.assertEqual(sum(qty for _, qty in leg(ask_pairs)), 3)
        bid_pairs = [candidate(1, 1, 2, qty=3, destination_id=99, tid=99),
                     candidate(2, 1, 2, cost=11, qty=3, destination_id=99, tid=99)]
        self.assertEqual(sum(qty for _, qty in leg(bid_pairs)), 3)

    def test_used_liquidity_remains_used_on_a_later_leg(self):
        pair = candidate(1, 1, 2, qty=5)
        chosen = leg([pair], used={('purchase', pair.source['order_id']): 4,
                                  ('sale', pair.destination['order_id']): 3})
        self.assertEqual(sum(qty for _, qty in chosen), 1)

    def test_minimum_buyer_volume_can_pool_source_orders_at_one_visit(self):
        pairs = [candidate(1, 1, 2, qty=2, minimum=5, tid=99,
                           destination_id=99, destination_volume=5),
                 candidate(2, 1, 2, qty=3, minimum=5, tid=99,
                           destination_id=99, destination_volume=5)]
        self.assertEqual(sum(qty for _, qty in leg(pairs)), 5)

    def test_minimum_buyer_volume_blocks_underfilled_leg(self):
        pair = candidate(1, 1, 2, qty=5, minimum=5)
        self.assertEqual(leg([pair], settings(cargo_m3=4)), [])

    def test_residual_buyer_liquidity_below_original_minimum_is_unfillable(self):
        pair = candidate(1, 1, 2, qty=6, minimum=4)
        self.assertEqual(leg([pair], used={('sale', pair.destination['order_id']): 4}), [])

    def test_reserved_cash_and_cargo_limit_every_purchase_batch(self):
        pair = candidate(1, 1, 2, cost=10, net=20, qty=100, volume=2)
        chosen = leg([pair], settings(budget_isk=100, cash_reserve_isk=70, cargo_m3=5))
        self.assertEqual(sum(qty for _, qty in chosen), 2)
        chosen = leg([pair], settings(budget_isk=100, cash_reserve_isk=90, cargo_m3=100))
        self.assertEqual(sum(qty for _, qty in chosen), 1)

    def test_cent_precision_does_not_overspend_the_wallet(self):
        chosen = leg([candidate(1, 1, 2, cost=3.34, net=4, qty=100)],
                     settings(budget_isk=10.01, cargo_m3=100))
        self.assertEqual(sum(qty for _, qty in chosen), 2)

    def test_revisited_stations_do_not_refill_snapshot_orders(self):
        config = settings(budget_isk=100, cargo_m3=1)
        plan = optimize([candidate(1, 1, 2, qty=1),
                         candidate(2, 2, 1, qty=1)], config)
        self.assertEqual(plan['profit_isk'], 20)
        self.assertEqual(len(plan['station_ids']), 3)
        self.assertEqual(plan['steps'][-1]['cargo_after_m3'], 0)

    def test_no_eligible_trade_produces_an_empty_plan(self):
        plan = optimize([], settings())
        self.assertEqual(plan['station_ids'], [])
        self.assertEqual(plan['station_count'], 0)
        self.assertEqual(plan['profit_isk'], 0)
        self.assertEqual(plan['final_budget_isk'], 100)

    def test_execution_uses_the_fee_adjusted_money_on_candidates(self):
        config = settings(budget_isk=100, sales_tax_pct=7.5,
                          buy_fee_pct=2, sell_fee_pct=1)
        pair = candidate(1, 1, 2, cost=10, net=20, qty=1)
        pair.cost = calc.order_money(pair.source['price'], config, True)
        pair.net = calc.order_money(pair.destination['price'], config, False)
        plan = optimize([pair], config)
        self.assertEqual(plan['profit_isk'], 8.1)
        self.assertEqual(plan['final_budget_isk'], 108.1)
        self.assertEqual(plan['steps'][0]['cash_after_isk'], 89.8)

    def test_validator_enforces_buyer_minimum_at_each_visit(self):
        from short_route_optimizer import validate_short_selection
        a = candidate(1, 1, 2, qty=3, minimum=5, tid=99,
                      destination_id=99, destination_volume=6)
        b = candidate(2, 2, 3)
        c = candidate(3, 3, 2, qty=3, minimum=5, tid=99,
                      destination_id=99, destination_volume=6)
        with self.assertRaises(ValueError):
            validate_short_selection([(a, 3, 0, 1), (b, 1, 1, 2), (c, 3, 2, 3)],
                                     settings(cargo_m3=10), [1, 2, 3, 2])

    def test_validator_rejects_four_bought_types(self):
        from short_route_optimizer import validate_short_selection
        with self.assertRaises(ValueError):
            validate_short_selection([(candidate(i, 1, 2), 1, 0, 1) for i in range(1, 5)],
                                     settings(), [1, 2])

    def test_validator_rejects_inventory_carried_past_the_next_stop(self):
        from short_route_optimizer import validate_short_selection
        with self.assertRaises(ValueError):
            validate_short_selection([(candidate(1, 1, 3), 1, 0, 2)],
                                     settings(), [1, 2, 3])


class ShortReportTests(unittest.TestCase):
    def analysis(self, payload=None, short_plan=None):
        pair = candidate(1, 1, 2)
        if payload:
            pair.meta['type_name'] = payload
        if short_plan is None:
            short_plan = optimize([pair])
        if payload:
            short_plan['route'][0] = payload
            short_plan['steps'][0]['station']['station_name'] = payload
        return dict(settings=settings(), snapshot_id='test-snapshot',
                    settings_hash=common.settings_fingerprint(settings()),
                    snapshot_times={'The_Forge': {'checked_at_utc': common.iso_now()}},
                    skipped={}, short_plan=short_plan,
                    plan={**short_plan, 'route': ['FULL LOOP MARKER']})

    def test_empty_plan_renders_without_a_profit_per_jump_value(self):
        plan = optimize([])
        self.assertEqual(plan['total_jumps'], 0)
        self.assertIsNone(plan['profit_per_jump_isk'])
        html = report.build_html([], self.analysis(short_plan=plan), short_mode=True)
        self.assertIn('Прибыль на прыжок<b>—</b>', html)
        self.assertIn('Прибыльная короткая цепочка под заданные настройки не найдена.', html)

    def test_same_system_sales_render_without_dividing_by_zero(self):
        nav = Navigation()
        nav.stations[6].update(system_id=101, system_name='System 1')
        plan = optimize([candidate(1, 1, 6)], nav=nav)
        self.assertEqual(plan['station_ids'], [1, 6])
        self.assertEqual(plan['total_jumps'], 0)
        self.assertGreater(plan['profit_isk'], 0)
        self.assertIsNone(plan['profit_per_jump_isk'])
        html = report.build_html([], self.analysis(short_plan=plan), short_mode=True)
        self.assertIn('Прибыль на прыжок<b>без прыжков</b>', html)
        self.assertIn('Station 6', html)

    def test_short_page_has_its_own_route_and_links_between_reports(self):
        analysis = self.analysis()
        full_html = report.build_html([], analysis)
        short_html = report.build_html([], analysis, short_mode=True)
        self.assertIn('href="short_route_report.html"', full_html)
        self.assertIn('href="arbitrage_report.html"', short_html)
        self.assertIn('FULL LOOP MARKER', full_html)
        self.assertNotIn('FULL LOOP MARKER', short_html)
        self.assertIn('id="tblBulk"', full_html)
        self.assertNotIn('id="tblBulk"', short_html)
        self.assertNotIn('id="tblSingle"', short_html)
        self.assertIn('Короткий маршрут продаж', short_html)
        self.assertIn('Прибыль на прыжок', short_html)
        self.assertIn('оптимальность не гарантируется', short_html)
        self.assertIn('Run_trade.py', short_html)

    def test_short_page_escapes_station_and_item_names(self):
        payload = '</script><script>injected()</script>'
        html = report.build_html([], self.analysis(payload), short_mode=True)
        self.assertNotIn(payload, html)
        self.assertIn('&lt;/script&gt;&lt;script&gt;injected()', html)

    def test_short_and_full_checkboxes_have_independent_storage_keys(self):
        analysis = self.analysis()
        full_keys = set(re.findall(r'data-key="([^"]+)"', report.build_html([], analysis)))
        short_keys = set(re.findall(r'data-key="([^"]+)"',
                                    report.build_html([], analysis, short_mode=True)))
        self.assertTrue(full_keys)
        self.assertTrue(short_keys)
        self.assertTrue(full_keys.isdisjoint(short_keys))
        analysis['short_plan']['profit_isk'] += 1
        recalculated_keys = set(re.findall(r'data-key="([^"]+)"',
                                           report.build_html([], analysis, short_mode=True)))
        self.assertTrue(short_keys.isdisjoint(recalculated_keys))


if __name__ == '__main__':
    unittest.main()
