"""Candidate retention for short routes must preserve actual station choices."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import Calculate_profit as calc
import trader_common as common


class ShortCandidateTests(unittest.TestCase):
    def build(self, books, *, include_short=True, cargo=1):
        settings = {**common.load_settings(), 'budget_isk': 100,
                    'cash_reserve_isk': 0, 'cargo_m3': cargo,
                    'sales_tax_pct': 0, 'buy_fee_pct': 0,
                    'sell_fee_pct': 0, 'min_profit_pct': 0,
                    'min_profit_per_unit_isk': 0}
        dumps, order_id = {}, 0
        for region, orders in books.items():
            prepared = []
            for order in orders:
                order_id += 1
                prepared.append(dict(type_id=1, order_id=order_id,
                                     volume_remain=order.get('volume_remain', 1),
                                     min_volume=1, issued=common.iso_now(), duration=90,
                                     **{k: v for k, v in order.items() if k != 'volume_remain'}))
            dumps[region] = {'orders': prepared}
        nav = Mock()
        nav.permitted_station.return_value = True
        nav.contraband = set()
        metadata = {1: dict(type_name='Item', volume_m3=1)}
        with patch.object(calc, 'type_info', return_value=(metadata, [], [])), \
                contextlib.redirect_stdout(io.StringIO()):
            return calc.build_candidates(dumps, settings, nav, include_short=include_short)

    def test_default_return_contract_and_region_pruning_are_unchanged(self):
        result = self.build({
            'The_Forge': [dict(price=10, is_buy_order=False, location_id=11),
                          dict(price=11, is_buy_order=False, location_id=12)],
            'Domain': [dict(price=20, is_buy_order=True, location_id=21)],
        }, include_short=False)
        self.assertEqual(len(result), 2)
        self.assertEqual({c.source['location_id'] for c in result[0]}, {11})

    def test_short_candidates_keep_alternative_station_in_same_region(self):
        full, _, short = self.build({
            'The_Forge': [dict(price=10, is_buy_order=False, location_id=11),
                          dict(price=11, is_buy_order=False, location_id=12)],
            'Domain': [dict(price=20, is_buy_order=True, location_id=21)],
        })
        self.assertEqual({c.source['location_id'] for c in full}, {11})
        self.assertEqual({c.source['location_id'] for c in short}, {11, 12})

    def test_short_candidates_allow_different_stations_in_one_region(self):
        full, _, short = self.build({
            'The_Forge': [dict(price=10, is_buy_order=False, location_id=11),
                          dict(price=20, is_buy_order=True, location_id=12)],
        })
        self.assertEqual(full, [])
        self.assertEqual(len(short), 1)
        self.assertEqual((short[0].source['location_id'], short[0].destination['location_id']),
                         (11, 12))

    def test_short_candidates_exclude_buy_and_sell_at_same_station(self):
        _, _, short = self.build({
            'The_Forge': [dict(price=10, is_buy_order=False, location_id=11),
                          dict(price=20, is_buy_order=True, location_id=11)],
        })
        self.assertEqual(short, [])

    def test_short_book_retains_deeper_liquidity_for_revisits(self):
        full, _, short = self.build({
            'The_Forge': [dict(price=10 + i, is_buy_order=False, location_id=11)
                          for i in range(6)],
            'Domain': [dict(price=20, is_buy_order=True, location_id=21, volume_remain=6)],
        })
        self.assertEqual({c.source['price'] for c in full}, {10})
        self.assertEqual({c.source['price'] for c in short}, {10, 11, 12, 13, 14})


if __name__ == '__main__':
    unittest.main()
