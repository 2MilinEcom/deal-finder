"""Conservative, explainable ranking; search candidates are never verified coupons."""
from datetime import date
import re

CATEGORY_WORDS = {
    'coffee': ('coffee', 'espresso', 'latte', 'cappuccino'),
    'household': ('detergent', 'laundry', 'dish soap', 'paper towel', 'cleaner'),
    'toiletries': ('toothpaste', 'shampoo', 'deodorant', 'toothbrush'),
    'pharmacy': ('prescription', 'vitamin', 'ibuprofen', 'acetaminophen'),
    'groceries': ('milk', 'eggs', 'bread', 'banana', 'apple', 'rice', 'produce'),
    'electronics': ('headphones', 'laptop', 'charger', 'television'),
    'fuel': ('gasoline', 'diesel', 'fuel'),
    'fast food': ('burger', 'fries', 'taco', 'nuggets'),
}


def categories(text):
    text = (text or '').lower()
    return [category for category, words in CATEGORY_WORDS.items()
            if any(re.search(r'\b' + re.escape(word) + r'\b', text) for word in words)]


def expired(value):
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) < date.today()
    except ValueError:
        return False


def rank(deal, receipts, history, nearby, feedback):
    store = deal.get('store', '').casefold()
    text = f"{deal.get('title', '')} {deal.get('notes', '')}"
    cats = categories(text)
    visits = sum(1 for r in receipts if r.get('store', '').casefold() == store)
    purchases = sum(1 for r in receipts if set(cats) & set(categories(' '.join(r.get('items', [])))))
    used = sum(1 for d in history if d.get('used') and d.get('store', '').casefold() == store)
    negative = sum(1 for d in history if d.get('status') == 'dismissed'
                   and set(cats) & set(categories(d.get('title', '') + ' ' + (d.get('notes') or ''))))
    rejected = sum(1 for f in feedback if f.get('action') == 'dismissed'
                   and set(cats) & set(categories(f.get('title', ''))))
    interested = sum(1 for f in feedback if f.get('action') in ('interested', 'used')
                     and f.get('store', '').casefold() == store)
    distance = min((n['distance_meters'] for n in nearby if n.get('chain', '').casefold() == store
                    or n.get('name', '').casefold() == store), default=None)
    score = 20.0
    reasons = []
    if visits:
        score += min(visits * 5, 20); reasons.append('You shop at this business')
    if purchases:
        score += min(purchases * 6, 24); reasons.append('Matches items in your receipts')
    if used:
        score += min(used * 6, 18); reasons.append('You have used offers here before')
    if interested:
        score += min(interested * 4, 12); reasons.append('Based on your interested feedback')
    score -= min((negative + rejected) * 12, 48)
    kind = str(deal.get('deal_type') or '').lower()
    value = float(deal.get('discount_value') or 0)
    if '$' in kind or 'dollar' in kind or kind == 'cash back':
        score += min(value, 20)
    elif '%' in kind:
        score += min(value / 3, 12)
    minimum = float(deal.get('minimum_spend') or 0)
    if minimum > 0 and minimum > max(value, 1) * 10:
        score -= 5  # Large spend requirements make otherwise strong offers harder to use.
    if distance is not None:
        score += max(0, 15 - distance / 200); reasons.append('Close to your current location')
    if deal.get('status') == 'saved':
        score += 8; reasons.append('You saved this offer')
    if deal.get('verification_status') == 'verified':
        score += 10
    elif deal.get('source_type') == 'official':
        score += 6
    elif deal.get('source_type') in ('web', 'social'):
        score -= 8
    try:
        days = (date.today() - date.fromisoformat(str(deal.get('discovered_at') or deal.get('created_at'))[:10])).days
        score += max(0, 8 - max(days, 0) / 4)
    except (ValueError, TypeError):
        pass
    try:
        days_left = (date.fromisoformat(str(deal.get('expiration_date'))[:10]) - date.today()).days
        if 0 <= days_left <= 3:
            score += 6; reasons.append('Expires soon')
    except (ValueError, TypeError):
        pass
    return {**deal, 'score': round(score, 2), 'why': reasons or ['An active offer from your saved deals'],
            'distance_meters': distance, 'categories': cats}


def possible_stacks(deals):
    """Only stored component offers; no guaranteed or summed face-value savings."""
    pairs = []
    for i, a in enumerate(deals):
        for b in deals[i + 1:]:
            if a.get('store', '').casefold() != b.get('store', '').casefold():
                continue
            types = {str(a.get('deal_type') or '').casefold(), str(b.get('deal_type') or '').casefold()}
            if any('reward' in t or 'cash' in t for t in types) and any('coupon' in t for t in types):
                pairs.append({'store': a['store'], 'label': 'Possible stack',
                              'component_ids': [a['id'], b['id']],
                              'components': [a['title'], b['title']],
                              'terms': 'Check both offers: exclusions, spend thresholds and reward timing may prevent combining.',
                              'guaranteed': False, 'estimated_combined_value': None})
    return pairs[:3]
