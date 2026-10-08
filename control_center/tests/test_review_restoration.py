import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center import store
from control_center.app import deals, latest_review


class ReviewRestorationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.patches = [patch.object(store, 'DATA', root), patch.object(store, 'DB', root / 'test.sqlite'), patch.object(store, 'ARTIFACTS', root / 'artifacts')]
        for item in self.patches:
            item.start()
        store.initialize()
        for week in ['a', 'b']:
            store.execute('INSERT INTO weeks(id,start,end,label,status,updated_at) VALUES(?,?,?,?,?,?)', (week, '2026-09-16', '2026-09-22', week, 'prepared', 'new-inputs'))
        fields = {'growth_stage':'Early','deal_date':'2026-09-18','website':'https://example.com/','round':'Series A','amount':'$10 million','investors':'Alpha','sector':'Construction Tech','description':'Contractor software','geography':'New York City, New York, USA','announcement_link':'https://example.com/news'}
        store.execute('INSERT INTO deals(id,week_id,company,entity_key,fields,block_ids,sources,evidence,missing,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)', ('company', 'a', 'Example', 'example', store.encode(fields), '[]', '[]', '[]', '{}', 'new-inputs'))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.folder.cleanup()

    def add_run(self, name, kind, status, started, week='a'):
        store.execute('INSERT INTO runs(id,week_id,kind,target,status,stage,started_at,metrics) VALUES(?,?,?,?,?,?,?,?)', (name, week, kind, 'local', status, 'complete', started, '{"prepared_at":"old-inputs"}'))

    def test_latest_completed_review_preserves_decisions_and_audit_after_preparation(self):
        self.add_run('saved', 'review', 'completed', '2026-10-01')
        answer = {'fit':'Yes','reason':'Source-backed fit','confidence':.95,'known_investment':False}
        store.execute('INSERT INTO decisions(run_id,deal_id,first_pass,final) VALUES(?,?,?,?)', ('saved', 'company', store.encode(answer), store.encode(answer)))
        store.execute('INSERT INTO events(run_id,at,stage,message,detail) VALUES(?,?,?,?,?)', ('saved','2026-10-01','complete','Saved review audit','{}'))
        self.add_run('failed-review', 'review', 'failed', '2026-10-02')
        self.add_run('prep', 'prepare', 'completed', '2026-10-03')
        value = latest_review('a')
        self.assertEqual(value['id'], 'saved')
        self.assertEqual(value['decisions'][0]['final']['fit'], 'Yes')
        self.assertEqual(value['events'][0]['message'], 'Saved review audit')
        self.assertEqual(value['metrics']['prepared_at'], 'old-inputs')
        with patch('control_center.fx.prepared_rates', return_value=({}, None)):
            rows = deals('a')
        self.assertEqual(rows[0]['decision']['run_id'], 'saved')
        self.assertEqual(rows[0]['fields']['geography'], 'New York City, New York, USA')

    def test_active_review_does_not_discard_last_completed_result(self):
        self.add_run('saved', 'review', 'local_completed', '2026-10-01')
        self.add_run('active', 'review', 'running', '2026-10-02')
        self.assertEqual(latest_review('a')['id'], 'saved')
        store.execute("UPDATE runs SET status='completed' WHERE id='active'")
        self.assertEqual(latest_review('a')['id'], 'active')

    def test_latest_review_is_week_scoped_and_not_limited_by_recent_job_history(self):
        self.add_run('saved', 'review', 'completed', '2026-01-01')
        self.add_run('other', 'review', 'completed', '2026-12-01', week='b')
        for index in range(110):
            self.add_run('prep-'+str(index), 'prepare', 'completed', '2026-11-01')
        self.assertEqual(latest_review('a')['id'], 'saved')
        self.assertEqual(latest_review('b')['id'], 'other')
        self.assertIsNone(latest_review('no-review'))

    def test_all_weeks_uses_each_weeks_completed_review_and_preserves_unreviewed(self):
        self.add_run('review-a', 'review', 'completed', '2026-10-01')
        self.add_run('review-b', 'review', 'completed', '2026-10-02', week='b')
        original = store.get_deals('a')[0]
        for did, company in [('other', 'Other'), ('new', 'New')]:
            store.execute('INSERT INTO deals(id,week_id,company,entity_key,fields,block_ids,sources,evidence,missing,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)', (did, 'b', company, did, store.encode({**original['fields'], 'website': 'https://' + did + '.example/'}), '[]', '[]', '[]', '{}', 'new-inputs'))
        for rid, did, fit in [('review-a', 'company', 'Yes'), ('review-b', 'other', 'No')]:
            answer = {'fit': fit, 'reason': 'Source-backed decision'}
            store.execute('INSERT INTO decisions(run_id,deal_id,first_pass,final) VALUES(?,?,?,?)', (rid, did, store.encode(answer), store.encode(answer)))
        self.add_run('failed', 'review', 'failed', '2026-10-03', week='b')
        with patch('control_center.fx.prepared_rates', return_value=({}, None)):
            values = {value['id']: value for value in deals()}
            self.assertEqual(values['company']['decision']['run_id'], 'review-a')
            self.assertEqual(values['other']['decision']['run_id'], 'review-b')
            self.assertIsNone(values['new']['decision'])
            self.assertEqual([value['id'] for value in deals('All', fit='Unreviewed')], ['new'])
            self.assertEqual([value['id'] for value in deals('All', fit='Yes')], ['company'])
            self.assertEqual([value['id'] for value in deals('All', q='Other')], ['other'])
            self.assertEqual([value['id'] for value in deals('a')], ['company'])


if __name__ == '__main__':
    unittest.main()
