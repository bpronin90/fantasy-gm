"""Small, explicit CSV collectors and immutable evidence packs (stdlib only)."""
import csv
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlparse
from .snapshots import timestamp, number

SOURCES = {
    'players': 'https://github.com/nflverse/nflverse-data/releases/download/players/players.csv',
    'team_stats': 'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season}.csv',
    'stats': 'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv',
    'schedule': 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv',
    'snaps': 'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv',
}
REQUIRED = {'team_stats': {'team', 'season', 'week', 'season_type'}, 'players': {'gsis_id', 'display_name'}, 'stats': {'player_id', 'season', 'week', 'season_type', 'team', 'targets', 'carries'}, 'schedule': {'game_id', 'season', 'week', 'game_type', 'home_team', 'away_team', 'gameday', 'gametime'}, 'snaps': {'pfr_player_id', 'season', 'week', 'offense_snaps'}}
TEAM_ALIASES = {'LA': 'LAR'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def collect(kind, season, as_of, cache_dir, opener=urllib.request.urlopen):
    """Explicit user-invoked collection. Cache never hides an unsuccessful refresh."""
    url = SOURCES[kind].format(season=season)
    error = None
    for attempt in range(3):
        try:
            with opener(url, timeout=20) as response:
                body = response.read(20_000_001)
                if len(body) > 20_000_000:
                    raise ValueError('source exceeds 20 MB')
                meta = {'etag': response.headers.get('ETag'), 'last_modified': response.headers.get('Last-Modified')}
            rows = parse_csv(kind, body, season)
            artifact = dict(kind=kind, url=url, provider='nflverse', fetched_at=as_of, published_at=None, sha256=hashlib.sha256(body).hexdigest(), rows=rows, normalization={'version': 1, 'team_aliases': TEAM_ALIASES}, **meta)
            path = Path(cache_dir); path.mkdir(parents=True, exist_ok=True)
            (path / (kind + '-' + artifact['sha256'] + '.json')).write_text(json.dumps(artifact))
            return artifact
        except (OSError, ValueError) as exc:
            error = exc
            if attempt < 2: time.sleep(0.2 * (attempt + 1))
    raise ValueError(f'{kind} refresh failed; cached data not declared fresh: {error}')


def parse_csv(kind, body, season):
    reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
    if not REQUIRED[kind] <= set(reader.fieldnames or []):
        raise ValueError('missing columns: ' + kind)
    rows = list(reader)
    if not rows or any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError('empty or malformed CSV')
    # nflverse uses LA for the Rams; snapshots use LAR. Preserve the raw-body
    # hash in collect(), and canonicalize all source joins at this boundary.
    for row in rows:
        for field in ('team', 'opponent_team', 'home_team', 'away_team', 'latest_team', 'opponent'):
            if field in row:
                row[field] = TEAM_ALIASES.get(row[field], row[field])
    if kind != 'players':
        rows = [r for r in rows if r['season'] == str(season)]
        if not rows: raise ValueError('wrong season or unavailable season')
        if any(not r['week'].isdigit() or not 1 <= int(r['week']) <= 22 for r in rows):
            raise ValueError('invalid week')
    if kind == 'stats':
        for row in rows:
            for key in ('targets', 'carries'):
                value = float(row[key])
                if not number(value) or value < 0: raise ValueError('invalid usage')
    return rows


def evidence(subject, kind, value, artifact, observed_at, season, week, status='observed'):
    item = dict(subject=subject, kind=kind, value=value, source_url=artifact['url'], provider=artifact['provider'], published_at=artifact.get('published_at'), observed_at=observed_at, fetched_at=artifact['fetched_at'], season=season, week=week, revision=artifact['sha256'], status=status)
    item['evidence_id'] = digest(item)
    return item


def build_pack(selection, request, artifacts, official_items=()):
    """Never name-match a player: require explicitly verified GSIS/Yahoo crosswalk."""
    s = selection['snapshot']; now = timestamp(request['as_of'])
    items, coverage, derived = [], {}, []
    schedule = artifacts.get('schedule', {}).get('rows', [])
    stats = artifacts.get('stats', {}).get('rows', [])
    identities = artifacts.get('players', {}).get('rows', [])
    for p in s['roster']:
        pid = p.get('player_id', p['name'])
        gaps = []
        for kind, artifact in artifacts.items():
            if timestamp(artifact['fetched_at']) > now: gaps.append(kind + ' fetched after cutoff')
        gsis = p.get('gsis_id')
        defense = p['positions'] == ['DEF']
        matches = ([{'team': p['team'], 'season': s['season'], 'stable_key': f"DEF:{p['team']}:{s['season']}"}] if defense else [r for r in identities if r['gsis_id'] == gsis] if gsis else [])
        identity_artifact = artifacts.get('schedule' if defense else 'players')
        if len(matches) != 1 or not p.get('identity_verified') or not identity_artifact:
            gaps.append('identity')
        else:
            items.append(evidence(pid, 'identity', matches[0], identity_artifact, identity_artifact['fetched_at'], s['season'], request['week']))
            if not 0 <= (now - timestamp(identity_artifact['fetched_at'])).total_seconds() <= 86400: gaps.append('identity freshness')
        games = [r for r in schedule if r['season'] == str(s['season']) and r['game_type'] == s['season_type'] and p['team'] in (r['home_team'], r['away_team'])]
        upcoming = [g for g in games if int(g['week']) == request['week']]
        if len(upcoming) != 1:
            gaps.append('schedule/bye')
        else:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            g = dict(upcoming[0])
            try:
                g['kickoff'] = datetime.fromisoformat(g['gameday'] + 'T' + g['gametime']).replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
                items.append(evidence(pid, 'schedule', g, artifacts['schedule'], g['kickoff'], s['season'], request['week']))
                age = (now - timestamp(artifacts['schedule']['fetched_at'])).total_seconds()
                if not 0 <= age <= 3600: gaps.append('schedule freshness')
            except ValueError: gaps.append('unknown kickoff')
        completed = [g for g in games if int(g['week']) < request['week'] and g.get('home_score') not in (None, '') and g.get('away_score') not in (None, '')]
        expected = sorted(int(g['week']) for g in completed)
        player_stats = artifacts.get('team_stats', {}).get('rows', []) if defense else stats
        stats_artifact = artifacts.get('team_stats' if defense else 'stats', {})
        history = [r for r in player_stats if r['season'] == str(s['season']) and r['season_type'] == s['season_type'] and (r.get('team') == p['team'] if defense else r['player_id'] == gsis) and int(r['week']) in expected] if len(matches) == 1 else []
        available = sorted(int(r['week']) for r in history)
        if not expected or available != expected: gaps.append('latest-game statistics incomplete (DNP not inferred)')
        ids = []
        for row in history:
            game = next(g for g in completed if int(g['week']) == int(row['week']))
            item = evidence(pid, 'statistics', row, stats_artifact, game['gameday'], s['season'], int(row['week']))
            items.append(item); ids.append(item['evidence_id'])
        history.sort(key=lambda r: int(r['week']))
        for field in (() if defense else ('targets', 'carries')):
            for window, subset in (('season', history), ('last3', history[-3:])):
                numerator = sum(float(r[field]) for r in subset)
                denominator = sum(float(r[field]) for r in stats if r['season'] == str(s['season']) and r['season_type'] == s['season_type'] and any(r['team'] == x['team'] and r['week'] == x['week'] for x in subset))
                derived.append(dict(subject=pid, metric=field + '_share', window=window, value=numerator / denominator if denominator else None, numerator=numerator, denominator=denominator, games=len(subset), input_ids=ids, formula_version=1, denominator_source_revision=artifacts.get('stats', {}).get('sha256')))
        injuries = []
        for item in official_items:
            if item.get('subject') != pid: continue
            parsed = urlparse(item['source_url'])
            if parsed.scheme != 'https' or not (parsed.hostname == 'nfl.com' or (parsed.hostname or '').endswith('.nfl.com') or item.get('manually_verified_official') is True):
                raise ValueError('injury/news must cite manually verified official source')
            if item['kind'] not in ('injury', 'news', 'matchup'): raise ValueError('invalid manual evidence kind')
            age = (now - timestamp(item['observed_at'])).total_seconds()
            if not 0 <= age <= 21600 or timestamp(item['fetched_at']) > now or item['season'] != s['season'] or item['week'] != request['week']:
                gaps.append('stale/wrong-context official evidence'); continue
            frozen = dict(item); frozen['evidence_id'] = digest(item); items.append(frozen)
            if item['kind'] == 'injury': injuries.append(item)
        if not injuries: gaps.append('injury unknown')
        if len({json.dumps(i['value'], sort_keys=True) for i in injuries}) > 1: gaps.append('injury conflict')
        if not any(i['subject'] == pid and i['kind'] == 'matchup' for i in items): gaps.append('matchup context missing')
        coverage[pid] = dict(missing=gaps, expected_weeks=expected, available_weeks=available, snaps='not collected', routes='unavailable')
    pack = dict(schema_version=2, request=request, snapshot=selection, snapshot_hash=digest(s), evidence=items, derived=derived, coverage=coverage, constraints=dict(advisory_only=True, automatic_transactions=False, paid_ai_apis=False), freshness_policy=dict(snapshot_seconds=86400, identity_seconds=86400, schedule_seconds=3600, injury_seconds=21600), settings_status=s['league_settings'], generated_at=request['as_of'])
    pack['pack_id'] = digest(pack)
    return pack
