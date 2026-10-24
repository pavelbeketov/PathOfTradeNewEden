import contextlib
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import Calculate_profit as calc
import Make_report as report
import Orders_in_a_region as download
import Run_trade as runner
import esi_client
import trader_common as common
from route_optimizer import direction, solve_route, validate_selection


def settings(**overrides):
    return {**common.load_settings(), 'budget_isk':100, 'cargo_m3':10,
            'sales_tax_pct':0, 'min_profit_pct':0, 'solver_time_limit_seconds':3,
            'solver_relative_gap':0, **overrides}


def candidate(oid, buy_region, sell_region, cost, net, qty=10, volume=1,
              source_id=None, dest_id=None, minimum=1, tid=None):
    tid = tid or oid
    a = dict(type_id=tid,order_id=source_id or oid*2,region=buy_region,price=cost,
             volume_remain=qty,min_volume=1,location_id=1)
    b = dict(type_id=tid,order_id=dest_id or oid*2+1,region=sell_region,price=net,
             volume_remain=qty,min_volume=minimum,location_id=2)
    return calc.Candidate(a,b,dict(type_name=str(tid),volume_m3=volume),calc.cents(cost),calc.cents(net),qty)


def optimize(cs,s=None,route=None):
    s = s or settings()
    return solve_route(cs,s,route or s['route'])


class OptimizerTests(unittest.TestCase):
    def test_reinvest_and_carry_cargo(self):
        s = settings(budget_isk=40,cargo_m3=3)
        cs = [candidate(1,'The_Forge','Sinq_Laison',40,80,qty=1),
              candidate(2,'Sinq_Laison','Domain',80,120,qty=1)]
        chosen,result = optimize(cs,s)
        self.assertEqual(result['profit_isk'],80)
        self.assertEqual(sum(q for _,q,_,_ in chosen),2)
        self.assertTrue(validate_selection(chosen,s,s['route']))

    def test_cargo_applies_across_multiple_legs(self):
        s = settings(budget_isk=1000,cargo_m3=1)
        cs = [candidate(1,'The_Forge','Domain',1,100,qty=1),
              candidate(2,'Sinq_Laison','Metropolis',1,10,qty=1)]
        chosen,result = optimize(cs,s)
        self.assertEqual(result['profit_isk'],99)
        self.assertEqual(len(chosen),1)

    def test_shared_source_not_spent_twice(self):
        cs = [candidate(1,'The_Forge','Domain',10,30,qty=3,source_id=50,tid=1),
              candidate(2,'The_Forge','Heimatar',10,20,qty=3,source_id=50,tid=1)]
        chosen,_ = optimize(cs)
        self.assertLessEqual(sum(q for _,q,_,_ in chosen),3)

    def test_shared_destination_not_sold_twice(self):
        cs = [candidate(1,'The_Forge','Domain',10,30,qty=3,dest_id=99,tid=1),
              candidate(2,'Sinq_Laison','Domain',11,30,qty=3,dest_id=99,tid=1)]
        chosen,_ = optimize(cs)
        self.assertLessEqual(sum(q for _,q,_,_ in chosen),3)

    def test_minimum_volume_blocks_unfillable_sale(self):
        c = candidate(1,'The_Forge','Domain',10,30,qty=10,minimum=5)
        chosen,result = optimize([c],settings(cargo_m3=4))
        self.assertEqual(chosen,[])
        self.assertEqual(result['profit_isk'],0)

    def test_minimum_volume_can_pool_multiple_sources(self):
        a = candidate(1,'The_Forge','Domain',10,20,qty=2,dest_id=99,minimum=5,tid=1)
        b = candidate(2,'Sinq_Laison','Domain',11,20,qty=3,dest_id=99,minimum=5,tid=1)
        a.destination.update(volume_remain=5)
        b.destination.update(volume_remain=5)
        chosen,_ = optimize([a,b])
        self.assertEqual(sum(q for _,q,_,_ in chosen),5)

    def test_reserve_never_spent(self):
        chosen,_ = optimize([candidate(1,'The_Forge','Domain',10,30)],settings(cash_reserve_isk=90))
        self.assertEqual(sum(q for _,q,_,_ in chosen),1)

    def test_no_backward_wrap_and_finish_in_forge(self):
        s = settings()
        self.assertIsNone(direction(candidate(1,'Domain','Sinq_Laison',1,2),s['route']))
        self.assertEqual(direction(candidate(2,'Domain','The_Forge',1,2),s['route']),(4,5))

    def test_equal_profit_does_not_crash(self):
        chosen,result = optimize([candidate(1,'The_Forge','Domain',10,20,qty=1),candidate(2,'The_Forge','Domain',20,30,qty=1)])
        self.assertEqual(result['profit_isk'],20)

    def test_validator_rejects_overspending(self):
        c = candidate(1,'The_Forge','Domain',101,200,qty=1)
        with self.assertRaises(ValueError):
            validate_selection([(c,1,0,4)],settings(),settings()['route'])

    def test_integer_knapsack_not_greedy_ratio(self):
        s = settings(budget_isk=10,cargo_m3=10)
        a = candidate(1,'The_Forge','Domain',6,14,qty=1)
        b = candidate(2,'The_Forge','Domain',5,11,qty=2)
        chosen,result = optimize([a,b],s)
        self.assertEqual(result['profit_isk'],12)
        self.assertEqual(chosen[0][0].source['type_id'],2)


