"""Append-only retrospective calculations. Results never enter the reasoning view."""
import argparse
import copy
import json
from pathlib import Path
import sqlite3
from .research import collect, digest
from .snapshots import timestamp, number, RESERVE

VERSION = 1
CONTEXT = ('league_id', 'team_id', 'season', 'season_type', 'week', 'mode')


def require_private_ledger(parser, path):
    """Private ledgers (and the cache beside them) stay in ignored data/private/ or outside the repo."""
    out = Path(path).resolve(); root = Path(__file__).resolve().parents[2]; private = root / 'data/private'
    if private not in out.parents and (out == root or root in out.parents):
        parser.error('a ledger inside the repository must be under ignored data/private/')
    return str(out)  # callers use the resolved path, so a symlink cannot redirect the cache


def load_decision(path, decision_id):
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM decisions WHERE decision_id=?', (decision_id,)).fetchone()
    if not row: raise ValueError('decision not found')
    record = json.loads(row[0])
    pack = record['pack']
    if digest({k: v for k, v in pack.items() if k != 'pack_id'}) != pack['pack_id']:
        raise ValueError('historical pack hash mismatch')
    if digest(pack['snapshot']['snapshot']) != pack['snapshot_hash']:
        raise ValueError('historical snapshot hash mismatch')
    # Decisions recorded before record_hash existed cannot be verified beyond pack and
    # snapshot; outcomes label them legacy_unverified.
    if 'record_hash' in record and digest({k: v for k, v in record.items() if k != 'record_hash'}) != record['record_hash']:
        raise ValueError('historical decision hash mismatch')
    return record


def available_at_cutoff(item, cutoff):
    """Schedule event time may be future; collection/publication may never be."""
    try:
        if timestamp(item['fetched_at']) > cutoff: return False
        if item.get('published_at') and timestamp(item['published_at']) > cutoff: return False
        if item['kind'] != 'schedule' and timestamp(item['observed_at']) > cutoff: return False
        return True
    except (KeyError, ValueError, TypeError):
        return False


def reasoning_view(record):
    pack = record['pack']; cutoff = timestamp(pack['request']['as_of'])
    evidence = [e for e in pack['evidence'] if available_at_cutoff(e, cutoff)]
    return dict(decision_hash=digest(record), cutoff=pack['request']['as_of'],
                request=pack['request'], snapshot=pack['snapshot'], coverage=pack['coverage'],
                assessments=record['evaluation']['assessments'],
                assignments=record['evaluation']['lineups'][0]['assignments'],
                evidence=evidence, excluded_evidence_ids=[e['evidence_id'] for e in pack['evidence'] if e not in evidence])


def judge_reasoning(record, review=None):
    view = reasoning_view(record)
    gaps = list(record['evaluation'].get('blockers', []))
    gaps += ['post-cutoff or undated evidence: ' + i for i in view['excluded_evidence_ids']]
    for pid, coverage in view['coverage'].items():
        gaps += [pid + ': ' + g for g in coverage['missing']]
    if record.get('recommendation_status') == 'provisional':
        gaps.append('provisional archive does not establish a verified pregame decision')
    judgment = 'insufficient'
    if review:
        if review.get('view_hash') != digest(view) or review.get('cutoff_only') is not True or review.get('blinded_to_results') is not True:
            raise ValueError('reasoning review must attest a frozen, results-blind cutoff-only view')
        if review.get('judgment') not in ('good', 'poor', 'insufficient') or not isinstance(review.get('rationale'), str) or not review['rationale'].strip():
            raise ValueError('reasoning judgment and rationale required')
        refs = review.get('evidence_ids', [])
        if not refs or not set(refs) <= {e['evidence_id'] for e in view['evidence']}:
            raise ValueError('reasoning citations outside original cutoff')
        if any(not isinstance(t, str) or not t.strip() for t in review.get('error_tags', [])):
            raise ValueError('invalid reasoning error tags')
        judgment = review['judgment'] if not gaps else 'insufficient'
    return dict(judgment=judgment, basis='user-initiated cutoff-only review' if review else 'semantic reasoning review not supplied',
                gaps=sorted(set(gaps)), review=review, view_hash=digest(view))


