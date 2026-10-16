#!/usr/bin/env python3
"""Sono Bestie jobs crawler.

Reads employers.json, finds open sonographer-type jobs from each employer's own PUBLIC career site
(sitemap + schema.org JobPosting data, or the Workday career site's own search), and writes jobs.json.

Polite by design: honours robots.txt, identifies itself, waits between requests, and stores only
structured facts (title, date, place, type) plus a link back to the employer. It never copies job descriptions.
"""
import collections, hashlib, json, os, re, subprocess, sys, time, urllib.parse, urllib.robotparser

HERE = os.path.dirname(os.path.abspath(__file__))
UA = 'SonoBestieJobsBot/1.0 (+https://liondenwealth.github.io/sonobestie/; sonobestie@gmail.com)'
DELAY = float(os.environ.get('CRAWL_DELAY', '1.5'))
LIMIT = int(os.environ.get('CRAWL_LIMIT', '0'))  # employers to run (0 = all), for testing
ONLY = [x.strip().lower() for x in os.environ.get('CRAWL_ONLY', '').split(',') if x.strip()]

# A title must look like a sonography / echo / vascular-ultrasound job ...
INCLUDE = re.compile(r'sonograph|ultrasound|echocardiograph|\becho\b.{0,12}\btech|vascular\s+(tech|sonograph|lab)|\brdms\b|\brdcs\b|\brvt\b|\bsono\b', re.I)
# ... and not a physics / management / unrelated role
EXCLUDE = re.compile(r'physicist|physics|\bmanager\b|\bdirector\b|\bnurse\b|\bRN\b|physician|radiologist|\bMD\b|\bfellow\b|\bintern\b|veterinar', re.I)
URL_HINT = re.compile(r'sonograph|ultrasound|echocardiograph|sonog|vascular-tech|cardiac-sono|\brdms\b|\brvt\b|\brdcs\b', re.I)

STATES = {'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR','california':'CA','colorado':'CO','connecticut':'CT','delaware':'DE','florida':'FL','georgia':'GA','hawaii':'HI','idaho':'ID','illinois':'IL','indiana':'IN','iowa':'IA','kansas':'KS','kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD','massachusetts':'MA','michigan':'MI','minnesota':'MN','mississippi':'MS','missouri':'MO','montana':'MT','nebraska':'NE','nevada':'NV','new hampshire':'NH','new jersey':'NJ','new mexico':'NM','new york':'NY','north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK','oregon':'OR','pennsylvania':'PA','rhode island':'RI','south carolina':'SC','south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT','vermont':'VT','virginia':'VA','washington':'WA','west virginia':'WV','wisconsin':'WI','wyoming':'WY','district of columbia':'DC'}
ABBR = set(STATES.values())

last = [0.0]
robots = {}


def pause():
    wait = DELAY - (time.time() - last[0])
    if wait > 0:
        time.sleep(wait)
    last[0] = time.time()


def get(url):
    pause()
    r = subprocess.run(['curl', '-sL', '-m', '40', '--compressed', '-A', UA, url], capture_output=True)
    return r.stdout.decode('utf8', 'ignore')


