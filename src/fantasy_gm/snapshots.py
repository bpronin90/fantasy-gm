"""Offline snapshot boundary. No access to n8n or Yahoo."""
import copy
from datetime import datetime, timezone
import json
import math

TEAMS = set('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LAC LAR LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split())
POSITIONS = set('QB RB WR TE K DEF DL LB DB DT DE CB S'.split())
FLEX = {'FLEX': {'RB', 'WR', 'TE'}, 'SUPER_FLEX': {'QB', 'RB', 'WR', 'TE'}}
RESERVE = {'BN', 'IR', 'IR+'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timezone-qualified timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(snapshot):
    s = copy.deepcopy(snapshot)
    if len(json.dumps(s)) > 500000:
        raise ValueError('snapshot exceeds 500 KB')
    if s.get('schema_version') not in (1, 2) or s.get('platform') != 'yahoo':
        raise ValueError('unsupported snapshot')
    if not isinstance(s.get('league_id'), str) or not s['league_id'].strip():
        raise ValueError('league_id required')
    if s['schema_version'] == 2 and (not s.get('team_id') or s.get('season_type') not in ('REG', 'POST')):
        raise ValueError('v2 requires team_id and season_type')
    if type(s.get('season')) is not int or not 2000 <= s['season'] <= 2100:
        raise ValueError('invalid season')
    if type(s.get('current_week')) is not int or not 1 <= s['current_week'] <= 18:
        raise ValueError('invalid week')
    if type(s.get('is_sample')) is not bool:
        raise ValueError('sample flag required')
    timestamp(s['captured_at'])
    settings = s['league_settings']
    if type(settings.get('verified')) is not bool or not number(settings.get('ppr')) or not 0 <= settings['ppr'] <= 2:
        raise ValueError('invalid legacy settings')
    for value in settings.get('scoring', {}).values():
        if not number(value):
            raise ValueError('scoring coefficients must be finite numbers')
    slots = settings['roster_slots']
    if not slots:
        raise ValueError('slots required')
    for slot, count in slots.items():
        if slot not in POSITIONS | RESERVE | FLEX.keys() or type(count) is not int or not 0 <= count <= 40:
            raise ValueError('invalid slot capacity')
    if not isinstance(s.get('roster'), list) or not 0 < len(s['roster']) <= 100 or not isinstance(s.get('waiver_players'), list) or len(s['waiver_players']) > 1000:
        raise ValueError('invalid player lists')
    seen, counts, changes = set(), {}, []
    for group in ('roster', 'waiver_players'):
        for p in s[group]:
            if not isinstance(p.get('name'), str) or not 2 <= len(p['name'].strip()) <= 100 or any(ord(c) < 32 or c in '<>' for c in p['name']):
                raise ValueError('invalid name')
            for field in ('team', 'opponent'):
                if p.get(field) == 'WSH':
                    p[field] = 'WAS'
                    changes.append({'player': p['name'], 'field': field, 'from': 'WSH', 'to': 'WAS'})
            if p.get('team') not in TEAMS | {'FA'} or p.get('opponent') not in TEAMS | {None, 'BYE'}:
                raise ValueError('invalid team/opponent')
            if not p.get('positions') or not set(p['positions']) <= POSITIONS:
                raise ValueError('invalid positions')
            key = p.get('player_id') or p['name'].strip().casefold()
            if key in seen:
                raise ValueError('duplicate identity')
            seen.add(key)
            if group == 'roster':
                slot = p['slot']
                counts[slot] = counts.get(slot, 0) + 1
                if counts[slot] > slots.get(slot, 0):
                    raise ValueError('slot overflow or unconfigured slot')
                if slot not in RESERVE and not set(p['positions']) & FLEX.get(slot, {slot}):
                    raise ValueError('incompatible slot')
    prior = s.get('normalization', {}).get('changes', [])
    s['normalization'] = {'version': 1, 'changes': prior + changes}
    return s


def select(rows, request, scan_complete=True):
    """Caller must supply all bounded pages; never silently select a truncated scan."""
    for key in ('league_id', 'team_id', 'season', 'season_type', 'week', 'as_of', 'mode'):
        if key not in request:
            raise ValueError('missing request ' + key)
    if request['mode'] not in ('real', 'test') or not scan_complete:
        raise ValueError('invalid mode or incomplete snapshot scan')
    now = timestamp(request['as_of'])
    candidates = []
    for row in rows:
        try:
            s = validate(json.loads(row['snapshot_json']) if isinstance(row['snapshot_json'], str) else row['snapshot_json'])
            if any(s.get(k) != request[k] for k in ('league_id', 'team_id', 'season', 'season_type')) or s['current_week'] != request['week']:
                continue
            if request['mode'] == 'real' and (s['is_sample'] or row.get('is_sample') is not False):
                continue
            age = (now - timestamp(s['captured_at'])).total_seconds()
            if not -300 <= age <= 86400 or ('snapshot_row_id' in request and row['id'] != request['snapshot_row_id']):
                continue
            candidates.append((timestamp(s['captured_at']), int(row['id']), s))
        except (ValueError, KeyError, TypeError):
            continue
    if not candidates:
        raise ValueError('BLOCKED: no eligible exact-context snapshot')
    _, row_id, snapshot = max(candidates, key=lambda x: x[:2])
    return {'row_id': row_id, 'snapshot': snapshot}
