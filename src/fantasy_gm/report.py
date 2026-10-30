"""Feasible lineup search using user-initiated Codex assessments, never projections."""
import json
from pathlib import Path
import sqlite3
from .snapshots import timestamp, RESERVE, number
from .research import digest


def evaluate(pack, assessments):
    s = pack['snapshot']['snapshot']; req = pack['request']; now = timestamp(req['as_of'])
    if req['mode'] == 'real' and s['is_sample']: raise ValueError('synthetic pack forbidden')
    if digest({k: v for k, v in pack.items() if k != 'pack_id'}) != pack['pack_id']: raise ValueError('pack hash mismatch')
    settings = s['league_settings']
    blockers = []
    # A verified section needs an attributable check, not just a legacy boolean.
    for section in ('scoring', 'eligibility', 'locks'):
        check = settings.get('verification', {}).get(section, {})
        if check.get('verified') is not True or not check.get('source') or not check.get('checked_at'):
            blockers.append(section + ' unverified')
        elif timestamp(check['checked_at']) > now:
            blockers.append(section + ' verification is future dated')
    if not settings.get('scoring') or settings.get('missing_rules', ['unknown']): blockers.append('scoring incomplete')
    slots = [f'{slot}:{i+1}' for slot, n in settings['roster_slots'].items() if slot not in RESERVE for i in range(n)]
    players = {p.get('player_id', p['name']): p for p in s['roster']}
    evidence = {e['evidence_id']: e for e in pack['evidence']}
    required = ('thesis', 'confidence_reason', 'counterargument', 'risk', 'scoring_fit', 'contingency', 'recheck_at')
    usable, locked, locked_bench = {}, {}, set()
    for pid, p in players.items():
        schedules = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'schedule']
        kickoff = schedules[0]['value']['kickoff'] if len(schedules) == 1 else None
        if kickoff and timestamp(kickoff) <= now:
            if p['slot'] in RESERVE: locked_bench.add(pid)
            else: locked.setdefault(p['slot'], []).append(pid)
        gaps = pack['coverage'][pid]['missing']
        if gaps: blockers.append(pid + ': ' + ', '.join(gaps))
        a = assessments.get(pid)
        if not a:
            blockers.append(pid + ': assessment missing'); continue
        if any(not isinstance(a.get(k), str) or not a[k].strip() for k in required) or a.get('confidence') not in ('low', 'medium', 'high') or not number(a.get('rank_score')):
            raise ValueError('incomplete reasoned assessment: ' + pid)
        refs = a.get('evidence_ids', [])
        if not refs or any(ref not in evidence or evidence[ref]['subject'] != pid for ref in refs): raise ValueError('invalid assessment citations')
        if timestamp(a['recheck_at']) <= now or (kickoff and timestamp(a['recheck_at']) > timestamp(kickoff)):
            blockers.append(pid + ': no actionable recheck deadline')
        if not p.get('eligibility_verified') or not p.get('eligible_slots'): blockers.append(pid + ': Yahoo eligibility unknown')
        if p['slot'] in ('IR', 'IR+') or pid in locked_bench or p.get('opponent') == 'BYE': continue
        injury = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'injury']
        if any(e['value'].get('game_status') in ('out', 'inactive') for e in injury): continue
        usable[pid] = a
    fixed = {}
    for slot, ids in locked.items():
        matching = [k for k in slots if k.split(':')[0] == slot]
        if len(ids) > len(matching): raise ValueError('invalid locked assignments')
        fixed.update(zip(matching, ids))
    # Dynamic programming over selected-player bitsets avoids factorial permutations.
    ids = sorted(players); bits = {pid: 1 << i for i, pid in enumerate(ids)}
    states = {0: (0, {}, 0)}
    for slot in slots:
        base = slot.split(':')[0]; next_states = {}
        candidates = [fixed[slot]] if slot in fixed else list(usable)
        for mask, (score, assignment, optionality) in states.items():
            for pid in candidates:
                if mask & bits[pid] or (pid in fixed.values() and fixed.get(slot) != pid): continue
                if slot not in fixed and base not in players[pid].get('eligible_slots', []): continue
                if pid not in usable and slot not in fixed: continue
                a = usable.get(pid, {'rank_score': 0})
                schedules = [e for e in evidence.values() if e['subject'] == pid and e['kind'] == 'schedule']
                flex_time = timestamp(schedules[0]['value']['kickoff']).timestamp() if base in ('FLEX', 'SUPER_FLEX') and schedules else 0
                value = (score + a['rank_score'], dict(assignment, **{slot: pid}), optionality + flex_time)
                key = mask | bits[pid]
                old = next_states.get(key)
                if old is None or (value[0], value[2]) > (old[0], old[2]): next_states[key] = value
        if len(next_states) > 100000: raise ValueError('lineup search exceeds bounded state limit')
        states = next_states
    ranked = sorted(states.values(), key=lambda x: (x[0], x[2]), reverse=True)
    if not ranked: blockers.append('no complete feasible lineup')
    return dict(status='blocked' if blockers else 'review_required', blockers=sorted(set(blockers)), slots=slots, lineups=[dict(rubric_score=v[0], assignments=v[1]) for v in ranked[:5]], assessments=assessments, locked_assignments=fixed)