def post_json(url, payload):
    pause()
    r = subprocess.run(['curl', '-s', '-m', '40', '-X', 'POST', '-H', 'Content-Type: application/json', '-A', UA, url, '-d', json.dumps(payload)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def robots_for(url):
    p = urllib.parse.urlparse(url)
    root = f'{p.scheme}://{p.netloc}'
    if root not in robots:
        txt = get(root + '/robots.txt')
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        robots[root] = (rp, re.findall(r'(?im)^sitemap:\s*(\S+)', txt))
    return robots[root]


def allowed(url):
    return robots_for(url)[0].can_fetch(UA, url)


def sitemap_urls(sm, depth=0):
    x = get(sm)
    locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', x)
    if '<sitemapindex' in x and depth < 2:
        out = []
        for l in locs[:200]:
            out += sitemap_urls(l, depth + 1)
        return out
    return locs


def jobposting(html):
    for b in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(b)
        except Exception:
            continue
        items = d if isinstance(d, list) else d.get('@graph', [d]) if isinstance(d, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get('@type') == 'JobPosting':
                return it
    return None


def place(jp):
    """(city, state-abbreviation, lat, lon) from a JobPosting."""
    loc = jp.get('jobLocation')
    loc = loc[0] if isinstance(loc, list) and loc else loc
    if not isinstance(loc, dict):
        return '', '', None, None
    a = loc.get('address', {}) if isinstance(loc.get('address'), dict) else {}
    city = (a.get('addressLocality') or '').strip()
    region = (a.get('addressRegion') or '').strip()
    st = region.upper() if region.upper() in ABBR else STATES.get(region.lower(), '')
    if not st and re.search(r',\s*([A-Z]{2})\s*(\d{5})?$', city):
        m = re.search(r',\s*([A-Z]{2})\s*(\d{5})?$', city)
        st, city = m.group(1), city[:m.start()].strip()
    if not st:
        m = re.search(r",\s*([A-Z]{2})\b", city)
        if m:
            city, st = city[:m.start()], m.group(1)
        else:
            for full, ab in STATES.items():
                m = re.search(r",\s*" + full + r"\b", city, re.I)
                if m:
                    city, st = city[:m.start()], ab
                    break
    if ' - ' in city or ',' in city:
        city = re.split(r' - |, ', city)[-1]
    city = city.strip()
    geo = loc.get('geo') if isinstance(loc.get('geo'), dict) else {}
    try:
        lat, lon = float(geo.get('latitude')), float(geo.get('longitude'))
    except Exception:
        lat = lon = None
    return city, st, lat, lon


def raw_locality(jp):
    loc = jp.get('jobLocation')
    loc = loc[0] if isinstance(loc, list) and loc else loc
    a = (loc or {}).get('address', {}) if isinstance(loc, dict) else {}
    return (a.get('addressLocality') or '') if isinstance(a, dict) else ''


def kind(t):
    t = ' '.join(t) if isinstance(t, list) else str(t or '')
    t = t.upper()
    if 'PART' in t:
        return 'part_time'
    if 'FULL' in t:
        return 'full_time'
    if 'PER_DIEM' in t or 'PRN' in t or 'TEMPORARY' in t or 'CONTRACT' in t:
        return 'per_diem'
    return 'other'


WD_RE = re.compile(r'https://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)')
WD_HINT = {}
WD_LOC = {}
WD_TERMS = ['sonographer', 'ultrasound', 'echocardiograph', 'vascular technologist']


def workday_links(base):
    m = WD_RE.search(base) or WD_RE.search(get(base))
    if not m:
        return None
    org, wd, site = m.groups()
    root = f'https://{org}.{wd}.myworkdayjobs.com'
    api = f'{root}/wday/cxs/{org}/{site}/jobs'
    if not allowed(api):
        return []
    seen, out = set(), []
    for term in WD_TERMS:
        for offset in range(0, 400, 20):
            d = post_json(api, {'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': term})
            ps = d.get('jobPostings', [])
            for p in ps:
                if p['externalPath'] in seen or not INCLUDE.search(p['title']) or EXCLUDE.search(p['title']):
                    continue
                seen.add(p['externalPath'])
                link = f'{root}/{site}{p["externalPath"]}'
                WD_HINT[link] = [b for b in p.get('bulletFields', []) if b][1:]
                WD_LOC[link] = p.get('locationsText') or ''
                out.append(link)
            if len(ps) < 20:
                break
    return out



GEO_FILE = os.path.join(HERE, 'geocache.json')
try:
    GEO = json.load(open(GEO_FILE))
except Exception:
    GEO = {}
NOMINATIM = 'https://nominatim.openstreetmap.org/search'


FULL = {v: k.title() for k, v in STATES.items()}


def _nominatim(q):
    time.sleep(1.1)  # Nominatim's usage policy: at most 1 request per second
    qs = urllib.parse.urlencode({'q': q, 'format': 'jsonv2', 'addressdetails': 1, 'limit': 5, 'countrycodes': 'us'})
    r = subprocess.run(['curl', '-s', '-m', '30', '-A', UA, f'{NOMINATIM}?{qs}'], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return []


def geocode(query, allowed_states):
    """(city, state, lat, lon) via OpenStreetMap Nominatim, cached. Tries the employer's states in order.
    Street addresses and plain city names get a city and coordinates; hospital / clinic names only a state (their match is less reliable)."""
    key = f'{query}|{",".join(allowed_states)}'
    if key in GEO:
        v = GEO[key]
        return tuple(v) if v else None
    street = bool(re.match(r'^\d', query.strip()))
    plain_city = bool(re.fullmatch(r"[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,2}", query.strip())) and not re.search(r'center|clinic|hospital|medical|health|building|campus|pavilion|care|institute', query, re.I)
    best = None
    for st in (list(allowed_states)[:4] or [None]):
        for hit in _nominatim(f'{query}, {FULL[st]}' if st else query):
            a = hit.get('address', {})
            hst = STATES.get((a.get('state') or '').lower(), '')
            if not hst or (st and hst != st):
                continue
            if hit.get('addresstype') in ('county', 'state', 'region', 'country', 'state_district', 'administrative'):
                continue
            city = a.get('city') or a.get('town') or a.get('village') or a.get('hamlet') or a.get('suburb') or ''
            if not city:
                continue
            if street or plain_city:
                best = (city, hst, float(hit['lat']), float(hit['lon']))
            else:
                best = ('', hst, None, None)
            break
        if best:
            break
    GEO[key] = list(best) if best else None
    return best


EMP_STATES = {e['name']: list(e.get('states', [])) for e in json.load(open(os.path.join(HERE, 'employers.json')))}


def fill_places(jobs):
    """Give every job a state and coordinates: parse what the employer wrote, then look the rest up."""
    by_emp = collections.defaultdict(collections.Counter)
    for j in jobs:
        if j['state']:
            by_emp[j['employer']][j['state']] += 1
    for j in jobs:
        emp_states = EMP_STATES.get(j['employer'], []) + [s for s, n in by_emp[j['employer']].most_common(6) if s not in EMP_STATES.get(j['employer'], [])]
        if not j['state']:
            raw = j.get('raw') or j['city']
            m = re.match(r"^([A-Za-z .'-]+)-([A-Z]{2})$", raw.strip())  # Workday style "Boston-MA"
            if m:
                j['city'], j['state'] = m.group(1).strip(), m.group(2)
            else:
                clean = re.sub(r'\([^)]*\)', '', raw).strip(' -,')
                tries = [clean]
                g = None
                for q in tries:
                    g = geocode(q, emp_states)
                    if g:
                        break
                if g:
                    j['city'] = g[0] if g[0] else ('' if (not j['city'] or re.search(r'\d', j['city']) or j['city'] == raw) else j['city'])
                    j['state'] = g[1]
                    if g[2] is not None:
                        j['lat'], j['lon'] = g[2], g[3]
        if j['state'] and j['lat'] is None and j['city']:
            g = geocode(f"{j['city']}, {j['state']}", {j['state']})
            if g:
                j['lat'], j['lon'] = g[2], g[3]
    for j in jobs:  # never show a street, clinic name or building as a city
        if not j['state'] or re.search(r'\d|center|clinic|hospital|medical|health|building|campus|pavilion|institute', j['city'] or '', re.I):
            j['city'] = j['city'] if j['state'] and j['lat'] is not None and not re.search(r'\d', j['city'] or '') and len(j['city'] or '') < 26 else ''
    json.dump(GEO, open(GEO_FILE, 'w'), indent=0)
    return jobs


def crawl_employer(e):
    name, base = e['name'], e['base']
    links = workday_links(base)
    if links is None:
        _, sms = robots_for(base)
        cands = [s for s in sms if 'sitemap' in s.lower()] or [base.rstrip('/') + '/sitemap.xml']
        urls = []
        for sm in cands[:3]:
            urls = sitemap_urls(sm)
            if urls:
                break
        links = [u for u in urls if URL_HINT.search(urllib.parse.unquote(u))]
    jobs = []
    for u in links:
        if not allowed(u):
            continue
        jp = jobposting(get(u))
        if not jp:
            continue
        title = (jp.get('title') or '').strip()
        if not title or not INCLUDE.search(title) or EXCLUDE.search(title):
            continue
        city, st, lat, lon = place(jp)
        if not st:  # Workday's own search result lists the state / city next to the job id
            for b in WD_HINT.get(u, []):
                ab = b.upper() if b.upper() in ABBR else STATES.get(b.lower(), '')
                if ab:
                    st = ab
                elif not city or city == jp.get('title'):
                    city = b
        jobs.append({
            'id': hashlib.sha1(u.encode()).hexdigest()[:12], 'employer': name, 'title': title,
            'posted': (jp.get('datePosted') or '')[:10], 'expires': (jp.get('validThrough') or '')[:10] or None,
            'type': kind(jp.get('employmentType')), 'city': city, 'state': st, 'lat': lat, 'lon': lon, 'url': u,
            'raw': WD_LOC.get(u) or raw_locality(jp),
        })
    return jobs


def main():
    prev = {}
    try:
        prev = {j['id']: j for j in json.load(open(os.path.join(HERE, 'jobs.json')))['jobs']}
    except Exception:
        pass
    employers = [e for e in json.load(open(os.path.join(HERE, 'employers.json'))) if e.get('enabled', True)]
    if ONLY:
        employers = [e for e in employers if e['name'].lower() in ONLY]
    if LIMIT:
        employers = employers[:LIMIT]
    now = time.strftime('%Y-%m-%d', time.gmtime())
    jobs, stats = {}, collections.OrderedDict()
    for e in employers:
        try:
            got = crawl_employer(e)
        except Exception as ex:
            got = []
            print(f"{e['name']}: error {ex}", flush=True)
        # if an employer suddenly returns nothing, keep its previous jobs for one run instead of wiping them
        if not got:
            got = [j for j in prev.values() if j['employer'] == e['name']]
            for j in got:
                j['stale'] = True
        for j in got:
            old = prev.get(j['id'])
            j['firstSeen'] = old['firstSeen'] if old else now
            jobs[j['id']] = j
        stats[e['name']] = len(got)
        print(f"{e['name']:28} {len(got):>4}", flush=True)
    out = fill_places([j for j in jobs.values() if not (j['expires'] and j['expires'] < now)])
    out.sort(key=lambda j: (j['posted'] or '', j['firstSeen']), reverse=True)
    payload = {'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'count': len(out), 'employers': stats, 'jobs': out}
    json.dump(payload, open(os.path.join(HERE, 'jobs.json'), 'w'), indent=None, separators=(',', ':'))
    print(f'wrote {len(out)} jobs from {sum(1 for v in stats.values() if v)} employers')


if __name__ == '__main__':
    main()
