"""Exact event constraints; do not substitute unrelated clients or competitors."""
def candidate_rows(event, rows):
    if event.get('channel') not in (None, 'wholesale'):
        return []
    if event['event_type'] not in (
        'price_decrease', 'price_increase', 'new_competitor', 'margin_below_plan',
        'market_share_change', 'price_position_change',
    ):
        return []
    evidence = event.get('evidence') or {}
    client = event.get('company_code') if event.get('company_role') == 'client' else None
    rival = (event.get('company_code') if event.get('company_role') == 'competitor'
             else evidence.get('competitor_code'))
    product = event.get('product_code')
    return [r for r in rows
            if r['region_code'] == event['region_code']
            and r.get('channel') == 'wholesale'
            and (not client or r['client_code'] == client)
            and (not rival or r['competitor_code'] == rival)
            and (not product or r['product_code'] == product)]
