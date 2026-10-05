"""Republish a completed decision set in USD without another AI review."""
import argparse
import copy
import time

from . import store
from .fx import prepared_rates
from .google import Publisher, sheet_date
from .logic import make_package, normalized_name, sheet_amount


def refresh(source_id):
    source = store.get_run(source_id)
    if source['status'] != 'completed' or source['kind'] != 'review' or source['target'] != 'test':
        raise ValueError('Select a completed review of the Northrock demo copies')
    if store.query("SELECT id FROM runs WHERE status='running'"):
        raise ValueError('Wait for the active workflow to finish')
    old = source['package']
    decisions = {d['deal_id']:d['final'] for d in source['decisions']}
    identities = {normalized_name(d['company']):d['deal_id'] for d in source['decisions']}
    deals = []
    columns = dict(zip(['growth_stage','deal_date','website','round','amount','investors','sector','description','geography','announcement_link'], [1,2,4,5,6,7,8,9,10,12]))
    for row in old['rows']:
        did = identities[normalized_name(row[3])]
        deals.append({'id':did,'company':row[3],'fields':{key:row[col] for key,col in columns.items()},'sources':row[11].split('; '),'block_ids':[]})
    book, _ = prepared_rates(source['week_id'])
    book = copy.deepcopy(book)
    observations = {o['d']:o for o in book['observations']}
    for week in store.query("SELECT id FROM weeks WHERE status='prepared'"):
        cached, _ = prepared_rates(week['id'])
        observations.update({o['d']:o for o in cached['observations']})
    book['observations'] = sorted(observations.values(), key=lambda o:o['d'])
    package = make_package(deals, decisions, old['start'], old['end'], book)
    if any(a[:5]+a[7:] != b[:5]+b[7:] for a,b in zip(old['rows'],package['rows'])) or len(old['rows']) != len(package['rows']):
        raise ValueError('USD refresh must not change the selected deals or other row fields')
    rid = store.new_run(source['week_id'], 'usd_refresh', 'test')
    started = time.perf_counter()
    try:
        writer = Publisher('test')
        snapshot = writer.snapshot()
        store.event(rid,'publication','Normalizing USD amounts and republishing the existing final decision set',{'source_run':source_id,'new_ai_calls':0,'amount_normalization':package['stats']['amount_normalization']})
        result = writer.publish(package,snapshot,rid)
        writer.verify(package,snapshot)
        actual = writer.sheet_snapshot()['values'].get('values',[])[1:]
        for audit in result['historical_amount_normalization']:
            matching = [r for r in actual if len(r)>6 and r[3]==audit['company'] and sheet_date(r[2])==audit['deal_date']]
            expected = sheet_amount(audit.get('display_amount','USD amount unavailable'))
            if not matching or any(r[6] != expected for r in matching):
                raise ValueError('Historical USD amount readback differs for '+audit['company'])
        metrics = {**package['stats'],'publication':result,'source_run':source_id,'new_ai_calls':0}
        store.update_run(rid,status='completed',stage='complete',finished_at=store.now(),elapsed=time.perf_counter()-started,package=package,metrics=metrics)
        return {'run_id':rid,'rows':len(package['rows']),'historical_rows_normalized':len(result['historical_amount_normalization']),'capital_usd_m':package['stats']['capital_usd_m'],'seconds':time.perf_counter()-started}
    except Exception as error:
        store.update_run(rid,status='failed',stage='failed',error=str(error),finished_at=store.now(),elapsed=time.perf_counter()-started)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    print(refresh(parser.parse_args().run))
