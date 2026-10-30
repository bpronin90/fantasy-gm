"""python3 -m src.fantasy_gm --help"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from .snapshots import select
from .research import collect, build_pack
from .report import evaluate, render, persist, persist_provisional


def read(path): return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser(description='User-initiated private weekly GM analysis; no live workflow calls')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('pack'); p.add_argument('--rows', required=True); p.add_argument('--request', required=True); p.add_argument('--official', required=True); p.add_argument('--output-dir', required=True)
    p = sub.add_parser('report'); p.add_argument('--pack', required=True); p.add_argument('--assessments', required=True); p.add_argument('--output-dir', required=True)
    p = sub.add_parser('record-provisional', help='Record existing historical report without refreshing or overwriting it')
    p.add_argument('--pack', required=True); p.add_argument('--evaluation', required=True); p.add_argument('--report', required=True); p.add_argument('--output-dir', required=True)
    p.add_argument('--acknowledge-unverified-settings', action='store_true', required=True)
    p.add_argument('--parent-decision-id')
    args = parser.parse_args(); out = Path(args.output_dir).resolve()
    root = Path(__file__).resolve().parents[2]
    if out != root / 'data/private' and root / 'data/private' not in out.parents and (out == root or root in out.parents):
        parser.error('repository outputs must be under ignored data/private/')
    out.mkdir(parents=True, exist_ok=True)
    if args.command == 'pack':
        req = read(args.request); rows = read(args.rows); selection = select(rows, req)
        from .snapshots import timestamp
        if abs((datetime.now(timezone.utc) - timestamp(req['as_of'])).total_seconds()) > 300:
            raise ValueError('live collection requires a current cutoff within five minutes')
        # Fetch time is actual observation time, never a historical request cutoff.
        fetched = datetime.now(timezone.utc).isoformat()
        artifacts = {kind: collect(kind, req['season'], fetched, out / 'cache') for kind in ('players', 'schedule', 'stats')}
        if any(p['positions'] == ['DEF'] for p in selection['snapshot']['roster']):
            artifacts['team_stats'] = collect('team_stats', req['season'], datetime.now(timezone.utc).isoformat(), out / 'cache')
        req['as_of'] = datetime.now(timezone.utc).isoformat()
        selection = select(rows, req)
        pack = build_pack(selection, req, artifacts, read(args.official))
        (out / 'pack.json').write_text(json.dumps(pack, indent=2))
        print('Pack saved; review coverage and supply cited Codex assessments before report.')
    elif args.command == 'report':
        pack = read(args.pack); result = evaluate(pack, read(args.assessments)); report = render(pack, result)
        (out / 'report.md').write_text(report)
        (out / 'evaluation.json').write_text(json.dumps(result, indent=2))
        if result['blockers']: print('BLOCKED: report saved with explicit gaps; no ledger decision written.'); return
        decision_id = persist(out / 'decisions.sqlite', pack, result, report, datetime.now(timezone.utc).isoformat())
        print('Decision saved: ' + decision_id)
    else:
        decision_id = persist_provisional(out / 'decisions.sqlite', read(args.pack), read(args.evaluation),
            Path(args.report).read_text(), datetime.now(timezone.utc).isoformat(),
            acknowledge_unverified=args.acknowledge_unverified_settings, parent_decision_id=args.parent_decision_id)
        print('Provisional decision saved: ' + decision_id)

if __name__ == '__main__': main()