def render(pack, evaluation):
    req = pack['request']; s = pack['snapshot']['snapshot']
    lines = [f"# Weekly GM report: {req['league_id']} / {req['team_id']} — {req['season']} {req['season_type']} week {req['week']}", f"Status: {evaluation['status']}. Advisory only. Evidence cutoff: {req['as_of']}", f"Pack: {pack['pack_id']}; snapshot row {pack['snapshot']['row_id']}, SHA256 {pack['snapshot_hash']}", 'Rubric scores are qualitative comparisons supplied by Codex, not projected fantasy points.', 'Rules: ' + json.dumps(s['league_settings'], sort_keys=True), 'Blockers: ' + ('; '.join(evaluation['blockers']) or 'none'), '']
    if pack.get('history'):
        findings = pack['history']
        lines += ['Historical findings (descriptive; review original decisions before applying):',
                  json.dumps({k: findings.get(k, {}) for k in ('recurring_errors', 'evidence_gaps', 'confidence_patterns', 'limitation')}, sort_keys=True)]
    for i, lineup in enumerate(evaluation['lineups']):
        lines.append(('Candidate lineup' if i == 0 else f'Alternative {i}') + ': ' + json.dumps(lineup['assignments'], sort_keys=True))
    for pid, a in sorted(evaluation['assessments'].items(), key=lambda x: x[1]['rank_score'], reverse=True):
        lines += ['', f'## {player_name(s, pid)} ({pid})', f"Confidence: {a['confidence']} — {a['confidence_reason']}"]
        for key in ('thesis', 'scoring_fit', 'counterargument', 'risk', 'contingency', 'recheck_at'): lines.append(key + ': ' + a[key])
        lines.append('Usage: ' + json.dumps([d for d in pack['derived'] if d['subject'] == pid]))
        for eid in a['evidence_ids']:
            e = next(e for e in pack['evidence'] if e['evidence_id'] == eid)
            lines.append(f"Evidence {eid}: {e['source_url']} (observed {e['observed_at']}, fetched {e['fetched_at']})")
    return '\n\n'.join(lines) + '\n'


def player_name(snapshot, pid):
    return next(p['name'] for p in snapshot['roster'] if p.get('player_id', p['name']) == pid)


def persist(path, pack, evaluation, report, created_at):
    """Local private ledger. Atomic unique-key writes; immutable conflicting retries fail."""
    if evaluation['status'] != 'review_required': raise ValueError('cannot record blocked recommendations')
    created = timestamp(created_at)
    if created < timestamp(pack['request']['as_of']): raise ValueError('creation predates cutoff')
    if (created - timestamp(pack['request']['as_of'])).total_seconds() > 21600:
        raise ValueError('pack older than six hours; refresh evidence')
    if any(timestamp(a['recheck_at']) <= created for a in evaluation['assessments'].values()):
        raise ValueError('recheck deadline passed; refresh assessment')
    for e in pack['evidence']:
        if e['kind'] == 'schedule' and timestamp(e['value']['kickoff']) <= created:
            raise ValueError('pregame ledger cutoff passed; refresh pack and review locks')
    record = dict(pack=pack, evaluation=evaluation, report=report, created_at=created_at, adoption='unknown', record_type='decision')
    decision_id = digest(dict(pack_id=pack['pack_id'], context=pack['request']))
    return _store_record(path, decision_id, record)


