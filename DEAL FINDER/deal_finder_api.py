from __future__ import annotations

import hashlib
import secrets
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from uuid import UUID
from contextlib import contextmanager, asynccontextmanager, closing
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import certifi
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from savings_engine import expired, rank, possible_stacks
from public_offers import public_offers, brand_key
from offer_dates import offer_expiration
from deal_judge import openai_key, judge_ai, rule_picks, finalize, current, JudgeUnavailable

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('DEAL_FINDER_DB', str(APP_DIR / 'deals.db')))
CACHE_SECONDS = 2700
_search_lock = threading.Lock()
_judge_lock = threading.Lock()


@contextmanager
def get_connection():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def migrate_database():
    # SQLite's backup API captures a consistent snapshot, including any WAL contents.
    if DB_PATH.exists():
        backup = DB_PATH.with_suffix('.db.pre-v3.bak')
        if not backup.exists():
            with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
    with get_connection() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL, title TEXT NOT NULL,
            deal_type TEXT, discount_value REAL DEFAULT 0, minimum_spend REAL DEFAULT 0,
            promo_code TEXT, expiration_date TEXT, source TEXT, notes TEXT,
            used INTEGER DEFAULT 0, household_member TEXT DEFAULT 'Me', created_at TEXT)''')
        columns = {r['name'] for r in c.execute('PRAGMA table_info(deals)')}
        additions = {'status': "TEXT NOT NULL DEFAULT 'active'", 'redeemed_at': 'TEXT',
                     'source_url': 'TEXT', 'verification_status': "TEXT NOT NULL DEFAULT 'unknown'",
                     'discovered_at': 'TEXT', 'category': 'TEXT', 'source_type': "TEXT NOT NULL DEFAULT 'saved'"}
        for name, definition in additions.items():
            if name not in columns:
                c.execute(f'ALTER TABLE deals ADD COLUMN {name} {definition}')
        c.execute("UPDATE deals SET status='used' WHERE used=1")
        for row in c.execute("SELECT id,store,source FROM deals WHERE source_url IS NULL").fetchall():
            url = canonical_url(row['source'] or '')
            if url:
                source_type, _ = classify_source(url, row['store'], row['store'])
                c.execute("UPDATE deals SET source_url=?,source_type=?,verification_status='unverified' WHERE id=?",
                          (url, source_type, row['id']))
        c.execute('''CREATE TABLE IF NOT EXISTS deal_events (
            id INTEGER PRIMARY KEY, deal_id INTEGER, action TEXT NOT NULL,
            created_at TEXT NOT NULL, FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE SET NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS discovery_cache (
            cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, expires_at REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS discovery_feedback (
            candidate_id TEXT PRIMARY KEY, store TEXT NOT NULL, title TEXT NOT NULL,
            action TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        c.execute('CREATE TABLE IF NOT EXISTS ai_shortlist_cache (cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, expires_at REAL NOT NULL)')
        c.execute('PRAGMA user_version=3')


@asynccontextmanager
async def lifespan(app):
    migrate_database()
    yield


app = FastAPI(title='Deal Finder API', version='3.0', lifespan=lifespan)


@app.middleware('http')
async def authenticate(request: Request, call_next):
    token = os.getenv('DEAL_FINDER_API_TOKEN', '').strip()
    if token and not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        return JSONResponse(status_code=401, content={'detail': 'Enter your Mac server access token in Settings.'})
    try:
        return await call_next(request)
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={'detail': 'The deal database is temporarily unavailable. Try again shortly.'})



def now_iso():
    return datetime.now(timezone.utc).isoformat()


def serialize(row):
    result = dict(row)
    if not result.get('expiration_date') and result.get('source_type') in ('official', 'delivery', 'web', 'social'):
        result['expiration_date'] = offer_expiration((result.get('title') or '') + ' ' + (result.get('notes') or ''))
    if not result.get('used') and expired(result.get('expiration_date')):
        result['status'] = 'expired'
    return result


@app.get('/health')
def health():
    try:
        with get_connection() as c:
            c.execute('SELECT id FROM deals LIMIT 1').fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(503, 'The deal database is unavailable.') from exc
    return {'ok': True, 'database_exists': DB_PATH.exists(), 'api_version': '3.0',
            'discovery_configured': bool(get_tavily_api_key()), 'public_discovery_available': True, 'ai_configured': bool(openai_key()), 'cache_seconds': CACHE_SECONDS}


@app.get('/deals')
def get_deals(store: str | None = None, household_member: str | None = None,
              include_used: bool = False, include_history: bool = False):
    clauses, params = [], []
    if store:
        clauses.append('LOWER(store)=LOWER(?)'); params.append(store.strip())
    if household_member:
        clauses.append("LOWER(COALESCE(household_member,'Me')) IN (LOWER(?),'household')")
        params.append(household_member.strip())
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with get_connection() as c:
        rows = [serialize(r) for r in c.execute('SELECT * FROM deals' + where + ' ORDER BY id DESC', params)]
    if include_history:
        return rows
    return [r for r in rows if (include_used or not r['used']) and r['status'] not in ('dismissed', 'expired')]


@app.get('/deals/{deal_id}')
def get_deal(deal_id: int):
    with get_connection() as c:
        row = c.execute('SELECT * FROM deals WHERE id=?', (deal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Deal not found')
    return serialize(row)


def update_state(deal_id, status):
    with get_connection() as c:
        row = c.execute('SELECT * FROM deals WHERE id=?', (deal_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Deal not found')
        if status == 'used' and expired(row['expiration_date']):
            raise HTTPException(409, 'This offer has expired.')
        used = int(status == 'used')
        if row['status'] != status or row['used'] != used:
            c.execute('UPDATE deals SET used=?,status=?,redeemed_at=? WHERE id=?',
                      (used, status, now_iso() if used else None, deal_id))
            c.execute('INSERT INTO deal_events(deal_id,action,created_at) VALUES(?,?,?)', (deal_id, status, now_iso()))
    return {'ok': True, 'deal_id': deal_id, 'used': bool(used), 'status': status}


@app.post('/deals/{deal_id}/use')
def mark_deal_used(deal_id: int):
    return update_state(deal_id, 'used')


@app.post('/deals/{deal_id}/unused')
def mark_deal_unused(deal_id: int):
    return update_state(deal_id, 'active')


class StateRequest(BaseModel):
    status: str

    @field_validator('status')
    @classmethod
    def valid_status(cls, v):
        if v not in ('active', 'saved', 'used', 'dismissed'):
            raise ValueError('Invalid deal state')
        return v


@app.post('/deals/{deal_id}/state')
def change_state(deal_id: int, payload: StateRequest):
    return update_state(deal_id, payload.status)


class ReceiptOfferRequest(BaseModel):
    receipt_id: UUID
    benefit_id: UUID
    store: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    value: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    percent_value: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    expiration_date: date | None = None
    terms: str = Field(default='', max_length=10000)
    household_member: str = Field(default='Me', min_length=1, max_length=100)
    confirmed_available: bool

    @model_validator(mode='after')
    def one_value_type(self):
        if self.value and self.percent_value:
            raise ValueError('Use a dollar value or a percent value, not both')
        return self

    @field_validator('store', 'title', 'household_member')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('A name is required')
        return value.strip()


@app.post('/receipt-offers/save')
def save_receipt_offer(payload: ReceiptOfferRequest):
    if not payload.confirmed_available:
        raise HTTPException(422, 'Confirm that this is an unused offer available to you.')
    expiry = payload.expiration_date.isoformat() if payload.expiration_date else None
    if expired(expiry):
        raise HTTPException(409, 'This receipt offer has expired.')
    source = f'Receipt:{payload.receipt_id}:{payload.benefit_id}'
    with get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('SELECT id FROM deals WHERE source=?', (source,)).fetchone()
        if existing:
            return {'ok': True, 'deal_id': existing['id']}
        kind = '% Off' if payload.percent_value else ('$ Off' if payload.value else 'Other')
        cursor = c.execute('''INSERT INTO deals(store,title,deal_type,discount_value,source,notes,
            expiration_date,household_member,status,verification_status,source_type,created_at,discovered_at)
            VALUES(?,?,?,?,?,?,?,?,'saved','user_reviewed','receipt',?,?)''',
            (payload.store, payload.title, kind, payload.percent_value or payload.value, source,
             payload.terms + '\nImported from a receipt; confirm eligibility and terms.', expiry,
             payload.household_member, now_iso(), now_iso()))
        return {'ok': True, 'deal_id': cursor.lastrowid}


class NearbyDealSearchRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    chain: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    force_refresh: bool = False
    interests: str = Field(default='', max_length=300)

    @field_validator('business_name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Business name must not be blank')
        return value.strip()


def get_tavily_api_key():
    key = os.getenv('TAVILY_API_KEY') or os.getenv('TAVILY_KEY')
    if key and key.strip():
        return key.strip()
    import tomllib
    for path in (APP_DIR / '.streamlit/secrets.toml', Path.home() / '.streamlit/secrets.toml'):
        try:
            data = tomllib.loads(path.read_text())
            for name in ('TAVILY_API_KEY', 'tavily_api_key', 'TAVILY_KEY', 'tavily_key'):
                if isinstance(data.get(name), str) and data[name].strip():
                    return data[name].strip()
        except (OSError, ValueError):
            continue
    return None


def tavily_search(query, api_key, max_results=8, include_domains=None):
    body = {'query': query, 'search_depth': 'basic', 'max_results': max_results,
            'topic': 'general', 'include_answer': False, 'include_raw_content': False}
    if include_domains: body['include_domains'] = include_domains
    request = urllib.request.Request('https://api.tavily.com/search', data=json.dumps(body).encode(),
                                    headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'})
    try:
        context = ssl.create_default_context(cafile=os.getenv('SSL_CERT_FILE') or certifi.where())
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            data = json.load(response)
        return data.get('results', []) if isinstance(data.get('results'), list) else []
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Never return an upstream response body, key, or local path to clients.
        raise HTTPException(502, 'Current offer search is temporarily unavailable. Try again later.') from exc


OFFICIAL_DOMAINS = {"domino's": 'dominos.com', 'dominos': 'dominos.com', 'domino’s': 'dominos.com', 'cvs': 'cvs.com', 'walgreens': 'walgreens.com', 'costco': 'costco.com',
                    'target': 'target.com', 'walmart': 'walmart.com', 'safeway': 'safeway.com',
                    'jack in the box': 'jackinthebox.com', 'starbucks': 'starbucks.com',
                    'mcdonald’s': 'mcdonalds.com', "mcdonald's": 'mcdonalds.com'}


def matches_domain(host, domain):
    return host == domain or host.endswith('.' + domain)


def classify_source(url, business_name, chain):
    host = (urlparse(url).hostname or '').lower()
    for domain, label in [('doordash.com', 'DoorDash'), ('ubereats.com', 'Uber Eats'), ('grubhub.com', 'Grubhub')]:
        if matches_domain(host, domain):
            return 'delivery', label + ' · eligibility varies by account'
    if any(matches_domain(host, d) for d in ('instagram.com', 'facebook.com', 'tiktok.com', 'x.com', 'twitter.com')):
        return 'social', 'Social promotion · unverified'
    domain = OFFICIAL_DOMAINS.get((chain or business_name).strip().casefold())
    if domain and matches_domain(host, domain):
        return 'official', 'Official source · terms unverified'
    return 'web', 'Web candidate · unverified'


def canonical_url(url):
    p = urlparse(url)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        return None
    pairs = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in ('fbclid', 'gclid')]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), '', urlencode(sorted(pairs)), ''))


@app.post('/nearby-deals/search')
def search_nearby_deals(payload: NearbyDealSearchRequest):
    key = hashlib.sha256(json.dumps(['discovery-v4-written-expiry', payload.business_name.casefold(), (payload.chain or '').casefold(),
                                   (payload.address or '').casefold(), payload.interests.casefold(), bool(get_tavily_api_key())]).encode()).hexdigest()
    # Serialize misses to prevent concurrent clients from issuing identical paid searches.
    with _search_lock:
        with get_connection() as c:
            cached = c.execute('SELECT * FROM discovery_cache WHERE cache_key=?', (key,)).fetchone()
        if cached and not payload.force_refresh and cached['expires_at'] > time.time():
            return {**json.loads(cached['result']), 'cached': True}
        # Forced refresh still has a 60-second floor to prevent button-spam costs.
        if cached and cached['expires_at'] - CACHE_SECONDS > time.time() - 60:
            return {**json.loads(cached['result']), 'cached': True}
        brand = payload.chain or payload.business_name
        direct, source_message = public_offers(brand)
        api_key = get_tavily_api_key()
        if not api_key and not direct and brand_key(brand) not in ("domino's", 'cvs', 'doordash'):
            raise HTTPException(503, 'Offer discovery is not configured on your Mac. Saved deals are still available.')
        query = f'"{brand}" {payload.address or ""} current offers coupons rewards official delivery promotions {payload.interests} {date.today():%B %Y}'
        raw = []
        if api_key and not direct:
            domain = OFFICIAL_DOMAINS.get(brand.casefold()) or ('doordash.com' if brand_key(brand) == 'doordash' else None)
            try: raw = tavily_search(query, api_key, include_domains=[domain] if domain else None)
            except HTTPException:
                if not direct: raise
                source_message = 'Public merchant offers loaded; broader search is temporarily unavailable.'
        candidates = {r['id']: r for r in direct}
        for item in raw:
            url = canonical_url(str(item.get('url') or ''))
            title = str(item.get('title') or '').strip()
            summary = re.sub(r'\s+', ' ', str(item.get('content') or '')).strip()[:420]
            if not url or not title or not re.search(r'\b(offer|deal|coupon|promo|discount|bogo|free|reward|save|savings)\b', title + ' ' + summary, re.I):
                continue
            dated_title = re.findall(r'\b(20\d{2})\b', title + ' ' + urlparse(url).path)
            if dated_title and max(map(int, dated_title)) < date.today().year:
                continue
            # An explicit old end date is excluded; unknown end dates stay clearly unknown.
            end = offer_expiration(title + ' ' + summary)
            if end and expired(end):
                continue
            source, label = classify_source(url, payload.business_name, payload.chain)
            try:
                provider_score = float(item.get('score') or 0)
            except (ValueError, TypeError):
                provider_score = 0
            score = min(max(provider_score, 0), 1) + (0.3 if source == 'official' else 0)
            candidate = {'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'title': title,
                         'summary': summary, 'url': url, 'source_type': source, 'source_label': label,
                         'score': score, 'published_date': item.get('published_date') if isinstance(item.get('published_date'), str) else None,
                         'expiration_date': end, 'verification_status': 'unverified',
                         'caveat': 'Confirm terms, location and account eligibility.' + (' Costco membership and warehouse eligibility may be required.' if 'costco' in brand.casefold() else '')}
            if url not in candidates or score > candidates[url]['score']:
                candidates[url] = candidate
        results = sorted(candidates.values(), key=lambda r: -r['score'])[:10]
        response = {'business_name': payload.business_name, 'chain': payload.chain, 'address': payload.address,
                    'searched_at': now_iso(), 'result_count': len(results), 'results': results, 'cached': False,
                    'message': source_message if not api_key else None, 'broad_search_configured': bool(api_key)}
        with get_connection() as c:
            c.execute('INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?)', (key, json.dumps(response), time.time() + CACHE_SECONDS))
            c.execute('DELETE FROM discovery_cache WHERE expires_at < ?', (time.time() - 86400,))
        return response


class ReceiptProfile(BaseModel):
    store: str = Field(max_length=200)
    items: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('items')
    @classmethod
    def bounded_items(cls, values):
        if any(len(v) > 200 for v in values):
            raise ValueError('Item name too long')
        return values


class NearbyBusiness(BaseModel):
    name: str = Field(max_length=200)
    chain: str = Field(max_length=200)
    distance_meters: float = Field(ge=0, le=100000)


class ForYouRequest(BaseModel):
    receipts: list[ReceiptProfile] = Field(default_factory=list, max_length=100)
    nearby: list[NearbyBusiness] = Field(default_factory=list, max_length=25)
    limit: int = Field(default=8, ge=1, le=10)


@app.post('/for-you')
def for_you(payload: ForYouRequest):
    history = get_deals(include_history=True)
    with get_connection() as c:
        feedback = [dict(r) for r in c.execute('SELECT * FROM discovery_feedback')]
    active = [r for r in history if not r['used'] and r['status'] not in ('expired', 'dismissed')]
    ranked = sorted((rank(d, [r.model_dump() for r in payload.receipts], history,
                          [n.model_dump() for n in payload.nearby], feedback) for d in active), key=lambda r: -r['score'])
    return {'recommendations': ranked[:payload.limit], 'stacks': possible_stacks(active),
            'generated_at': now_iso(), 'ranking_version': 1}


class CandidateFeedback(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    store: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    action: str

    @field_validator('action')
    @classmethod
    def validate_action(cls, value):
        if value not in ('interested', 'dismissed', 'used'):
            raise ValueError('Invalid feedback action')
        return value


@app.post('/feedback')
def candidate_feedback(payload: CandidateFeedback):
    with get_connection() as c:
        c.execute('INSERT OR REPLACE INTO discovery_feedback VALUES(?,?,?,?,?)',
                  (payload.candidate_id, payload.store, payload.title, payload.action, now_iso()))
    return {'ok': True}


class SaveCandidateRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    household_member: str = Field(default='Me', min_length=1, max_length=100)


@app.post('/candidates/save')
def save_candidate(payload: SaveCandidateRequest):
    # Resolve only a server-cached candidate. The client cannot invent a verified offer.
    with get_connection() as c:
        for row in c.execute('SELECT result FROM discovery_cache WHERE expires_at>?', (time.time(),)).fetchall():
            cached = json.loads(row['result'])
            candidate = next((d for d in cached['results'] if d['id'] == payload.candidate_id), None)
            if not candidate:
                continue
            if expired(candidate.get('expiration_date')) or expired(offer_expiration(candidate['title'] + ' ' + candidate['summary'])):
                raise HTTPException(409, 'This candidate has expired. Search for current offers.')
            existing = c.execute('SELECT id FROM deals WHERE source_url=? AND title=? AND household_member=?',
                                 (candidate['url'], candidate['title'], payload.household_member)).fetchone()
            if existing:
                return {'ok': True, 'deal_id': existing['id']}
            cursor = c.execute('''INSERT INTO deals(store,title,source,source_url,notes,expiration_date,
                status,verification_status,source_type,discovered_at,created_at,household_member)
                VALUES(?,?,?,?,?,?,'saved','unverified',?,?,?,?)''',
                (cached['chain'] or cached['business_name'], candidate['title'], candidate['source_label'],
                 candidate['url'], candidate['summary'] + '\n' + candidate['caveat'], candidate['expiration_date'],
                 candidate['source_type'], cached['searched_at'], now_iso(), payload.household_member))
            return {'ok': True, 'deal_id': cursor.lastrowid}
    raise HTTPException(404, 'This candidate is no longer cached. Refresh the business search first.')


class ShortlistCandidate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    distance_meters: float | None = Field(default=None, ge=0, le=100000)

class BestDealsRequest(BaseModel):
    candidates: list[ShortlistCandidate] = Field(default_factory=list, max_length=32)
    interests: str = Field(default='', max_length=300)

@app.post('/best-deals')
def best_deals(payload: BestDealsRequest):
    requested = {p.id for p in payload.candidates}
    distances = {p.id: p.distance_meters for p in payload.candidates}
    # Resolve facts server-side; clients and models cannot invent offer data or eligibility.
    indexed = {}
    with get_connection() as c:
        for row in c.execute('SELECT result FROM discovery_cache WHERE expires_at>? ORDER BY expires_at DESC', (time.time(),)):
            cached = json.loads(row['result'])
            for candidate in cached['results']:
                if candidate['id'] in requested and candidate['id'] not in indexed:
                    indexed[candidate['id']] = {**candidate, 'store': cached.get('chain') or cached['business_name']}
    offers = [offer for offer in indexed.values() if current(offer)]
    if not offers:
        return {'mode': 'empty', 'summary': 'No current offers are ready to compare. Check for fresh deals first.',
                'picks': [], 'eligible_ids': [], 'checked_at': now_iso()}
    # Cache the public assessment. Personal ranking happens locally on the Mac after retrieval.
    key = hashlib.sha256(json.dumps([date.today().isoformat(), 'judge-v1', sorted(offers, key=lambda o: o['id'])], sort_keys=True).encode()).hexdigest()
    with _judge_lock:
        with get_connection() as c:
            cached = c.execute('SELECT result FROM ai_shortlist_cache WHERE cache_key=? AND expires_at>?', (key, time.time())).fetchone()
        if cached:
            assessment = json.loads(cached['result']); mode = 'ai'; note = None
        else:
            try:
                assessment = judge_ai(offers)
                if not any(p.get('id') in indexed for p in assessment['picks'] if isinstance(p, dict)):
                    raise JudgeUnavailable('AI could not assess these offers reliably. Showing basic value checks.')
                mode = 'ai'; note = None
                with get_connection() as c:
                    c.execute('INSERT OR REPLACE INTO ai_shortlist_cache VALUES(?,?,?)', (key, json.dumps(assessment), time.time() + CACHE_SECONDS))
                    c.execute('DELETE FROM ai_shortlist_cache WHERE expires_at<?', (time.time(),))
            except JudgeUnavailable as exc:
                assessment = rule_picks(offers); mode = 'basic'; note = str(exc)
    picks = finalize(offers, assessment, payload.interests, distances)
    # Do not propagate arbitrary model summary claims. Explain the verified mode instead.
    summary = ('AI compared advertised savings and restrictions. Check eligibility before buying.' if mode == 'ai'
               else note or 'Basic value checks; AI selection is unavailable.')
    if not picks and mode == 'ai': summary = 'AI did not find a strong offer worth recommending in this batch.'
    return {'mode': mode, 'summary': summary, 'picks': picks, 'eligible_ids': [o['id'] for o in offers], 'checked_at': now_iso()}