class AuditRegressionTests(unittest.TestCase):
    def build(self,books,metadata=None,cargo=10):
        s = settings(cargo_m3=cargo)
        nav = Mock();nav.permitted_station.return_value=True;nav.contraband=set()
        dumps = {}
        for region,orders in books.items():
            for i,o in enumerate(orders):
                o.update(issued=common.iso_now(),duration=90,location_id=1,order_id=i+len(dumps)*100,
                         min_volume=o.get('min_volume',1),volume_remain=o.get('volume_remain',10))
            dumps[region]={'orders':orders}
        metadata = metadata if metadata is not None else {1:dict(type_name='item',volume_m3=1)}
        with patch.object(calc,'type_info',return_value=(metadata,[],[])),contextlib.redirect_stdout(io.StringIO()):
            return calc.build_candidates(dumps,s,nav)[0]

    def test_same_region_best_does_not_hide_other_region(self):
        cs = self.build({'The_Forge':[dict(type_id=1,price=10,is_buy_order=False),dict(type_id=1,price=30,is_buy_order=True)],
                         'Domain':[dict(type_id=1,price=20,is_buy_order=True)]})
        self.assertTrue(any(c.source['region']=='The_Forge' and c.destination['region']=='Domain' for c in cs))

    def test_deeper_book_levels_retained_when_needed(self):
        cs = self.build({'The_Forge':[dict(type_id=1,price=10,is_buy_order=False,volume_remain=1),dict(type_id=1,price=11,is_buy_order=False,volume_remain=10)],
                         'Domain':[dict(type_id=1,price=20,is_buy_order=True)]})
        self.assertEqual({c.source['price'] for c in cs},{10,11})

    def test_buy_order_minimum_above_remaining_is_unfillable(self):
        cs = self.build({'The_Forge':[dict(type_id=1,price=10,is_buy_order=False,volume_remain=5)],
                         'Domain':[dict(type_id=1,price=20,is_buy_order=True,volume_remain=2,min_volume=3)]})
        self.assertEqual(cs,[])

    def test_sell_order_minimum_field_does_not_block_partial_buy(self):
        cs = self.build({'The_Forge':[dict(type_id=1,price=10,is_buy_order=False,volume_remain=5,min_volume=5)],
                         'Domain':[dict(type_id=1,price=20,is_buy_order=True,volume_remain=1,min_volume=1)]})
        self.assertEqual(len(cs),1)
        self.assertEqual(cs[0].source['min_volume'],1)

    def test_unknown_or_oversize_volume_excluded(self):
        books={'The_Forge':[dict(type_id=1,price=10,is_buy_order=False)],'Domain':[dict(type_id=1,price=20,is_buy_order=True)]}
        self.assertEqual(self.build(books,metadata={}),[])
        self.assertEqual(self.build(books,metadata={1:dict(type_name='large',volume_m3=20)}),[])

    def test_html_script_terminator_escaped(self):
        payload='</script><script>alert(1)</script>'
        html=report.build_html(report.normalize([dict(type_id=1,type_name=payload)]))
        self.assertNotIn(payload,html)
        self.assertIn('\\u003c/script',html)

    def test_atomic_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.json';p.write_text('old')
            with patch.object(common.os,'replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):common.atomic_json(p,{'new':True})
            self.assertEqual(p.read_text(),'old')
            self.assertEqual(len(list(Path(temp).iterdir())),1)

    def test_settings_reject_negative_and_nan(self):
        for key,value in [('budget_isk',-1),('cargo_m3',float('nan')),('sales_tax_pct',100),('download_workers',True)]:
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temp:
                p=Path(temp)/'s.json';p.write_text(json.dumps(settings(**{key:value})))
                with self.assertRaises(ValueError):common.load_settings(p)

    def test_snapshot_failure_rejected(self):
        with patch.object(common,'read_json',return_value={'success':False}),patch.object(Path,'exists',return_value=True):
            with self.assertRaises(ValueError):common.load_snapshot(settings())

    def test_429_retry_after_shared_block(self):
        client=esi_client.ESIClient(settings())
        retry=Mock(status_code=429,headers={'Retry-After':'9'})
        ok=Mock(status_code=200,headers={})
        session=Mock();session.get.side_effect=[retry,ok]
        with patch.object(client,'session',return_value=session),patch.object(client.limiter,'acquire'),patch.object(client.limiter,'block') as block:
            self.assertIs(client.get('status/'),ok)
            block.assert_called_once_with(9)

    def test_no_uniform_token_refill_or_short_wait_escape(self):
        clock=[0.0]
        limiter=esi_client.RateLimiter()
        limiter.groups['x']={'total':150,'remaining':0,'window':900,'events':esi_client.deque([(900,150)])}
        def sleep(s):clock[0]+=s
        with patch.object(esi_client.time,'monotonic',side_effect=lambda:clock[0]),patch.object(esi_client.time,'sleep',side_effect=sleep):
            limiter.acquire('x')
        self.assertGreaterEqual(clock[0],900)

    def test_unexpected_304_without_body_is_error(self):
        client=Mock();client.get.return_value=Mock(status_code=304)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(ValueError):download.fetch_region(client,'The_Forge',pool,None)

    def test_scan_failure_remains_failed(self):
        saved=[]
        with patch.object(download,'load_settings',return_value=settings()),patch.object(download,'read_json',side_effect=FileNotFoundError),patch.object(download,'atomic_json',side_effect=lambda p,v:saved.append(json.loads(json.dumps(v)))),patch.object(download,'fetch_region',side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):download.main()
        self.assertFalse(saved[-1]['success'])
        self.assertIn('error',saved[-1])

    def test_launcher_runs_all_stages_in_order(self):
        with patch.object(runner.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),0)
        self.assertEqual([Path(call.args[0][-1]).name for call in run.call_args_list],
                         ['Orders_in_a_region.py','Calculate_profit.py','Make_report.py'])
        self.assertTrue(all(call.kwargs['cwd']==runner.ROOT for call in run.call_args_list))

    def test_launcher_stops_when_a_stage_fails(self):
        with patch.object(runner.subprocess,'run',side_effect=[SimpleNamespace(returncode=1)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(),1)

    def test_windows_launcher_has_crlf_line_endings(self):
        content=(common.ROOT/'Run_trade.cmd').read_bytes()
        self.assertNotIn(b'\n',content.replace(b'\r\n',b''))
        self.assertTrue(content.startswith(b'@echo off\r\n'))


if __name__=='__main__':
    unittest.main()
