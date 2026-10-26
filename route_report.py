"""Escaped HTML itineraries for the full loop and short station chain."""
import hashlib
import json
from html import escape


def h(value):
    return escape(str(value), quote=True)


def money(value):
    return f"{value:,.2f}"


def _plan_key(analysis, plan, mode):
    identity = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]
    return f"trader:{analysis['snapshot_id']}:{analysis['settings_hash'][:12]}:{mode}:{identity}"


def render_route(analysis):
    p, s = analysis['plan'], analysis['settings']
    oldest = min(t['checked_at_utc'] for t in analysis['snapshot_times'].values())
    gap = p['relative_gap']
    if p['optimality_proven']:
        quality = 'Оптимум по доступным ордерам подтверждён.'
    elif gap is not None:
        quality = f"Лучший найденный план; верхняя оценка улучшения — {gap*100:.3f}%."
    else:
        quality = 'Лучший найденный план; оптимальность не подтверждена.'
    out = [f'<div class="routePath">{h(" → ".join(p["route"]))}</div>',
           '<div class="planSummary">',
           f'<div class="planMetric">Начальный бюджет<b>{money(s["budget_isk"])} ISK</b></div>',
           f'<div class="planMetric">Прибыль после комиссий<b>+{money(p["profit_isk"])} ISK</b></div>',
           f'<div class="planMetric">После круга<b>{money(p["final_budget_isk"])} ISK</b></div>',
           f'<div class="planMetric">Карго<b>{money(s["cargo_m3"])} м³</b></div></div>',
           f'<p class="snapshotNote">Налог с продажи: {s["sales_tax_pct"]}%; дополнительные комиссии покупки / продажи: '
           f'{s["buy_fee_pct"]}% / {s["sell_fee_pct"]}%. Резерв: {money(s["cash_reserve_isk"])} ISK. '
           f'Сравнено порядков: {len(p["alternatives"])}. {h(quality)}</p>',
           f'<p class="snapshotNote">Маршрут содержит {p["total_jumps"]} прыжков по базе звёздных врат; '
           f'security систем ≥ {s["min_system_security"]}. Цель расчёта — прибыль за круг. '
           'Расходы времени и риск потери груза в прибыль не включены.</p>',
           f'<p id="snapshotAge" class="snapshotNote" data-time="{h(oldest)}" data-max="{s["max_snapshot_age_minutes"]}">'
           f'Снимок проверен: {h(oldest)}</p>']
    missing = analysis['skipped'].get('unknown_type_ids', [])
    if missing:
        out.append(f'<p class="snapshotNote stale">Исключено типов без подтверждённых названия и объёма: {len(missing)}. '
                   'Они не участвуют в оценке оптимальности.</p>')
    if not p['trade_count']:
        out.append('<p class="stale">Под заданный бюджет, карго и комиссии прибыльных закупок не найдено. Не покупай товар ради заполнения трюма.</p>')
    out.extend(_render_steps(p, s, _plan_key(analysis, p, 'loop')))
    out.append('<p class="snapshotNote">Круг завершён: груз продан. Укажи фактический новый бюджет в settings.json '
               'и снова выполни Order → Calculate → Make. Отметки действий сохраняются только для этого плана.</p>')
    out.append('<details><summary>Сравнение порядков регионов</summary><div class="tableWrap"><table class="planAlternatives">'
               '<thead><tr><th>Круг</th><th>Прибыль, ISK</th><th>Оставшийся разрыв оценки</th></tr></thead><tbody>')
    for alternative in sorted(p['alternatives'],key=lambda a:a['profit_isk'],reverse=True):
        agap = alternative['gap']
        amount = ('≤ '+money(alternative['upper_bound_isk'])+' (хуже выбранного)') if alternative['status'] == 'dominated' else money(alternative['profit_isk'])
        out.append(f'<tr><td>{h(" → ".join(alternative["route"]))}</td><td>{amount}</td>'
                   f'<td>{f"{agap*100:.3f}%" if agap is not None else "неизвестен"}</td></tr>')
    out.append('</tbody></table></div></details>')
    return '\n'.join(out)


