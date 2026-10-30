"""Explicit sample inventory for the loopback workspace's live sales check.

Speech and requirement extraction still use the configured providers. This
adapter replaces inventory only and is never used by the hosted Zoho client.
"""
from __future__ import annotations

import re
from copy import deepcopy


def _item(sku, name, category, rate, stock, description, image):
    return {'item_id': sku, 'sku': sku, 'name': name, 'category': category,
            'rate': rate, 'available_stock': stock, 'stock_on_hand': stock,
            'description': description, 'image_url': f'/demo-products/{image}.svg',
            'currency_code': 'INR', 'catalogue_source': 'local-demo',
            'demo': True, 'status': 'active', 'can_be_sold': True}


ITEMS = (
    _item('DEMO-FAN-REG1200', 'Everyday ceiling fan 1200 mm', 'fan', 2299, 24,
          'Regular ceiling fan. Three blades, brown finish, 1200 mm sweep.', 'fan'),
    _item('DEMO-FAN-BLDC1200', 'Aero BLDC ceiling fan 1200 mm', 'fan', 4499, 16,
          'Quiet, energy efficient BLDC fan with remote control. 1200 mm sweep.', 'fan'),
    _item('DEMO-FAN-LED1200', 'Luxe ceiling fan 1200 mm with LED light', 'fan', 6999, 8,
          'Premium BLDC ceiling fan with dimmable LED light and remote control.', 'fan-light'),
    _item('DEMO-FAN-PEDESTAL', 'Breeze pedestal fan 400 mm', 'fan', 3499, 12,
          'Portable standing fan, adjustable height and oscillation.', 'pedestal'),
    _item('DEMO-MOTOR-1HP', 'Water pump motor 1 HP', 'motor', 6999, 10,
          'Single phase electric motor, 1 HP for domestic water pump applications.', 'motor'),
    _item('DEMO-MOTOR-2HP', 'Water pump motor 2 HP', 'motor', 9499, 6,
          'Heavy duty single phase electric motor, 2 HP for water pump applications.', 'motor'),
    _item('DEMO-AC-15T', 'Inverter split air conditioner 1.5 ton', 'ac', 39990, 5,
          '1.5 ton inverter AC, quiet cooling and energy saving mode.', 'ac'),
    _item('DEMO-AC-1T', 'Inverter split air conditioner 1 ton', 'ac', 31990, 4,
          '1 ton inverter AC for a compact room with quiet cooling.', 'ac'),
    _item('DEMO-LIGHT-LED', 'LED ceiling light 24 W', 'light', 1299, 30,
          'Round LED ceiling light, 24 W, warm white finish.', 'light'),
)


def _categories(query: str) -> set[str]:
    words = set(re.findall(r'[a-z0-9]+', query.casefold()))
    categories = set()
    if words & {'fan', 'fans', 'ceiling', 'pedestal', 'bldc'}:
        categories.add('fan')
    # A fan's motor specification should search fans; a separate motor request
    # should search pump motors rather than matching every BLDC fan description.
    if words & {'pump', 'pumps', 'hp'} or (words & {'motor', 'motors'} and 'fan' not in categories):
        categories.add('motor')
    if words & {'ac', 'conditioner', 'conditioners', 'aircon', 'cooling'}:
        categories.add('ac')
    if words & {'light', 'lights', 'lighting', 'lamp'} and 'fan' not in categories:
        categories.add('light')
    return categories


class LocalDemoCatalogueClient:
    def status(self):
        return {'connected': True, 'organization_id': 'local-demo', 'currency': 'INR',
                'catalogue_source': 'local-demo', 'demo': True}

    async def search_books_items(self, org, query='', date=None, page=1):
        if org != 'local-demo':
            raise ValueError('Demo catalogue requires its local organization')
        if page != 1:
            return {'items': [], 'has_more': False, 'demo': True, 'catalogue_source': 'local-demo'}
        query = str(query or '').strip().casefold()[:200]
        categories = _categories(query)
        tokens = re.findall(r'[a-z0-9]+', query)
        candidates = []
        for item in ITEMS:
            name = f"{item['name']} {item['sku']}".casefold()
            description = item['description'].casefold()
            if query and categories and item['category'] not in categories:
                continue
            if query and not categories and not any(token in name for token in tokens):
                continue
            score = sum(5 if token in name else 1 if token in description else 0 for token in tokens)
            candidates.append((score, item))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return {'items': [deepcopy(item) for _, item in candidates], 'has_more': False,
                'demo': True, 'catalogue_source': 'local-demo'}