def fantasy_points(stats, settings, positions=None):
    """Recorded exact-column coefficients, cumulative bonuses and interval rules.

    No provider fantasy totals, inferred zeros, rounding rules, or Yahoo defaults.
    Unsupported/missing columns block that player's score.
    """
    if positions is not None:
        scopes = [settings['scoring_by_position'][p] for p in positions if p in settings.get('scoring_by_position', {})]
        if len(scopes) > 1: raise ValueError('ambiguous positional scoring')
        if scopes:
            scope = scopes[0]
            if not isinstance(scope, dict) or not {'scoring', 'missing_rules'} <= set(scope): raise ValueError('incomplete positional scoring')
            settings = dict(settings, scoring=scope['scoring'], missing_rules=scope['missing_rules'], scoring_bonuses=scope.get('scoring_bonuses', []))
        elif set(positions) & {'DEF', 'K'}:
            raise ValueError('explicit DEF/K scoring scope required; offensive team totals forbidden')
    rules = settings.get('scoring', {})
    if not rules or settings.get('missing_rules', ['unknown']): raise ValueError('scoring incomplete')
    def stat(key):
        if key in ('fantasy_points', 'fantasy_points_ppr'): raise ValueError('provider fantasy totals forbidden')
        raw = stats.get(key)
        if raw is None or raw == '' or isinstance(raw, bool): raise ValueError('missing statistic: ' + key)
        try: value = float(raw)
        except (TypeError, ValueError): raise ValueError('invalid statistic: ' + key)
        if not number(value): raise ValueError('invalid statistic: ' + key)
        return value
    total = 0
    for key, coefficient in rules.items():
        if not number(coefficient): raise ValueError('invalid scoring coefficient')
        if coefficient: total += stat(key) * coefficient
    for bonus in settings.get('scoring_bonuses', []):
        if set(bonus) != {'stat', 'min', 'max', 'points'} or not number(bonus['min']) or not number(bonus['points']) or (bonus['max'] is not None and (not number(bonus['max']) or bonus['max'] <= bonus['min'])):
            raise ValueError('unsupported bonus rule')
        value = stat(bonus['stat'])
        if value >= bonus['min'] and (bonus['max'] is None or value < bonus['max']): total += bonus['points']
    return round(total, 10)


def historical_options(record):
    """Build legality only from frozen roster, eligibility, injury and kickoff data."""
    pack = record['pack']; s = pack['snapshot']['snapshot']; cutoff = timestamp(pack['request']['as_of'])
    slots = [f'{k}:{i+1}' for k, n in s['league_settings']['roster_slots'].items() if k not in RESERVE for i in range(n)]
    if not slots: raise ValueError('no historical active lineup slots')
    players = {p.get('player_id', p['name']): p for p in s['roster']}
    if len(players) != len(s['roster']): raise ValueError('ambiguous historical identities')
    evidence = reasoning_view(record)['evidence']; fixed = {}; excluded = set(); gaps = []
    for pid, p in players.items():
        if not p.get('eligible_slots') or not p.get('eligibility_verified'): gaps.append(pid + ': eligibility unverified')
        injury_statuses = {e['value'].get('game_status') for e in evidence if e['subject'] == pid and e['kind'] == 'injury'}
        if len(injury_statuses) > 1: gaps.append(pid + ': conflicting historical injury reports')
        games = [e for e in evidence if e['subject'] == pid and e['kind'] == 'schedule']
        if p.get('opponent') == 'BYE': excluded.add(pid); continue
        if len(games) != 1:
            gaps.append(pid + ': kickoff unknown'); continue
        if timestamp(games[0]['value']['kickoff']) <= cutoff:
            if p['slot'] in RESERVE: excluded.add(pid)
            else:
                candidates = [k for k in slots if k.split(':')[0] == p['slot'] and k not in fixed]
                if not candidates: raise ValueError('invalid historical locked slots')
                fixed[candidates[0]] = pid
        elif p['slot'] in ('IR', 'IR+'):
            excluded.add(pid)
        elif any(e['subject'] == pid and e['kind'] == 'injury' and e['value'].get('game_status') in ('out', 'inactive') for e in evidence):
            excluded.add(pid)
    return slots, players, fixed, excluded, gaps


def legal(assignments, slots, players, fixed, excluded):
    return (set(assignments) == set(slots) and len(set(assignments.values())) == len(slots)
            and all(pid in players and pid not in excluded and slot.split(':')[0] in players[pid].get('eligible_slots', []) for slot, pid in assignments.items())
            and all(assignments.get(slot) == pid for slot, pid in fixed.items())
            and all(pid not in fixed.values() or fixed.get(slot) == pid for slot, pid in assignments.items()))


