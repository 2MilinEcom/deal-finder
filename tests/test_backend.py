"""Runs the actual API against a temporary copy of the uploaded database; no Tavily spend."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1] / 'DEAL FINDER'
sys.path.insert(0, str(BACKEND))
import deal_finder_api as api
from savings_engine import categories


class BackendTests(unittest.TestCase):
    def test_receipt_offer_review_idempotence_and_expiration(self):
        payload = api.ReceiptOfferRequest(receipt_id=uuid4(), benefit_id=uuid4(), store='CVS',
            title='$4 off a $20 purchase', value=4, terms='Spend $20. ExtraCare required.', confirmed_available=True)
        saved = api.save_receipt_offer(payload)
        deal = api.get_deal(saved['deal_id'])
        self.assertEqual(deal['source_type'], 'receipt')
        self.assertEqual(deal['verification_status'], 'user_reviewed')
        self.assertEqual(deal['status'], 'saved')
        self.assertEqual(deal['discount_value'], 4)
        api.mark_deal_used(saved['deal_id'])
        self.assertEqual(api.save_receipt_offer(payload), saved)
        self.assertEqual(api.get_deal(saved['deal_id'])['used'], 1)
        with self.assertRaises(api.HTTPException):
            api.save_receipt_offer(payload.model_copy(update={'confirmed_available': False}))
        with self.assertRaises(api.HTTPException):
            api.save_receipt_offer(payload.model_copy(update={'expiration_date': api.date(2000, 1, 1)}))
        percent = payload.model_copy(update={'benefit_id': uuid4(), 'value': 0, 'percent_value': 20})
        self.assertEqual(api.get_deal(api.save_receipt_offer(percent)['deal_id'])['deal_type'], '% Off')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'deals.db'
        shutil.copy2(BACKEND / 'deals.db', self.db)
        self.original_path = api.DB_PATH
        api.DB_PATH = self.db
        api.migrate_database()

    def tearDown(self):
        api.DB_PATH = self.original_path
        self.temp.cleanup()

    def test_migration_preserves_rows_and_is_idempotent(self):
        with sqlite3.connect(BACKEND / 'deals.db') as c:
            original = c.execute('SELECT id,store,title,used FROM deals').fetchall()
        api.migrate_database()
        with sqlite3.connect(self.db) as c:
            self.assertEqual(original, c.execute('SELECT id,store,title,used FROM deals').fetchall())
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertTrue(self.db.with_suffix('.db.pre-v3.bak').exists())

    def test_lifecycle_and_events(self):
        deal = api.get_deals(include_history=True)[0]
        with api.get_connection() as c:
            c.execute('UPDATE deals SET expiration_date=NULL,used=0,status=? WHERE id=?', ('active', deal['id']))
        api.mark_deal_used(deal['id']); api.mark_deal_used(deal['id'])
        self.assertEqual(api.get_deal(deal['id'])['status'], 'used')
        self.assertIsNotNone(api.get_deal(deal['id'])['redeemed_at'])
        with api.get_connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM deal_events').fetchone()[0], 1)
        api.mark_deal_unused(deal['id'])
        self.assertEqual(api.get_deal(deal['id'])['used'], 0)
        api.change_state(deal['id'], api.StateRequest(status='dismissed'))
        self.assertNotIn(deal['id'], [r['id'] for r in api.get_deals()])
        api.change_state(deal['id'], api.StateRequest(status='saved'))
        self.assertEqual(api.get_deal(deal['id'])['status'], 'saved')

    def test_expiration_and_domain_spoofing(self):
        deal = api.get_deals(include_history=True)[0]
        with api.get_connection() as c:
            c.execute("UPDATE deals SET expiration_date='2020-01-01',used=0,status='active' WHERE id=?", (deal['id'],))
        self.assertEqual(api.get_deal(deal['id'])['status'], 'expired')
        with self.assertRaises(api.HTTPException) as raised:
            api.mark_deal_used(deal['id'])
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(api.classify_source('https://cvs.com.evil.test', 'CVS', 'CVS')[0], 'web')
        self.assertEqual(api.classify_source('https://notdoordash.com', 'CVS', 'CVS')[0], 'web')
        self.assertEqual(api.classify_source('https://www.cvs.com/offers', 'CVS', 'CVS')[0], 'official')

    def test_discovery_cache_dedup_save_and_refresh_floor(self):
        raw = [dict(url='https://costco.com/offers?utm_source=foo', title='Laundry savings', content='Save on detergent', score=.9),
               dict(url='https://costco.com/offers#x', title='Laundry savings', content='Save on detergent', score=.8),
               dict(url='javascript:alert(1)', title='Free stuff', content='free', score=1),
               dict(url='https://example.com/old', title='Offer', content='Offer expires 2020-01-01', score=.8)]
        payload = api.NearbyDealSearchRequest(business_name='Costco', chain='Costco')
        with patch.object(api, 'get_tavily_api_key', return_value='fake-test-key'), patch.object(api, 'tavily_search', return_value=raw) as search:
            first = api.search_nearby_deals(payload)
            self.assertEqual(first['result_count'], 1)
            self.assertIn('membership', first['results'][0]['caveat'])
            second = api.search_nearby_deals(payload)
            self.assertTrue(second['cached'])
            api.search_nearby_deals(api.NearbyDealSearchRequest(business_name='Costco', chain='Costco', force_refresh=True))
            self.assertEqual(search.call_count, 1)
            with api.get_connection() as c:
                c.execute('UPDATE discovery_cache SET expires_at=0')
            api.search_nearby_deals(payload)
            self.assertEqual(search.call_count, 2)
            saved = api.save_candidate(api.SaveCandidateRequest(candidate_id=first['results'][0]['id']))
            again = api.save_candidate(api.SaveCandidateRequest(candidate_id=first['results'][0]['id']))
            self.assertEqual(saved['deal_id'], again['deal_id'])
            deal = api.get_deal(saved['deal_id'])
            self.assertEqual(deal['verification_status'], 'unverified')
            self.assertEqual(deal['status'], 'saved')

    def test_personalization_and_feedback(self):
        with api.get_connection() as c:
            c.execute('DELETE FROM deal_events'); c.execute('DELETE FROM deals')
            c.execute("INSERT INTO deals(store,title,used,status) VALUES('Costco','Detergent offer',0,'active')")
            c.execute("INSERT INTO deals(store,title,used,status) VALUES('Other','Random offer',0,'active')")
        base = api.for_you(api.ForYouRequest())['recommendations']
        personalized = api.for_you(api.ForYouRequest(receipts=[api.ReceiptProfile(store='Costco', items=['laundry detergent'])],
                                                     nearby=[api.NearbyBusiness(name='Costco', chain='Costco', distance_meters=500)]))['recommendations']
        self.assertEqual(personalized[0]['store'], 'Costco')
        self.assertGreater(personalized[0]['score'], next(r for r in base if r['store'] == 'Costco')['score'])
        self.assertIn('Matches items in your receipts', personalized[0]['why'])
        api.candidate_feedback(api.CandidateFeedback(candidate_id='x', store='Costco', title='Detergent', action='dismissed'))
        lowered = api.for_you(api.ForYouRequest())['recommendations']
        self.assertLess(next(r for r in lowered if r['store'] == 'Costco')['score'], next(r for r in base if r['store'] == 'Costco')['score'])
        self.assertEqual(categories('ZXQ 12 OZ'), [])

    def test_public_offers_without_search_key_and_distinct_saves(self):
        from public_offers import extract_public_offers
        html = "<h2>Carry out for just $7.99 each.</h2><h2>Mix and match any two for $6.99 each.</h2>"
        direct = extract_public_offers("Domino's", html)
        payload = api.NearbyDealSearchRequest(business_name="Domino's", chain="Domino's")
        with patch.object(api, 'get_tavily_api_key', return_value=None), patch.object(api, 'public_offers', return_value=(direct, None)), patch.object(api, 'tavily_search') as paid:
            found = api.search_nearby_deals(payload)
            self.assertEqual(found['result_count'], 2); paid.assert_not_called()
            ids = [api.save_candidate(api.SaveCandidateRequest(candidate_id=r['id']))['deal_id'] for r in found['results']]
            self.assertEqual(len(set(ids)), 2)
            self.assertFalse(found['broad_search_configured'])
            self.assertTrue(all(r['verification_status'] == 'unverified' for r in found['results']))

    def test_public_offer_parser_excludes_generic_hidden_and_expired(self):
        from public_offers import extract_public_offers
        html = "<h2>Explore top promos</h2><script><h2>Save 90% off</h2></script><h2>10/5 - 10/11 50% off pizzas</h2><h2>9/1 - 9/5 20% off</h2><h2>12/1 - 12/5 30% off</h2>"
        offers = extract_public_offers("Domino's", html, api.date(2026, 10, 10))
        self.assertEqual(len(offers), 1); self.assertEqual(offers[0]['expiration_date'], '2026-10-11')
        self.assertEqual(extract_public_offers('Unknown merchant', html), [])
        dash = extract_public_offers('DoorDash', '<h2>Deals on groceries</h2><h2>$0 delivery fee on your first order when you sign up</h2>')
        self.assertEqual(len(dash), 1); self.assertIn('account eligibility', dash[0]['caveat'])

    def test_blocked_public_source_reports_gap_not_fake_offer(self):
        with patch.object(api, 'get_tavily_api_key', return_value=None), patch.object(api, 'public_offers', return_value=([], 'Merchant blocked access')):
            result = api.search_nearby_deals(api.NearbyDealSearchRequest(business_name='CVS', chain='CVS'))
            self.assertEqual(result['results'], []); self.assertIn('blocked', result['message'])

    def test_written_expiration_dates_filter_stale_cvs_promos(self):
        from offer_dates import offer_expiration
        self.assertEqual(offer_expiration('promotions expire September 26, 2026 at 11:59 PM ET'), '2026-09-26')
        self.assertEqual(offer_expiration('Offer ends Oct. 11, 2026'), '2026-10-11')
        self.assertEqual(offer_expiration('Valid until 10/11/26'), '2026-10-11')
        self.assertIsNone(offer_expiration('Shop current hair care deals'))
        old_saved = api.serialize(dict(id=99999, title='Old hair promotion', notes='Offers expire September 26, 2026', source_type='official', used=0, expiration_date=None))
        self.assertEqual(old_saved['status'], 'expired')
        with patch.object(api, 'public_offers', return_value=([], None)), patch.object(api, 'get_tavily_api_key', return_value='test'), patch.object(api, 'tavily_search', return_value=[
            {'url': 'https://www.cvs.com/hair', 'title': 'Hair care offers', 'content': 'Buy 1 get 1 free. Promotions expire September 26, 2026 at 11:59 PM ET.'}]):
            self.assertEqual(api.search_nearby_deals(api.NearbyDealSearchRequest(business_name='CVS', chain='CVS'))['results'], [])

    def test_ai_shortlist_grounding_caps_and_cache(self):
        from datetime import timedelta
        from deal_judge import finalize
        tomorrow = (api.date.today() + timedelta(days=1)).isoformat()
        offers = [dict(id='strong', title='50% off menu-priced pizzas', summary='Prices vary', url='https://www.dominos.com/deals', source_type='official', expiration_date=tomorrow, caveat='Confirm local terms.'),
            dict(id='first', title='$0 delivery fee on your first order', summary='Other fees apply', url='https://www.doordash.com/promos', source_type='delivery', expiration_date=None, caveat='New customers only.'),
            dict(id='generic', title='Hair care deals', summary='Browse our hair care department', url='https://www.cvs.com/hair', source_type='official', expiration_date=None, caveat='Check terms.'),
            dict(id='stale', title='Old sale', summary='Offers expire September 26, 2026', url='https://www.cvs.com/old', source_type='official', expiration_date=None, caveat='Check terms.')]
        with api.get_connection() as c:
            c.execute('INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?)', ('judge-fixture', json.dumps({'chain': "Domino's", 'business_name': "Domino's", 'results': offers}), time.time() + 2700))
        assessed = {'summary': 'Test', 'picks': [dict(id=o['id'], verdict='great', reason='Large advertised discount.', catch='Check the terms.', confidence='medium') for o in offers] + [dict(id='invented', verdict='great')]}
        payload = api.BestDealsRequest(candidates=[api.ShortlistCandidate(id=o['id'], distance_meters=300) for o in offers], interests='pizza')
        with patch.object(api, 'judge_ai', return_value=assessed) as model:
            result = api.best_deals(payload)
            self.assertEqual(result['mode'], 'ai')
            picks = {p['id']: p for p in result['picks']}
            self.assertEqual(picks['strong']['verdict'], 'great')
            self.assertEqual(picks['first']['verdict'], 'check')
            self.assertEqual(picks['generic']['verdict'], 'check')
            self.assertNotIn('stale', result['eligible_ids']); self.assertNotIn('invented', picks)
            api.best_deals(payload.model_copy(update={'interests': 'hair care'}))
            self.assertEqual(model.call_count, 1)
        altered = finalize([{**offers[0], 'store': "Domino's"}], {'picks': [dict(id='strong', verdict='great', reason='Save $999!', catch='Guaranteed!', confidence='high')]})
        self.assertEqual(altered[0]['verdict'], 'check'); self.assertNotIn('$999', altered[0]['reason'])

    def test_ai_failure_is_labeled_basic_and_unknown_ids_never_reach_model(self):
        with patch.object(api, 'judge_ai') as model:
            result = api.best_deals(api.BestDealsRequest(candidates=[api.ShortlistCandidate(id='invented')]))
            self.assertEqual(result['mode'], 'empty'); model.assert_not_called()
        offer = dict(id='test', title='$5 off pizza', summary='Spend $20', caveat='Check terms', expiration_date=None, source_type='official')
        with api.get_connection() as c:
            c.execute('INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?)', ('fallback-fixture', json.dumps({'chain': 'Pizza', 'business_name': 'Pizza', 'results': [offer]}), time.time() + 2700))
        with patch.object(api, 'judge_ai', side_effect=api.JudgeUnavailable('AI unavailable')):
            result = api.best_deals(api.BestDealsRequest(candidates=[api.ShortlistCandidate(id='test')]))
            self.assertEqual(result['mode'], 'basic'); self.assertIn('unavailable', result['summary'])

    def test_missing_key_and_unknown_deal(self):
        with patch.object(api, 'get_tavily_api_key', return_value=None), self.assertRaises(api.HTTPException) as raised:
            api.search_nearby_deals(api.NearbyDealSearchRequest(business_name='Store'))
        self.assertEqual(raised.exception.status_code, 503)
        with self.assertRaises(api.HTTPException) as raised:
            api.mark_deal_unused(999999)
        self.assertEqual(raised.exception.status_code, 404)

    def test_http_startup_validation_and_endpoints(self):
        # Real uvicorn server and HTTP JSON, using an OS-assigned port.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = dict(os.environ, DEAL_FINDER_DB=str(self.db))
        env.pop('DEAL_FINDER_API_TOKEN', None)
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'deal_finder_api:app', '--host', '127.0.0.1', '--port', str(port)],
                                cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def request(path, body=None):
            req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=3) as res: return res.status, json.load(res)
            except urllib.error.HTTPError as err:
                with err: return err.code, json.load(err)
        try:
            for _ in range(600):
                try:
                    if request('/health')[0] == 200: break
                except OSError: time.sleep(.1)
            else: self.fail('Uvicorn did not start')
            self.assertTrue(request('/health')[1]['ok'])
            rows = request('/deals?include_history=true')[1]
            self.assertIsInstance(rows, list)
            self.assertEqual(request('/deals/999999')[0], 404)
            self.assertEqual(request('/nearby-deals/search', {'business_name': '   '})[0], 422)
            self.assertEqual(request('/for-you', {'limit': 99})[0], 422)
            self.assertEqual(request('/for-you', {'nearby': [{'name': 'a', 'chain': 'a', 'distance_meters': -1}]})[0], 422)
            self.assertEqual(request('/deals/1/state', {'status': 'invented'})[0], 422)
            self.assertEqual(request('/for-you', {})[0], 200)
            offer = {'receipt_id': str(uuid4()), 'benefit_id': str(uuid4()), 'store': 'CVS',
                     'title': '$4 off next purchase', 'value': 4, 'confirmed_available': True}
            self.assertEqual(request('/receipt-offers/save', offer)[0], 200)
            self.assertEqual(request('/receipt-offers/save', dict(offer, confirmed_available=False))[0], 422)
            self.assertEqual(request('/receipt-offers/save', dict(offer, percent_value=20))[0], 422)
            for row in rows:
                self.assertTrue(all(key in row for key in ('id', 'store', 'title', 'used', 'status')))
        finally:
            proc.terminate(); proc.wait(timeout=10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
