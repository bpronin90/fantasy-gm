"""P2.3 advisory scouting atop the existing frozen Research Pack and lineup search.

No Yahoo client, transaction, workflow call, ledger write or background execution.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse

from .research import build_pack, collect, digest, evidence
from .report import evaluate
from .snapshots import number, select, timestamp, validate

RULES = dict(teams=10, ppr=1, flex=2, acquisition_limit=5,
             waiver_type='continual_rolling', waiver_days=1)
OFFENSE = ('workload', 'usage_trend', 'role_depth_chart', 'injuries', 'matchup', 'future_schedule')
DEFENSE = ('opponent_quality', 'sacks_pressure', 'turnovers', 'ol_qb_injuries',
           'recent_performance', 'matchup_risks', 'future_schedule')
CONTEXT = ('league_id', 'team_id', 'season', 'season_type', 'week', 'mode')


def seal(pack):
    pack['pack_id'] = digest({k: v for k, v in pack.items() if k != 'pack_id'})
    return pack


def _validate_pool(snapshot):
    players = snapshot['roster'] + snapshot['waiver_players']
    ids = [p.get('player_id') for p in players]
    if any(not isinstance(pid, str) or not pid for pid in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique stable player_id required for scouting')
    keys = [('DEF', p['team'], snapshot['season']) if p['positions'] == ['DEF'] else ('GSIS', p['gsis_id'])
            for p in players if p.get('identity_verified') and (p['positions'] == ['DEF'] or p.get('gsis_id'))]
    if len(keys) != len(set(keys)): raise ValueError('duplicate public identity in scouting pool')
    return players


def build_scouting_pack(selection, request, artifacts, official_items, manual):
    """Research roster AND manually listed candidates without changing the snapshot."""
    request = copy.deepcopy(request)
    original = copy.deepcopy(selection)
    s = validate(original['snapshot']); original['snapshot'] = s
    if any(s[k] != request[k] for k in ('league_id', 'team_id', 'season', 'season_type')) or s['current_week'] != request['week']:
        raise ValueError('snapshot context mismatch')
    if request['mode'] == 'real' and s['is_sample']:
        raise ValueError('synthetic pack forbidden')
    players = _validate_pool(s)
    research_selection = copy.deepcopy(original)
    research_selection['snapshot']['roster'] = players
    pack = build_pack(research_selection, request, artifacts, official_items)
    schedule = artifacts.get('schedule', {}).get('rows', [])
    for player in players:
        pid = player['player_id']; team = player['team']
        games = [g for g in schedule if g['season'] == str(request['season']) and g['game_type'] == request['season_type'] and team in (g['home_team'], g['away_team'])]
        if artifacts.get('schedule'):
            outlook = {str(w): [g for g in games if int(g['week']) == w] for w in range(request['week'], min(request['week'] + 3, 19))}
            pack['evidence'].append(evidence(pid, 'schedule_outlook', outlook, artifacts['schedule'], artifacts['schedule']['fetched_at'], request['season'], request['week']))
        if player['positions'] != ['DEF']: continue
        upcoming = [g for g in games if int(g['week']) == request['week']]
        if len(upcoming) != 1: continue  # Existing schedule gap already blocks streaming.
        game = upcoming[0]
        opponent = game['away_team'] if team == game['home_team'] else game['home_team']
        completed = [g for g in schedule if g['season'] == str(request['season']) and g['game_type'] == request['season_type'] and opponent in (g['home_team'], g['away_team']) and int(g['week']) < request['week'] and g.get('home_score') not in ('', None) and g.get('away_score') not in ('', None)]
        expected = sorted(int(g['week']) for g in completed)
        artifact = artifacts.get('team_stats', {})
        history = sorted([r for r in artifact.get('rows', []) if r['season'] == str(request['season']) and r['season_type'] == request['season_type'] and r['team'] == opponent and int(r['week']) in expected], key=lambda r: int(r['week']))
        if not expected or [int(r['week']) for r in history] != expected:
            pack['coverage'][pid]['missing'].append('opponent latest-game statistics incomplete')
        fields = ('sacks_suffered', 'passing_interceptions', 'fumbles_lost_total', 'passing_epa')
        for row in history:
            g = next(g for g in completed if int(g['week']) == int(row['week']))
            pack['evidence'].append(evidence(pid, 'opponent_statistics', row, artifact, g['gameday'], request['season'], int(row['week'])))
        for field in fields:
            try:
                values = [float(r[field]) for r in history]
                if not values or any(not number(v) for v in values): raise ValueError('missing')
            except (ValueError, KeyError, TypeError):
                pack['coverage'][pid]['missing'].append('opponent indicator unavailable: ' + field)
                continue
            refs = [e['evidence_id'] for e in pack['evidence'] if e['subject'] == pid and e['kind'] == 'opponent_statistics']
            for window, subset in (('season', values), ('last3', values[-3:])):
                pack['derived'].append(dict(subject=pid, metric='opponent_' + field, window=window, value=sum(subset), games=len(subset), input_ids=refs if window == 'season' else refs[-3:], formula_version=1))
    pack.update(snapshot=original, snapshot_hash=digest(s), scouting=dict(schema_version=1, manual=copy.deepcopy(manual)))
    return seal(pack)


def _fresh(check, now, seconds=21600):
    try:
        return (check.get('verified') is True and bool(check.get('source')) and
                0 <= (now - timestamp(check['checked_at'])).total_seconds() <= seconds)
    except (ValueError, KeyError, TypeError):
        return False


def _context(value, req):
    return all(value.get(k) == req[k] for k in CONTEXT)


def availability(manual, pid, req):
    """Conflicting attestations stay unknown; never infer from roster percentages."""
    records = [x for x in manual.get('availability', []) if x.get('player_id') == pid]
    now = timestamp(req['as_of'])
    valid = [x for x in records if _context(x, req) and _fresh(x, now, 3600)]
    states = {x.get('status') for x in valid}
    if not valid or len(states) != 1 or not states <= {'free_agent', 'waivers', 'unavailable'}:
        return dict(status='unknown', reason='missing, stale, conflicting or wrong-context availability', records=records)
    state = next(iter(states))
    if state == 'waivers':
        clears = {x.get('clears_at') for x in valid}
        try:
            if len(clears) != 1 or timestamp(next(iter(clears))) <= now:
                raise ValueError('expired')
        except (ValueError, TypeError):
            return dict(status='unknown', reason='waiver clearance unknown or passed; recheck Yahoo', records=records)
    return dict(status=state, reason='manually confirmed at cutoff; recheck before action', records=valid)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _citations(refs, subjects, evidence):
    if not isinstance(refs, list) or not refs or any(r not in evidence or evidence[r]['subject'] not in subjects for r in refs):
        raise ValueError('invalid scouting citations')


def _assessment(pid, player, assessment, evidence, now):
    required = ('thesis', 'scoring_fit', 'confidence_reason', 'counterargument', 'risk', 'contingency', 'recheck_at')
    if (any(not _text(assessment.get(k)) for k in required) or
            assessment.get('confidence') not in ('low', 'medium', 'high') or
            not number(assessment.get('rank_score')) or not 0 <= assessment['rank_score'] <= 100):
        raise ValueError('incomplete scouting assessment: ' + pid)
    _citations(assessment.get('evidence_ids'), {pid}, evidence)
    for factor in DEFENSE if player['positions'] == ['DEF'] else OFFENSE:
        detail = assessment.get('factors', {}).get(factor, {})
        if not _text(detail.get('text')):
            raise ValueError('missing scouting factor: ' + pid + ': ' + factor)
        _citations(detail.get('evidence_ids'), {pid}, evidence)
    return timestamp(assessment['recheck_at']) > now


def scout(pack, review):
    if digest({k: v for k, v in pack.items() if k != 'pack_id'}) != pack['pack_id']:
        raise ValueError('pack hash mismatch')
    s = pack['snapshot']['snapshot']; req = pack['request']; now = timestamp(req['as_of'])
    if digest(s) != pack['snapshot_hash']: raise ValueError('snapshot hash mismatch')
    if req['mode'] == 'real' and s['is_sample']: raise ValueError('synthetic pack forbidden')
    manual = pack['scouting']['manual']; blockers = []
    if not 0 <= (now - timestamp(s['captured_at'])).total_seconds() <= 86400:
        blockers.append('snapshot stale or future dated')
    rules = manual.get('rules', {})
    if rules.get('values') != RULES or not _fresh(rules, now) or not _context(manual, req):
        blockers.append('league/weekly rules unverified or mismatched')
    if s['league_settings'].get('ppr') != 1 or s['league_settings']['roster_slots'].get('FLEX') != 2:
        blockers.append('snapshot must reflect full PPR and two FLEX')
    account = manual.get('account', {})
    used = account.get('acquisitions_used')
    remaining = 5 - used if (_context(account, req) and _fresh(account, now, 3600) and type(used) is int and 0 <= used <= 5) else None
    if remaining is None: blockers.append('confirmed weekly acquisitions remaining unknown')
    elif remaining == 0: blockers.append('weekly acquisition limit reached')
    priority = account.get('waiver_priority') if _context(account, req) and _fresh(account, now, 3600) else None
    if type(priority) is not int or not 1 <= priority <= 10: priority = None
    validate(s)
    players = {p['player_id']: p for p in _validate_pool(s)}
    roster = {p['player_id'] for p in s['roster']}
    candidates = set(players) - roster
    evidence = {e['evidence_id']: e for e in pack['evidence']}
    if len(evidence) != len(pack['evidence']): raise ValueError('duplicate evidence IDs')
    assessments = review.get('players', {})
    if set(assessments) - set(players): raise ValueError('assessment subject outside scouting pool')
    gaps = {}
    for pid, p in players.items():
        missing = list(pack['coverage'].get(pid, {}).get('missing', ['research coverage absent']))
        a = assessments.get(pid)
        if a is None: missing.append('assessment missing')
        elif not _assessment(pid, p, a, evidence, now): missing.append('assessment recheck passed')
        if not p.get('identity_verified'): missing.append('identity unverified')
        if not p.get('eligibility_verified') or not p.get('eligible_slots'): missing.append('Yahoo eligibility unknown')
        # Verify evidence dates again at consumption; a manual pack cannot hide stale facts.
        for e in evidence.values():
            if e['subject'] != pid: continue
            if e.get('season') != req['season'] or (e['kind'] not in ('statistics', 'opponent_statistics') and e.get('week') != req['week']):
                missing.append('wrong-context research')
            age = (now - timestamp(e['fetched_at'])).total_seconds()
            limit = 3600 if e['kind'] in ('schedule', 'schedule_outlook') else 86400 if e['kind'] in ('identity', 'statistics', 'opponent_statistics') else 21600
            if not 0 <= age <= limit: missing.append('stale/future research')
            if e['kind'] in ('injury', 'news', 'matchup') and not 0 <= (now - timestamp(e['observed_at'])).total_seconds() <= 21600:
                missing.append('stale observed research')
            if urlparse(e['source_url']).scheme != 'https': missing.append('invalid research URL')
        injuries = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'injury']
        if not injuries or any(e['value'].get('game_status') in (None, '', 'unknown') for e in injuries):
            missing.append('injury status unknown')
        if p['positions'] == ['DEF']:
            matchups = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'matchup']
            if not matchups or any(e['value'].get('ol_qb_injury_check') != 'verified' for e in matchups):
                missing.append('opponent OL/QB injuries unverified')
        schedules = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'schedule']
        if len(schedules) != 1: missing.append('schedule missing or conflicting')
        elif timestamp(schedules[0]['value']['kickoff']) <= now: missing.append('kickoff passed; add/drop lock review required')
        elif a and timestamp(a['recheck_at']) > timestamp(schedules[0]['value']['kickoff']): missing.append('recheck must precede kickoff')
        if p.get('opponent') == 'BYE': missing.append('bye; no immediate streaming value')
        if any(e['subject'] == pid and e['kind'] == 'injury' and e['value'].get('game_status') in ('out', 'inactive') for e in evidence.values()):
            missing.append('out/inactive; stash requires separate review')
        gaps[pid] = sorted(set(missing))
    outlook = review.get('roster_outlook', {})
    if any(not _text(outlook.get(k)) for k in ('strengths', 'weaknesses', 'positional_needs')):
        raise ValueError('roster strengths, weaknesses and positional needs required')
    _citations(outlook.get('evidence_ids'), roster, evidence)
    # Existing lineup rules/scoring/eligibility gates remain authoritative.
    base_pack = copy.deepcopy(pack)
    base_pack['coverage'] = {pid: pack['coverage'][pid] for pid in roster}
    baseline = evaluate(seal(base_pack), {pid: assessments[pid] for pid in roster if pid in assessments})
    blockers += baseline['blockers']
    blockers += [pid + ': ' + ', '.join(gaps[pid]) for pid in sorted(roster) if gaps[pid]]
    available = {pid: availability(manual, pid, req) for pid in candidates}
    def replacement_lineup(comparisons):
        drops = {c['drop'] for c in comparisons}
        adds = {c['add'] for c in comparisons}
        hypothetical = copy.deepcopy(pack)
        hypothetical['snapshot']['snapshot']['roster'] = [p for p in s['roster'] if p['player_id'] not in drops] + [dict(players[pid], slot='BN') for pid in sorted(adds)]
        hypothetical['snapshot']['snapshot']['waiver_players'] = []
        hypothetical['snapshot_hash'] = digest(hypothetical['snapshot']['snapshot'])
        ids = (roster - drops) | adds
        return evaluate(seal(hypothetical), {pid: assessments[pid] for pid in ids if pid in assessments})

    proposals = []
    seen = set()
    for comparison in review.get('comparisons', []):
        add, drop = comparison.get('add'), comparison.get('drop')
        if add not in candidates or drop not in roster or (add, drop) in seen:
            raise ValueError('unique candidate/roster add-drop pair required')
        seen.add((add, drop))
        for key in ('replacement_value', 'drop_cost', 'need_fit', 'contingency'):
            if not _text(comparison.get(key)): raise ValueError('reasoned comparison required: ' + key)
        _citations(comparison.get('evidence_ids'), {add, drop}, evidence)
        if not {add, drop} <= {evidence[r]['subject'] for r in comparison['evidence_ids']}:
            raise ValueError('comparison must cite both add and drop')
        penalty = comparison.get('drop_penalty')
        if not number(penalty) or not 0 <= penalty <= 100: raise ValueError('invalid drop penalty')
        reasons = list(gaps[add]) + list(gaps[drop])
        if drop not in manual.get('droppable_ids', []): reasons.append('drop permission/undroppable/lock status unverified')
        if available[add]['status'] == 'unavailable': reasons.append('confirmed unavailable')
        elif available[add]['status'] == 'unknown': reasons.append('availability unknown')
        elif available[add]['status'] == 'waivers':
            kickoff = next((e['value']['kickoff'] for e in evidence.values() if e['subject'] == add and e['kind'] == 'schedule'), None)
            if kickoff and timestamp(available[add]['records'][0]['clears_at']) >= timestamp(kickoff):
                reasons.append('waivers clear after kickoff; no immediate matchup value')
        # Check a full replacement lineup; never leave a unique eligible position uncovered.
        feasible = replacement_lineup([comparison])
        reasons += feasible['blockers']
        gain = None
        if add in assessments and drop in assessments:
            gain = assessments[add]['rank_score'] - assessments[drop]['rank_score'] - penalty
            if gain <= 0: reasons.append('replacement gain does not justify drop cost')
        proposals.append(dict(comparison, net_gain=gain, availability=available[add]['status'],
                              status='conditional' if reasons or blockers else 'review_required',
                              reasons=sorted(set(reasons)), lineup=feasible['lineups'][:1]))
    proposals.sort(key=lambda p: (-(p['net_gain'] if p['net_gain'] is not None else -1000), p['add'], p['drop']))
    # One conservative success path; shared drops/adds are mutually exclusive fallback claims.
    plan, used_ids = [], set()
    for i, p in enumerate(proposals):
        p['rank'] = i + 1
        p['conflicts_with'] = [j + 1 for j, q in enumerate(proposals) if j != i and (p['add'] == q['add'] or p['drop'] == q['drop'])]
        if p['status'] == 'review_required' and remaining is not None and len(plan) < remaining and not {p['add'], p['drop']} & used_ids:
            combined = replacement_lineup([proposals[rank - 1] for rank in plan] + [p])
            if combined['blockers']:
                p['reasons'].append('incompatible with earlier success-path claims; re-evaluate separately')
            else:
                plan.append(p['rank']); used_ids.update((p['add'], p['drop']))
    incumbent = [pid for pid in roster if players[pid]['positions'] == ['DEF']]
    defenses = []
    for pid, p in players.items():
        if p['positions'] != ['DEF']: continue
        a = assessments.get(pid)
        defenses.append(dict(player_id=pid, team=p['team'], score=a['rank_score'] if a else None,
                             availability='rostered' if pid in roster else available[pid]['status'],
                             gaps=gaps[pid], versus_incumbent={other: a['rank_score'] - assessments[other]['rank_score'] for other in incumbent if a and other in assessments}))
    defenses.sort(key=lambda d: (-(d['score'] if d['score'] is not None else -1), d['player_id']))
    return dict(status='blocked' if blockers else 'review_required', blockers=sorted(set(blockers)),
                remaining=remaining, waiver_priority=priority, rules=RULES, roster_outlook=outlook,
                proposals=proposals, success_path=plan, availability=available, defenses=defenses,
                candidate_gaps={pid: gaps[pid] for pid in sorted(candidates)}, review=review,
                warning='Advisory only. Recheck all claims and locks manually. Unknown priority means no claim-success guarantee. Rolling priority changes after successful claims; fallback claims are alternatives, not additional transactions.')


def render_scouting(pack, result):
    req = pack['request']; s = pack['snapshot']['snapshot']
    names = {p['player_id']: p['name'] for p in s['roster'] + s['waiver_players']}
    lines = [f"# Waiver and defensive-streaming GM report — {req['season']} {req['season_type']} week {req['week']}",
             f"Context: {req['league_id']} / {req['team_id']}; mode {req['mode']}; cutoff {req['as_of']}",
             f"Pack {pack['pack_id']}; snapshot SHA256 {pack['snapshot_hash']}",
             f"Status: {result['status']}. {result['warning']}",
             'Scores use a shared 0–100 Codex qualitative value rubric; they are not fantasy projections.',
             'Rules: ' + json.dumps(result['rules'], sort_keys=True),
             f"Confirmed acquisitions remaining: {result['remaining'] if result['remaining'] is not None else 'unknown'}; waiver priority: {result['waiver_priority'] or 'unknown'}",
             'Blockers: ' + ('; '.join(result['blockers']) or 'none')]
    for k in ('strengths', 'weaknesses', 'positional_needs'): lines.append(k + ': ' + result['roster_outlook'][k])
    lines.append('Manual success path (re-evaluate after each result): ' + str(result['success_path']))
    for pid, record in sorted(result['availability'].items()):
        lines.append(f"Availability — {names[pid]}: {record['status']}; {record['reason']}. Checks: " + json.dumps(record['records'], sort_keys=True))
    for p in result['proposals']:
        lines += [f"## {p['rank']}. Add {names[p['add']]} / drop {names[p['drop']]}",
                  f"{p['status']}; availability {p['availability']}; net rubric gain {p['net_gain']}",
                  'Conditions: ' + ('; '.join(p['reasons']) or 'human review and availability recheck'),
                  'Mutually exclusive alternatives: ' + str(p['conflicts_with'])]
        for k in ('replacement_value', 'drop_cost', 'drop_penalty', 'need_fit', 'contingency'): lines.append(k + ': ' + str(p[k]))
        lines.append('Feasible replacement lineup: ' + json.dumps(p['lineup']))
    lines += ['## Independent defense comparison', json.dumps(result['defenses'], indent=2),
              'Candidate research gaps: ' + json.dumps(result['candidate_gaps'], sort_keys=True)]
    for pid, a in sorted(result['review']['players'].items()):
        lines += [f"## {names[pid]} — score {a['rank_score']}; confidence {a['confidence']}"]
        for k in ('thesis', 'scoring_fit', 'confidence_reason', 'counterargument', 'risk', 'contingency', 'recheck_at'): lines.append(k + ': ' + a[k])
        for k, detail in a['factors'].items(): lines.append(k + ': ' + detail['text'] + ' [' + ', '.join(detail['evidence_ids']) + ']')
        lines.append('Usage: ' + json.dumps([d for d in pack['derived'] if d['subject'] == pid]))
    for e in pack['evidence']:
        lines.append(f"Evidence {e['evidence_id']} ({e['subject']}, {e['kind']}): {e['source_url']} — observed {e['observed_at']}; fetched {e['fetched_at']}")
    return '\n\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('pack')
    for name in ('rows', 'request', 'official', 'manual', 'output-dir'): p.add_argument('--' + name, required=True)
    p = sub.add_parser('report')
    for name in ('pack', 'review', 'output-dir'): p.add_argument('--' + name, required=True)
    args = parser.parse_args(); out = Path(args.output_dir).resolve(); root = Path(__file__).resolve().parents[2]
    if out != root / 'data/private' and root / 'data/private' not in out.parents and (out == root or root in out.parents):
        parser.error('repository outputs must be under ignored data/private/')
    def read(path): return json.loads(Path(path).read_text())
    out.mkdir(parents=True, exist_ok=True)
    if args.command == 'pack':
        req = read(args.request); rows = read(args.rows); selection = select(rows, req)
        if abs((datetime.now(timezone.utc) - timestamp(req['as_of'])).total_seconds()) > 300:
            raise ValueError('live collection requires a current cutoff within five minutes')
        kinds = ['players', 'schedule', 'stats']
        if any(p['positions'] == ['DEF'] for p in selection['snapshot']['roster'] + selection['snapshot']['waiver_players']): kinds.append('team_stats')
        artifacts = {k: collect(k, req['season'], datetime.now(timezone.utc).isoformat(), out / 'cache') for k in kinds}
        req['as_of'] = datetime.now(timezone.utc).isoformat(); selection = select(rows, req)
        pack = build_scouting_pack(selection, req, artifacts, read(args.official), read(args.manual))
        (out / 'scouting-pack.json').write_text(json.dumps(pack, indent=2))
        print('Scouting pack saved; review coverage before supplying cited assessments.')
    else:
        pack = read(args.pack); result = scout(pack, read(args.review))
        (out / 'scouting-evaluation.json').write_text(json.dumps(result, indent=2))
        (out / 'scouting-report.md').write_text(render_scouting(pack, result))
        print('Advisory report saved: ' + result['status'] + '; no transactions or live records written.')


if __name__ == '__main__': main()
