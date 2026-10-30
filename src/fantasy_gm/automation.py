"""Deterministic scheduled research worker; no AI, Yahoo or n8n client.

Run only when explicitly invoked. A schedule definition does not activate this code.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from .research import collect, digest, build_pack
from .outcomes import load_decision, evaluate_outcome, append_outcome, history, CONTEXT, require_private_ledger
from .snapshots import timestamp

# These extend the existing CSV boundary, not a parallel downloader/parser.
from .research import SOURCES, REQUIRED
SOURCES.update(rosters='https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{season}.csv',
               injuries='https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv')
REQUIRED.update(rosters={'gsis_id', 'team', 'season', 'week'},
                injuries={'gsis_id', 'team', 'season', 'week', 'report_status', 'date_modified', 'season_type'})


def material_changes(record, pack):
    """Structured changes only. Never parse external prose as instructions."""
    old = record['pack']; changes = []
    for kind in ('schedule', 'injury', 'identity', 'news'):
        for pid in old['coverage']:
            before = [e['value'] for e in old['evidence'] if e['subject'] == pid and e['kind'] == kind]
            after = [e['value'] for e in pack['evidence'] if e['subject'] == pid and e['kind'] == kind]
            if before != after:
                changes.append(dict(subject=pid, kind=kind, before=before, after=after, action='user-initiated Codex recheck; no automatic recommendation'))
    for item in pack.get('nfl_roster_monitor', []):
        if item['status'] != 'matched': changes.append(dict(item, kind='nfl_roster', action='identity/team recheck; Yahoo ownership unknown'))
    for pid, coverage in pack['coverage'].items():
        if coverage['missing']: changes.append(dict(subject=pid, kind='coverage', gaps=coverage['missing'], action='research incomplete'))
    return changes


def official_injuries(record, artifact):
    """Optional public report monitor. Feed unavailable/old means unknown, never healthy.

    Not a substitute for official active/inactive verification in a final report.
    """
    req = record['pack']['request']; items = []
    for p in record['pack']['snapshot']['snapshot']['roster']:
        for row in artifact.get('rows', []):
            if row.get('gsis_id') != p.get('gsis_id') or not p.get('gsis_id') or str(row.get('week')) != str(req['week']) or str(row.get('season')) != str(req['season']) or row.get('season_type') != req['season_type']: continue
            # Preserve source status verbatim; avoid interpreting blank as available.
            date = row['date_modified']
            try: timestamp(date)
            except ValueError: date = None  # Never invent a timezone or publication time.
            items.append(dict(subject=p.get('player_id', p['name']), kind='injury', value=dict(game_status=row['report_status'].lower() or 'unknown', report=row),
                              source_url=artifact['url'], provider='nflverse injury feed (official verification pending)',
                              published_at=None, observed_at=date, fetched_at=artifact['fetched_at'],
                              season=req['season'], week=req['week'], revision=artifact['sha256'], monitor_only=True))
    return items


def refresh_pack(record, artifacts, as_of, ledger):
    req = dict(record['pack']['request'], as_of=as_of)
    # Historical official evidence is retained with its age; build_pack flags stale.
    official = [e for e in record['pack']['evidence'] if e['kind'] in ('injury', 'news', 'matchup')]
    pack = build_pack(copy.deepcopy(record['pack']['snapshot']), req, artifacts, official)
    monitor = official_injuries(record, artifacts.get('injuries', {}))
    pack['injury_monitor'] = monitor
    pack['nfl_roster_monitor'] = []
    for p in record['pack']['snapshot']['snapshot']['roster']:
        rows = [r for r in artifacts.get('rosters', {}).get('rows', [])
                if p.get('gsis_id') and r.get('gsis_id') == p['gsis_id']
                and str(r.get('season')) == str(req['season']) and str(r.get('week')) == str(req['week'])]
        if p['positions'] != ['DEF']:
            pack['nfl_roster_monitor'].append(dict(subject=p.get('player_id', p['name']), recorded_team=p['team'],
                rows=rows, status='matched' if len(rows) == 1 and rows[0]['team'] == p['team'] else 'changed_or_unknown'))
    if monitor:
        pack['evidence'].extend(dict(e, evidence_id=digest(e)) for e in monitor)
    snapshot_age = (timestamp(as_of) - timestamp(pack['snapshot']['snapshot']['captured_at'])).total_seconds()
    for pid, coverage in pack['coverage'].items():
        if not 0 <= snapshot_age <= 86400: coverage['missing'].append('recorded roster snapshot stale; Yahoo synchronization pending')
        coverage['missing'].append('scheduled pack requires user-initiated final evidence/eligibility/lock review')
        coverage['missing'].append('injury monitor is not official inactive verification')
        stats = artifacts.get('stats', {})
        if not stats or not 0 <= (timestamp(as_of)-timestamp(stats['fetched_at'])).total_seconds() <= 86400:
            coverage['missing'].append('statistics stale/unavailable')
    pack['history'] = history(ledger, req)
    pack['boundaries'] = dict(snapshot='previously recorded roster, not a synchronized Yahoo roster',
                              nfl_rosters='public NFL affiliations only; never fantasy ownership or Yahoo eligibility',
                              ai='Research Pack for user-initiated Codex; no unattended inference')
    pack['pack_id'] = digest({k: v for k, v in pack.items() if k != 'pack_id'})
    return pack


def run_job(ledger, job, as_of, collector=collect, observed_at=None):
    """Serialize bounded single-user jobs; commit every attempt and visible failures.

    Successful windows are idempotent. Failed windows can retry three times.
    SQLite rollback permits retry after process death. No workflow/Data Table writes.
    """
    if job.get('task') not in ('research', 'pregame', 'postgame'): raise ValueError('unsupported deterministic task')
    if type(job.get('interval_seconds')) is not int or not 300 <= job['interval_seconds'] <= 86400: raise ValueError('invalid interval')
    decision_ids = sorted(set(job['decision_ids']))
    if not decision_ids: raise ValueError('recorded decision IDs required; no roster imports')
    records = {key: load_decision(ledger, key) for key in decision_ids}
    for record in records.values():
        if any(record['pack']['request'][k] != job['context'][k] for k in CONTEXT): raise ValueError('job decision context mismatch')
    bucket = int(timestamp(as_of).timestamp()) // job['interval_seconds']
    key = digest(dict(job=job, bucket=bucket, worker_version=1))
    with sqlite3.connect(ledger, timeout=300) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS research_jobs (job_key TEXT NOT NULL, attempt INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_key,attempt))')
        prior = db.execute('SELECT attempt,payload FROM research_jobs WHERE job_key=? ORDER BY attempt DESC LIMIT 1', (key,)).fetchone()
        if prior:
            saved = json.loads(prior[1])
            if saved['status'] == 'success' or prior[0] >= 3: return saved
        attempt = prior[0]+1 if prior else 1
        artifacts, failures = {}, []
        kinds = ('schedule', 'stats', 'team_stats') if job['task'] == 'postgame' else ('players', 'schedule', 'stats', 'rosters', 'injuries')
        if any(p['positions'] == ['DEF'] for r in records.values() for p in r['pack']['snapshot']['snapshot']['roster']) and 'team_stats' not in kinds: kinds += ('team_stats',)
        for kind in kinds:
            try:
                art = collector(kind, job['context']['season'], as_of, Path(ledger).parent/'cache')
                if not 0 <= (timestamp(observed_at() if observed_at else as_of) - timestamp(art['fetched_at'])).total_seconds() <= (900 if job['task'] == 'pregame' and kind == 'schedule' else 3600): raise ValueError('stale/future research artifact')
                artifacts[kind] = art
            except (OSError, ValueError) as exc:
                # Do not save arbitrary server text/headers or exception payloads.
                failures.append(dict(kind=kind, error_type=type(exc).__name__, message='source refresh failed; research remains unavailable'))
        if observed_at is not None:
            as_of = observed_at()
        outputs = []
        # Calculate now; append outcomes after releasing the job transaction.
        # Pending outcomes live in the attempt, permitting crash-safe completion.
        for decision_id, record in records.items():
            try:
                if job['task'] == 'postgame':
                    prior = history(ledger, dict(record['pack']['request'], as_of=as_of))
                    previous = next((o for o in prior['evaluations'] if o['decision_id'] == decision_id), None)
                    review = previous['reasoning']['review'] if previous else None
                    outputs.append(dict(decision_id=decision_id, outcome=evaluate_outcome(record, decision_id, artifacts, as_of, review=review)))
                else:
                    pack = refresh_pack(record, artifacts, as_of, ledger)
                    outputs.append(dict(decision_id=decision_id, pack=pack, material_changes=material_changes(record, pack)))
            except (ValueError, KeyError, TypeError) as exc:
                failures.append(dict(decision_id=decision_id, error_type=type(exc).__name__, message='calculation failed; no ready report'))
        result = dict(job_key=key, attempt=attempt, as_of=as_of, status='failed' if failures else 'success',
                      task=job['task'], failures=failures, outputs=outputs, ai_inference=False,
                      reporting='Inspect private job report; nonzero CLI exit on failure. No external messages sent.')
        db.execute('INSERT INTO research_jobs VALUES (?,?,?)', (key, attempt, json.dumps(result, sort_keys=True, allow_nan=False)))
    return result


def complete_outcomes(ledger, result):
    # Replay after a crash is safe because append_outcome is content-addressed.
    return [append_outcome(ledger, item['outcome']) for item in result['outputs'] if 'outcome' in item]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True); parser.add_argument('--job', required=True)
    args = parser.parse_args(); args.ledger = require_private_ledger(parser, args.ledger)
    def live_collect(kind, season, as_of, cache):
        artifact = collect(kind, season, datetime.now(timezone.utc).isoformat(), cache)
        artifact['fetched_at'] = datetime.now(timezone.utc).isoformat()
        return artifact
    result = run_job(args.ledger, json.loads(Path(args.job).read_text()), datetime.now(timezone.utc).isoformat(),
                     collector=live_collect, observed_at=lambda: datetime.now(timezone.utc).isoformat())
    ids = complete_outcomes(args.ledger, result)
    # Private details remain in SQLite, not n8n execution logs.
    print(json.dumps(dict(job_key=result['job_key'], status=result['status'], failures=result['failures'], outcome_ids=ids, ai_inference=False)))
    if result['status'] != 'success': raise SystemExit(1)

if __name__ == '__main__': main()