def best_lineup(slots, players, fixed, excluded, points):
    ids = sorted(players); bits = {pid: 1 << i for i, pid in enumerate(ids)}
    states = {0: (0, {})}
    for slot in slots:
        next_states = {}
        for mask, (score, assignment) in states.items():
            for pid in ([fixed[slot]] if slot in fixed else ids):
                if pid in excluded or mask & bits[pid] or pid not in points: continue
                if slot.split(':')[0] not in players[pid].get('eligible_slots', []): continue
                if pid in fixed.values() and fixed.get(slot) != pid: continue
                new = (score + points[pid], dict(assignment, **{slot: pid})); key = mask | bits[pid]
                if key not in next_states or new[0] > next_states[key][0]: next_states[key] = new
        if len(next_states) > 100000: raise ValueError('historical lineup search exceeds limit')
        states = next_states
    if not states: return None
    score, assignments = max(states.values(), key=lambda v: v[0])
    return dict(points=round(score, 10), assignments=assignments)


def actual_points(record, artifacts, as_of, participation=()):
    """Join exact identities/week. Missing row is unknown; explicit sourced DNP is zero."""
    req = record['pack']['request']; s = record['pack']['snapshot']['snapshot']
    points, details, gaps = {}, {}, []
    gsis_ids = [p['gsis_id'] for p in s['roster'] if p.get('gsis_id') and p['positions'] != ['DEF']]
    if len(gsis_ids) != len(set(gsis_ids)): raise ValueError('ambiguous historical GSIS mapping')
    for kind, art in artifacts.items():
        age = (timestamp(as_of) - timestamp(art['fetched_at'])).total_seconds()
        if not 0 <= age <= 86400: raise ValueError('stale/future outcome artifact: ' + kind)
        if not art.get('sha256') or not art.get('url'): raise ValueError('outcome source provenance required')
    schedule = artifacts.get('schedule', {}).get('rows', [])
    for p in s['roster']:
        pid = p.get('player_id', p['name']); defense = p['positions'] == ['DEF']
        games = [g for g in schedule if str(g.get('season')) == str(req['season']) and str(g.get('week')) == str(req['week']) and g.get('game_type') == req['season_type'] and p['team'] in (g['home_team'], g['away_team'])]
        rows = [r for r in artifacts.get('team_stats' if defense else 'stats', {}).get('rows', []) if str(r.get('season')) == str(req['season']) and str(r.get('week')) == str(req['week']) and r.get('season_type') == req['season_type'] and (r.get('team') == p['team'] if defense else r.get('player_id') == p.get('gsis_id') and p.get('gsis_id'))]
        complete = len(games) == 1 and (games[0].get('status') == 'final' or (games[0].get('result') not in (None, '') and games[0].get('home_score') not in (None, '') and games[0].get('away_score') not in (None, '')))
        if complete:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            try:
                g = games[0]
                kickoff = datetime.fromisoformat(g['gameday']+'T'+g['gametime']).replace(tzinfo=ZoneInfo('America/New_York'))
                complete = kickoff <= timestamp(as_of) and all(number(float(g[k])) for k in ('home_score', 'away_score'))
            except (ValueError, KeyError, TypeError): complete = False
        status = 'incomplete_game' if not complete else 'missing_statistics'
        detail = dict(status=status, rows=rows, games=games)
        if complete and p.get('identity_verified'):
            dnp = [e for e in participation if e.get('subject') == pid and e.get('game_id') == games[0]['game_id'] and e.get('season') == req['season'] and e.get('week') == req['week'] and e.get('season_type') == req['season_type'] and e.get('status') == 'dnp' and e.get('source_url', '').startswith('https://') and e.get('revision') and timestamp(e['fetched_at']) <= timestamp(as_of)]
            if rows and dnp:
                detail['status'] = 'conflicting_dnp_statistics'
            elif not rows and len(dnp) == 1 and not defense:
                points[pid] = 0; detail.update(status='sourced_dnp', participation=dnp)
            elif len(rows) == 1:
                try:
                    points[pid] = fantasy_points(rows[0], s['league_settings'], p['positions']); detail['status'] = 'scored'
                except ValueError as exc: detail['status'] = str(exc)
            elif len(rows) > 1: detail['status'] = 'ambiguous_statistics'
        elif not p.get('identity_verified'): detail['status'] = 'identity_unverified'
        if pid not in points: gaps.append(pid + ': ' + detail['status'])
        detail['points'] = points.get(pid); details[pid] = detail
    return points, details, gaps