def render_short_route(analysis):
    p, s = analysis['short_plan'], analysis['settings']
    oldest = min(t['checked_at_utc'] for t in analysis['snapshot_times'].values())
    max_stations, max_types = p.get('max_stations', 6), p.get('max_purchase_types', 3)
    if p['total_jumps'] == 0:
        profit_per_jump_text = 'без прыжков' if p['trade_count'] else '—'
    else:
        profit_per_jump = p.get('profit_per_jump_isk')
        if profit_per_jump is None:
            profit_per_jump = p['profit_isk'] / p['total_jumps']
        profit_per_jump_text = f'{money(profit_per_jump)} ISK'
    out = [f'<div class="routePath">{h(" → ".join(p["route"]))}</div>',
           '<div class="planSummary">',
           f'<div class="planMetric">Начальный бюджет<b>{money(s["budget_isk"])} ISK</b></div>',
           f'<div class="planMetric">Прибыль после комиссий<b>+{money(p["profit_isk"])} ISK</b></div>',
           f'<div class="planMetric">На финише<b>{money(p["final_budget_isk"])} ISK</b></div>',
           f'<div class="planMetric">Станций<b>{p["station_count"]} / {max_stations}</b></div>',
           f'<div class="planMetric">Прыжков<b>{p["total_jumps"]}</b></div>',
           f'<div class="planMetric">Прибыль на прыжок<b>{profit_per_jump_text}</b></div></div>',
           f'<p class="snapshotNote">На каждой станции закупки — до {max_types} видов товара. '
           f'Всего в плане {p["purchase_count"]} действий покупки. Сначала продай привезённый груз, '
           'затем закупайся на следующий перелёт. Выручка доступна для следующих покупок.</p>',
           f'<p class="snapshotNote">Стартуй на первой указанной станции. Старт выбирается свободно; '
           'начальная станция, порядок регионов и возврат из настроек полного круга здесь не задают маршрут. '
           f'Посещений станций — не более {max_stations}; маршрут может закончиться раньше. '
           'Перелёт к старту в число прыжков не включён.</p>',
           f'<p class="snapshotNote">Учтены настройки: карго {money(s["cargo_m3"])} м³, '
           f'неприкосновенный резерв {money(s["cash_reserve_isk"])} ISK, налог с продажи {s["sales_tax_pct"]}%, '
           f'комиссии покупки / продажи {s["buy_fee_pct"]}% / {s["sell_fee_pct"]}%. '
           f'Минимальная прибыль: {s["min_profit_pct"]}% и {money(s["min_profit_per_unit_isk"])} ISK/шт. '
           f'Область станций: {h(s["station_scope"])}; security систем ≥ {s["min_system_security"]}; '
           f'контрабанда {"исключена" if s["exclude_contraband"] else "разрешена настройками"}.</p>',
           '<p class="snapshotNote">Приоритет поиска — прибыль на прыжок, чтобы сократить перелёты. '
           'Это упрощённый поиск прибыльной цепочки; оптимальность не гарантируется. '
           'Цены и наличие ордеров проверь перед сделкой в игре.</p>',
           f'<p id="snapshotAge" class="snapshotNote" data-time="{h(oldest)}" data-max="{s["max_snapshot_age_minutes"]}">'
           f'Снимок проверен: {h(oldest)}</p>']
    missing = analysis['skipped'].get('unknown_type_ids', [])
    if missing:
        out.append(f'<p class="snapshotNote stale">Исключено типов без подтверждённых названия и объёма: {len(missing)}.</p>')
    if not p['trade_count']:
        out.append('<p class="stale">Прибыльная короткая цепочка под заданные настройки не найдена.</p>')
    out.extend(_render_steps(p, s, _plan_key(analysis, p, 'short'), short_mode=True))
    out.append('<p class="snapshotNote">На финише весь запланированный груз продан. Для следующей цепочки '
               'укажи фактический бюджет в settings.json и запусти <code>python Run_trade.py</code> '
               'для обновления ордеров, перерасчёта и отчётов. Отметки действий относятся только к этому плану.</p>')
    return '\n'.join(out)


def _render_steps(p, s, key_prefix, short_mode=False):
    out = []
    event_id = 0
    for step in p['steps']:
        title = step['station']['station_name'] if short_mode else step['region']
        suffix = ' — финиш' if step['index'] == len(p['steps'])-1 else ' — старт' if short_mode and step['index'] == 0 else ''
        out.append(f'<section class="routeStep"><h3>{step["index"]+1}. {h(title)}{suffix}</h3>')
        for event in step['events']:
            event_id += 1
            side = event['action']
            if side == 'fly':
                label = 'Лететь / вернуться на станцию'
            else:
                label = ('Купить' if side == 'buy' else 'Продать немедленно') + f" {event['quantity']:,} × {event['type_name']}"
            out.append(f'<div class="routeAction {h(side)}"><label><input type="checkbox" class="actionCheck" '
                       f'data-key="{h(key_prefix)}:{event_id}"> {h(label)}</label>')
            station = event['station']
            out.append(f'<p><b>{h(station["station_name"])}</b><br>{h(station["system_name"])} '
                       f'· security {station["security"]:.2f} · station ID {station["station_id"]}</p>')
            if side != 'fly':
                out.append(f'<small>Цена в ордере: {money(event["unit_price"])} ISK/шт. · '
                           f'{"Расход" if side == "buy" else "Получишь после комиссий"}: {money(event["total_cash_isk"])} ISK · '
                           f'Объём: {money(event["volume_per_unit_m3"]*event["quantity"])} м³<br>'
                           f'order ID: {event["order_id"]} · минимальная партия: {event["minimum_quantity"]:,}</small>')
            if side == 'buy':
                for delivery in event['deliveries']:
                    out.append(f'<small>Везти {delivery["quantity"]:,} шт. → {h(delivery["region"])} · '
                               f'{h(delivery["station"])} · ожидаемая прибыль +{money(delivery["expected_profit_isk"])} ISK</small>')
            out.append(f'<small>После действия: деньги {money(event["cash_after_isk"])} ISK · '
                       f'карго {money(event["cargo_after_m3"])} / {money(s["cargo_m3"])} м³</small>')
            if event['flight_systems']:
                systems = ' → '.join(x['system_name'] for x in event['flight_systems'])
                out.append(f'<details><summary>Путь до станции: {event["jumps"]} прыжков</summary><p>{h(systems)}</p></details>')
            out.append('</div>')
        out.append(f'<p class="snapshotNote">На вылете: <b>{money(step["cash_after_isk"])} ISK</b> · '
                   f'занято <b>{money(step["cargo_after_m3"])} / {money(s["cargo_m3"])} м³</b>.</p>')
        if step['onboard']:
            out.append('<details><summary>Что остаётся в трюме</summary>')
            for item in step['onboard']:
                out.append(f'<p>{h(item["type_name"])} × {item["quantity"]:,} → {h(item["sell_region"])}, {h(item["sell_station"])}</p>')
            out.append('</details>')
        out.append('</section>')
    return out
