"""Visual regression smoke using recorded API fixtures; no live backend required."""
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / 'tools' / 'screenshots' / 'redesign'
SHOTS.mkdir(parents=True, exist_ok=True)
FIXTURES = ROOT / 'tools' / 'fixtures'
MAP = {'/auth/login': 'auth_login', '/auth/me': 'auth_me', '/health': 'health',
       '/farm/overview': 'farm_overview', '/farm/farms': 'farm_farms',
       '/farm/crops': 'farm_crops', '/farm/growth-records': 'farm_growth_records',
       '/chat/sessions': 'chat_sessions', '/pesticide/usage': 'pesticide_usage',
       '/knowledge/stats': 'knowledge_stats', '/knowledge/diseases': 'knowledge_diseases'}
errors = []

def mock(route):
    path = urlparse(route.request.url).path.split('/api/v1')[-1]
    name = MAP.get(path)
    if not name and path.startswith('/chat/sessions/'):
        name = 'chat_session_detail'
    if not name:
        errors.append('Unmocked: ' + path)
        route.fulfill(status=404, json={'detail': 'No fixture'})
        return
    route.fulfill(json=json.loads((FIXTURES / (name + '.json')).read_text(encoding='utf-8')))

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/api/v1/**', mock)
    page.goto((ROOT / 'index.html').as_uri())
    page.locator('#gate').wait_for(state='visible')
    page.screenshot(path=str(SHOTS / 'login.png'))
    page.locator('#gateUser').fill('demo')
    page.locator('#gatePass').fill('demo1234')
    page.locator('#gateSubmit').click()
    page.locator('#goDiag').wait_for()
    if page.evaluate("document.body.classList.contains('elder')"):
        page.locator('#elderBtn').click()
    for width in (1440, 390):
        page.set_viewport_size({'width': width, 'height': 1000 if width == 1440 else 844})
        for route in ('home', 'diagnose', 'chat', 'farm', 'pesticide', 'knowledge'):
            page.evaluate('(route) => Agri.app.go(route)', route)
            page.locator('#view h1').wait_for()
            page.wait_for_timeout(350)
            assert '页面出错了' not in page.locator('#view').inner_text(), route
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, route)
            page.screenshot(path=str(SHOTS / f'{width}-{route}.png'), full_page=True)
            print('PASS', width, route)
        page.locator('#elderBtn').click()
        page.evaluate("Agri.app.go('home')")
        page.locator('#goDiag').wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'elder overflow'
        page.screenshot(path=str(SHOTS / f'{width}-elder.png'), full_page=True)
        page.locator('#elderBtn').click()
    assert not errors, errors
    browser.close()
print('All visual smoke checks passed')
