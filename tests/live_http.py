"""Bounded HTTP acceptance checks. Run only against a separately seeded local DB."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request
from uuid import uuid4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    def __init__(self, base):
        self.base = base
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.csrf = None

    def request(self, path, data=None, raw=None, csrf=True, method=None):
        headers = {}
        if data is not None:
            raw = urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif raw is not None:
            headers['Content-Type'] = 'application/json'
            raw = raw.encode() if isinstance(raw, str) else raw
        if raw is not None and csrf and self.csrf:
            headers['X-CSRFToken'] = self.csrf
        req = Request(self.base + path, data=raw, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read().decode('utf-8', errors='replace')
            match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
            if match:
                self.csrf = match[1]
            return response.status, body, dict(response.headers)

    def login(self, login, password):
        assert self.request('/login/')[0] == 200
        assert self.request('/login/', data={'username': login, 'password': password})[0] == 302
        # Login rotates CSRF: obtain a fresh token from an authenticated page.
        assert self.request('/bank/' if login == 'expert' else '/')[0] == 200


def run(base, access):
    credentials = dict(line.split(': ', 1) for line in access.read_text().splitlines() if ': ' in line)
    results = []

    def check(name, actual, expected):
        passed = actual == expected
        results.append({'check': name, 'passed': passed, 'actual': actual, 'expected': expected})
        if not passed:
            raise AssertionError(f'{name}: {actual!r}, expected {expected!r}')

    anon = Session(base)
    check('health', anon.request('/health/')[0], 200)
    check('anonymous API', anon.request('/api/v1/applications/')[0], 401)
    anon.request('/login/')
    check('reversed login', anon.request('/login/', data={'username': 'entrepreneur'[::-1], 'password': 'incorrect'})[0], 400)
    check('SQL login', anon.request('/login/', data={'username': "' OR '1'='1", 'password': 'incorrect'})[0], 400)
    owner, other, staff = [Session(base) for _ in range(3)]
    for session, login in [(owner, 'entrepreneur'), (other, 'client_risk'), (staff, 'expert')]:
        session.login(login, credentials[login])
    for path in ['/', '/products/', '/applications/', '/applications/new/', '/profile/', '/documents/', '/notifications/', '/help/']:
        status, body, _ = owner.request(path)
        check('client page ' + path, status, 200)
        check('interface copy ' + path, bool(re.search(r'демоставка|учебный прототип|примеры скоринга|демонстрационная версия', body, re.I)), False)
    for path in ['/bank/', '/bank/audit/', '/bank/scorecard/']:
        check('staff page ' + path, staff.request(path)[0], 200)
        check('customer cannot open ' + path, owner.request(path)[0], 403)
    check('customer cannot submit scoring', owner.request('/api/v1/scorecard/', raw='{}')[0], 403)
    check('removed self-approval endpoint', owner.request('/scenarios/', data={'consent': 'on', 'payload': '{}'})[0], 404)
    payloads = Path(__file__).resolve().parents[1] / 'fixtures/scorecard'
    for name, decision, amount in [('good_mature', 'approve', 1500000), ('reduced_amount', 'approve', 8310000), ('mass_address', 'decline', None)]:
        status, body, _ = staff.request('/api/v1/scorecard/', raw=(payloads / f'{name}.json').read_text())
        check(name + ' HTTP', status, 200)
        result = json.loads(body)
        check(name + ' decision', result['decision'], decision)
        check(name + ' amount', result['approved_amount'], amount)
    for raw in ['null', '[]', '{', '{"x":1,"x":2}', '{"x":NaN}', '[' * 20 + '0' + ']' * 20]:
        check('invalid JSON ' + raw[:25], staff.request('/api/v1/scorecard/', raw=raw)[0], 400)
    for amount in ['-1', 'NaN', 'Infinity', '1e9999', '<script>alert(1)</script>', '1500000.001', "' OR 1=1"]:
        check('bad calculator amount ' + amount, owner.request('/api/v1/calculator/?' + urlencode({'amount': amount}))[0], 400)
    apps = json.loads(owner.request('/api/v1/applications/')[1])['results']
    app = next(row for row in apps if row['status'] == 'approved')
    app_path = '/applications/' + app['id'] + '/'
    check('other customer cannot read application', other.request(app_path)[0], 404)
    check('other customer cannot read API', other.request('/api/v1' + app_path)[0], 404)
    check('other customer cannot read schedule', other.request(app_path + 'schedule/')[0], 404)
    body = owner.request(app_path)[1]
    document = re.search(r'href="(/documents/[^/]+/download/)"', body)[1]
    check('other customer cannot read PDF', other.request(document)[0], 404)
    _, _, headers = owner.request(document)
    check('PDF is an attachment', headers['Content-Type'], 'application/octet-stream')
    check('PDF cannot be MIME-sniffed', headers['X-Content-Type-Options'], 'nosniff')
    for action in ['accept', 'cancel', 'decide', 'review']:
        check('other customer cannot ' + action, other.request(app_path + 'actions/' + action + '/', data={'version': app['version'], 'confirm': 'on'})[0], 404)
    check('CSRF enforced', owner.request(app_path + 'actions/accept/', data={'version': app['version'], 'confirm': 'on'}, csrf=False)[0], 403)
    rejected = next(row for row in apps if row['status'] == 'rejected')
    rejected_path = '/applications/' + rejected['id'] + '/'
    check('rejected acceptance returns to card', owner.request(rejected_path + 'actions/accept/', data={'version': rejected['version'], 'confirm': 'on'})[0], 302)
    check('rejected application remains blocked', json.loads(owner.request('/api/v1' + rejected_path)[1])['status'], 'rejected')
    owner.request('/applications/new/')
    token = str(uuid4())
    data = {'creation_token': token, 'product': 'working', 'amount': '1500000', 'months': '24', 'purpose': 'Проверка повторной отправки'}
    # Independent sessions avoid sharing a mutable cookie jar across threads.
    second = Session(base)
    second.login('entrepreneur', credentials['entrepreneur'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner.request, '/applications/new/', data=data)
        replay = pool.submit(second.request, '/applications/new/', data=data)
        responses = [first.result(), replay.result()]
    check('parallel create HTTP', [r[0] for r in responses], [302, 302])
    check('parallel create resolves to one draft', responses[0][2]['Location'].split('/')[2], responses[1][2]['Location'].split('/')[2])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda session: session.request(app_path + 'actions/accept/', data={'version': app['version'], 'confirm': 'on'}), [owner, second]))
    check('parallel acceptance HTTP', [r[0] for r in responses], [302, 302])
    check('application completed', json.loads(owner.request('/api/v1' + app_path)[1])['status'], 'funded')
    check('GET cannot log out', owner.request('/logout/')[0], 405)
    status, body, headers = owner.request('/')
    check('CSP has no unsafe inline', 'unsafe-inline' in headers['Content-Security-Policy'], False)
    for asset in re.findall(r'(?:src|href)="(/static/[^"#]+)', body):
        check('static asset ' + asset, anon.request(asset)[0], 200)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--access', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if urlparse(args.base).hostname not in {'127.0.0.1', 'localhost', '::1'} or not str(args.access.resolve()).startswith('/tmp/'):
        parser.error('Use a local server and an isolated workspace under /tmp.')
    result = run(args.base, args.access)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'HTTP checks passed: {len(result)}')