def persist_provisional(path, pack, evaluation, report, created_at, *, acknowledge_unverified=False, parent_decision_id=None):
    """Explicitly archive a supplied recommendation; never attest current readiness.

    Preserve the original evaluation (including blockers) and historical cutoff.
    Unlike persist(), this does not require refreshed evidence or verified rules.
    """
    if acknowledge_unverified is not True:
        raise ValueError('explicit acknowledgement of unverified settings required')
    if digest({k: v for k, v in pack.items() if k != 'pack_id'}) != pack['pack_id']:
        raise ValueError('pack hash mismatch')
    if digest(pack['snapshot']['snapshot']) != pack['snapshot_hash']:
        raise ValueError('snapshot hash mismatch')
    if timestamp(created_at) < timestamp(pack['request']['as_of']):
        raise ValueError('creation predates cutoff')
    if not isinstance(report, str) or not report.strip() or not evaluation.get('lineups'):
        raise ValueError('existing report and candidate lineup required')
    players = {p.get('player_id', p['name']) for p in pack['snapshot']['snapshot']['roster']}
    evidence = {e['evidence_id']: e for e in pack['evidence']}
    for pid, assessment in evaluation['assessments'].items():
        refs = assessment.get('evidence_ids', [])
        if pid not in players or not refs or any(ref not in evidence or evidence[ref]['subject'] != pid for ref in refs):
            raise ValueError('invalid assessment citations')
    # Legality comes from the frozen snapshot, not the supplied evaluation. Eligibility is
    # enforced wherever the snapshot lists it; players without it stay marked unverified.
    from .outcomes import historical_options, reasoning_view
    slots, roster, fixed, excluded, _ = historical_options(dict(pack=pack, evaluation=evaluation))
    # historical_options skips exclusions when kickoff evidence is missing; apply the
    # known ones anyway (locked players keep their historical slot).
    out = {e['subject'] for e in reasoning_view(dict(pack=pack, evaluation=evaluation))['evidence']
           if e['kind'] == 'injury' and e['value'].get('game_status') in ('out', 'inactive')}
    excluded |= {pid for pid, p in roster.items() if pid not in fixed.values() and
                 (p.get('opponent') == 'BYE' or p['slot'] in ('IR', 'IR+') or pid in out)}
    if sorted(evaluation['slots']) != sorted(slots):
        raise ValueError('evaluation slots differ from the snapshot roster slots')
    for lineup in evaluation['lineups']:
        assignments = lineup['assignments']
        if (set(assignments) != set(slots) or
                len(set(assignments.values())) != len(assignments) or
                not set(assignments.values()) <= players or
                not set(assignments.values()) <= evaluation['assessments'].keys() or
                any(pid in excluded for pid in assignments.values()) or
                any(roster[pid].get('eligible_slots') and slot.split(':')[0] not in roster[pid]['eligible_slots']
                    for slot, pid in assignments.items()) or
                any(assignments.get(slot) != pid for slot, pid in fixed.items())):
            raise ValueError('invalid candidate lineup')
    record = dict(pack=pack, evaluation=evaluation, report=report, created_at=created_at,
                  record_type='decision', recommendation_status='provisional', adoption='unknown',
                  research_cutoff=pack['request']['as_of'], evidence_status='historical_not_refreshed',
                  confirmed_final_lineup=False, parent_decision_id=parent_decision_id,
                  settings_status={key: dict(basis='manually supplied report settings/assumptions',
                                             independently_verified=False)
                                   for key in ('yahoo_scoring', 'positional_eligibility', 'kickoff_locks')},
                  recording_note='User-authorized provisional archive. Original report/evaluation remain unchanged; historical statements describe their creation, not this later ledger write.')
    key = dict(pack_id=pack['pack_id'], context=pack['request'])
    if parent_decision_id:
        # Changed content gets a new key; retry timestamps do not create duplicates.
        key.update(parent_decision_id=parent_decision_id,
                   revision_content=digest(dict(evaluation=evaluation, report=report)))
    return _store_record(path, digest(key), record)


def _store_record(path, decision_id, record):
    """Serialize writers so simultaneous conflicting retries cannot be ignored."""
    # Every stored decision, provisional or not, is verifiable by outcomes.load_decision.
    record = dict(record, record_hash=digest(record))
    payload = json.dumps(record, sort_keys=True, allow_nan=False)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS decisions (decision_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        parent_id = record.get('parent_decision_id')
        if parent_id:
            parent = db.execute('SELECT payload FROM decisions WHERE decision_id=?', (parent_id,)).fetchone()
            if parent is None:
                raise ValueError('revision parent does not exist')
            parent = json.loads(parent[0])
            if timestamp(record['created_at']) < timestamp(parent['created_at']):
                raise ValueError('revision predates parent')
            for key in ('league_id', 'team_id', 'season', 'season_type', 'week', 'mode'):
                if parent['pack']['request'][key] != record['pack']['request'][key]:
                    raise ValueError('revision context differs from parent')
        previous = db.execute('SELECT payload FROM decisions WHERE decision_id=?', (decision_id,)).fetchone()
        if previous:
            original = json.loads(previous[0])
            original.pop('created_at'); original.pop('record_hash', None)
            if original != {k: v for k, v in record.items() if k not in ('created_at', 'record_hash')}:
                raise ValueError('immutable decision conflict')
        db.execute('INSERT OR IGNORE INTO decisions VALUES (?, ?)', (decision_id, payload))
    return decision_id