def evaluate_outcome(record, decision_id, artifacts, as_of, review=None, participation=()):
    if timestamp(as_of) < timestamp(record['created_at']): raise ValueError('outcome predates decision recording')
    slots, players, fixed, excluded, gaps = historical_options(record)
    points, details, stat_gaps = actual_points(record, artifacts, as_of, participation)
    recommendation = record['evaluation']['lineups'][0]['assignments']
    baseline = {}; counts = {}
    for pid, p in players.items():
        if p['slot'] in RESERVE: continue
        counts[p['slot']] = counts.get(p['slot'], 0) + 1
        baseline[f"{p['slot']}:{counts[p['slot']]}"] = pid
    valid = legal(recommendation, slots, players, fixed, excluded)
    if not valid: gaps.append('recorded recommendation violates historical lineup constraints')
    relevant = {pid for pid, p in players.items() if pid not in excluded and (any(slot.split(':')[0] in p.get('eligible_slots', []) for slot in slots))}
    gaps += [g for g in stat_gaps if g.split(': ', 1)[0] in relevant]
    settings = record['pack']['snapshot']['snapshot']['league_settings']
    uncertainty = []
    for key in ('scoring', 'eligibility', 'locks'):
        check = settings.get('verification', {}).get(key, {})
        if check.get('verified') is not True or not check.get('source') or not check.get('checked_at') or timestamp(check['checked_at']) > timestamp(record['pack']['request']['as_of']):
            uncertainty.append(key + ' manually supplied/unverified')
    if 'receptions' in settings.get('scoring', {}) and settings.get('ppr') != settings['scoring']['receptions']:
        uncertainty.append('legacy PPR value conflicts with recorded reception coefficient; coefficient used')
    if record.get('recommendation_status') == 'provisional': uncertainty.append('provisional archive; original settings/adoption not independently verified')
    if not settings.get('scoring') or settings.get('missing_rules', ['unknown']): gaps.append('scoring incomplete')
    best = best_lineup(slots, players, fixed, excluded, points) if not gaps else None
    recommended = round(sum(points[p] for p in recommendation.values()), 10) if valid and all(p in points for p in recommendation.values()) else None
    baseline_points = round(sum(points[p] for p in baseline.values()), 10) if legal(baseline, slots, players, fixed, excluded) and all(p in points for p in baseline.values()) else None
    regret = round(best['points'] - recommended, 10) if best and recommended is not None else None
    reasoning = judge_reasoning(record, review)
    outcome = 'good' if regret == 0 else 'bad' if regret is not None else None
    classification = f"{reasoning['judgment']} decision, {outcome} outcome" if reasoning['judgment'] != 'insufficient' and outcome and not uncertainty else 'insufficient evidence to judge'
    return dict(record_type='outcome', evaluator_version=VERSION, decision_id=decision_id, decision_hash=digest(record),
                decision_integrity='verified' if 'record_hash' in record else 'legacy_unverified',
                context={k: record['pack']['request'][k] for k in CONTEXT}, as_of=as_of,
                status='incomplete' if gaps else 'conditional' if uncertainty else 'complete', gaps=sorted(set(gaps)), uncertainty=uncertainty,
                recommended_points=recommended, recorded_baseline_points=baseline_points,
                points_gained_lost=round(recommended-baseline_points, 10) if recommended is not None and baseline_points is not None else None,
                best_feasible=best, lineup_regret=regret, classification=classification, reasoning=reasoning, actuals=details,
                scoring=copy.deepcopy(settings), sources={kind: copy.deepcopy({k: v for k, v in art.items() if k != 'rows'}) for kind, art in artifacts.items()}, participation=copy.deepcopy(list(participation)),
                boundaries=dict(lineup='recorded recommendation; Yahoo submission unknown', alternatives='recorded roster only; waiver availability unknown', settings='recorded settings; verification is an original attestation, not a new Yahoo check'))


def _outcome_content(result):
    content = copy.deepcopy(result)
    for field in ('as_of', 'outcome_id', 'supersedes', 'revision_state'): content.pop(field, None)
    for art in content['sources'].values(): art.pop('fetched_at', None)
    return content


