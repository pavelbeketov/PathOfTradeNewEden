"""Escaped, self-contained HTML itinerary for the computed round trip."""
from html import escape


def h(value):
    return escape(str(value), quote=True)


def money(value):
    return f"{value:,.2f}"


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
    event_id = 0
    key_prefix = f"trader:{analysis['snapshot_id']}:{analysis['settings_hash'][:12]}"
    for step in p['steps']:
        out.append(f'<section class="routeStep"><h3>{step["index"]+1}. {h(step["region"])}'
                   f'{" — финиш" if step["index"] == len(p["steps"])-1 else ""}</h3>')
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