def append_outcome(path, result):
    # Compare the latest content, so a correction A -> B -> A appends a revision.
    content_hash = digest(_outcome_content(result))
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM decisions WHERE decision_id=?', (result['decision_id'],)).fetchone()
        if not row or digest(json.loads(row[0])) != result['decision_hash']: raise ValueError('linked immutable decision mismatch')
        db.execute('CREATE TABLE IF NOT EXISTS outcomes (sequence INTEGER PRIMARY KEY AUTOINCREMENT, outcome_id TEXT UNIQUE NOT NULL, decision_id TEXT NOT NULL, payload TEXT NOT NULL)')
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_outcomes_{operation} BEFORE {operation} ON outcomes BEGIN SELECT RAISE(ABORT, 'immutable outcome'); END")
        previous = db.execute('SELECT outcome_id, payload FROM outcomes WHERE decision_id=? ORDER BY sequence DESC LIMIT 1', (result['decision_id'],)).fetchone()
        if previous:
            original = json.loads(previous[1])
            if digest(_outcome_content(original)) == content_hash: return previous[0]
            if timestamp(result['as_of']) < timestamp(original['as_of']): raise ValueError('outcome revision predates latest evaluation')
        parent = previous[0] if previous else None
        key = digest(dict(content_hash=content_hash, supersedes=parent))
        payload = dict(result, outcome_id=key, supersedes=parent,
                       revision_state='reassessment' if previous else 'initial')
        db.execute('INSERT INTO outcomes(outcome_id,decision_id,payload) VALUES (?,?,?)', (key, result['decision_id'], json.dumps(payload, sort_keys=True, allow_nan=False)))
    return key


def history(path, request):
    """Latest evaluation per decision, same league/team, strictly available by cutoff."""
    cutoff = timestamp(request['as_of']); latest = {}
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='outcomes'").fetchone(): return dict(evaluations=[], recurring_errors={}, evidence_gaps={}, confidence_patterns={})
        for row in db.execute('SELECT payload FROM outcomes ORDER BY sequence'):
            item = json.loads(row[0]); ctx = item['context']
            if all(ctx[k] == request[k] for k in ('league_id', 'team_id', 'mode')) and timestamp(item['as_of']) <= cutoff:
                latest[item['decision_id']] = item
        errors, gaps, confidence = {}, {}, {}
        for item in latest.values():
            review = item['reasoning']['review'] or {}
            for tag in set(review.get('error_tags', [])): errors[tag] = errors.get(tag, 0) + 1
            for gap in set(item['reasoning']['gaps']): gaps[gap] = gaps.get(gap, 0) + 1
            decision = json.loads(db.execute('SELECT payload FROM decisions WHERE decision_id=?', (item['decision_id'],)).fetchone()[0])
            for pid in decision['evaluation']['lineups'][0]['assignments'].values():
                level = decision['evaluation']['assessments'][pid]['confidence']
                bucket = confidence.setdefault(level, dict(recommended_player_observations=0, complete_decisions=0, decisions_with_regret=0))
                bucket['recommended_player_observations'] += 1
            for level in {decision['evaluation']['assessments'][p]['confidence'] for p in decision['evaluation']['lineups'][0]['assignments'].values()}:
                confidence[level]['complete_decisions'] += int(item['lineup_regret'] is not None)
                confidence[level]['decisions_with_regret'] += int((item['lineup_regret'] or 0) > 0)
    return dict(evaluations=list(latest.values()), recurring_errors=errors, evidence_gaps=gaps, confidence_patterns=confidence,
                limitation='Descriptive counts, not causal calibration or automatic learning; repeated decisions may overlap.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True); parser.add_argument('--decision-id', required=True)
    parser.add_argument('--as-of', required=True); parser.add_argument('--artifacts'); parser.add_argument('--review'); parser.add_argument('--participation')
    parser.add_argument('--reasoning-view', action='store_true')
    args = parser.parse_args(); args.ledger = require_private_ledger(parser, args.ledger)
    record = load_decision(args.ledger, args.decision_id)
    if args.reasoning_view: print(json.dumps(reasoning_view(record), indent=2)); return
    read = lambda p: json.loads(Path(p).read_text())
    if args.artifacts:
        artifacts = read(args.artifacts)
    else:
        from datetime import datetime, timezone
        if abs((datetime.now(timezone.utc)-timestamp(args.as_of)).total_seconds()) > 300: raise ValueError('live results collection requires current as-of')
        artifacts = {}
        for kind in ('stats', 'schedule', 'team_stats'):
            art = collect(kind, record['pack']['request']['season'], datetime.now(timezone.utc).isoformat(), Path(args.ledger).parent/'cache')
            art['fetched_at'] = datetime.now(timezone.utc).isoformat()
            artifacts[kind] = art
        args.as_of = datetime.now(timezone.utc).isoformat()
    result = evaluate_outcome(record, args.decision_id, artifacts, args.as_of, read(args.review) if args.review else None, read(args.participation) if args.participation else ())
    print(json.dumps(dict(outcome_id=append_outcome(args.ledger, result), status=result['status'], lineup_regret=result['lineup_regret'], classification=result['classification'])))

if __name__ == '__main__': main()
