"""Fictional synthetic retrospective data only; no real NFL/league snapshots."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from test_phase2 import ready_pack
from src.fantasy_gm.research import digest, parse_csv
from src.fantasy_gm.report import evaluate, persist, render
from src.fantasy_gm.outcomes import (load_decision, reasoning_view, fantasy_points, historical_options,
    legal, evaluate_outcome, append_outcome, history)
from src.fantasy_gm.automation import run_job, complete_outcomes, official_injuries

AFTER = '2026-11-03T12:00:00Z'


def artifact(rows, kind='stats'):
    return dict(kind=kind, rows=rows, url='https://example.invalid/synthetic/'+kind, provider='synthetic',
                sha256=digest(rows), fetched_at=AFTER, published_at=None)


def setup_record(path):
    pack, assessments = ready_pack()
    for i, p in enumerate(pack['snapshot']['snapshot']['roster']):
        p.update(gsis_id='synthetic-'+str(i), identity_verified=True)
    pack['snapshot_hash'] = digest(pack['snapshot']['snapshot'])
    pack['pack_id'] = digest({k:v for k,v in pack.items() if k!='pack_id'})
    result = evaluate(pack, assessments)
    key = persist(path, pack, result, render(pack, result), '2026-10-28T14:00:00Z')
    return key, load_decision(path, key)


def results(values=(10,9,8,20,1)):
    game = dict(game_id='synthetic-game8', season='2026', week='8', game_type='REG', home_team='BUF', away_team='MIN', gameday='2026-11-01', gametime='13:00', home_score='21', away_score='14', result='7')
    stats = [dict(player_id='synthetic-'+str(i), season='2026', week='8', season_type='REG', team='BUF', receptions=str(v), targets=str(v), carries='0') for i,v in enumerate(values)]
    return dict(stats=artifact(stats), schedule=artifact([game], 'schedule'))


def review(record, judgment='good'):
    view = reasoning_view(record)
    return dict(view_hash=digest(view), cutoff_only=True, blinded_to_results=True, judgment=judgment,
                rationale='Synthetic cutoff-only role review; no results inspected.', evidence_ids=['0'], error_tags=['small_sample'])


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'decisions.sqlite'
        self.key, self.record = setup_record(self.path)

    def outcome(self, artifacts=None, record=None, **kw):
        return evaluate_outcome(record or self.record, self.key, artifacts or results(), AFTER, **kw)

    def test_actual_scoring_coefficients_bonuses_and_unknowns(self):
        settings = dict(scoring={'passing_yards':.04, 'passing_tds':4, 'passing_interceptions':-2, 'receptions':1, 'receiving_yards':.1}, missing_rules=[],
                        scoring_bonuses=[dict(stat='receiving_yards', min=100, max=None, points=3)])
        stats = dict(passing_yards='250', passing_tds='2', passing_interceptions='1', receptions='5', receiving_yards='100')
        self.assertEqual(fantasy_points(stats,settings),34)
        for change in ({'receiving_yards':''}, {'receiving_yards':float('nan')}, {'passing_tds':True}):
            with self.assertRaises(ValueError): fantasy_points(dict(stats,**change),settings)
        with self.assertRaises(ValueError): fantasy_points(stats,dict(settings,missing_rules=['DEF tiers unknown']))
        with self.assertRaises(ValueError): fantasy_points(stats,dict(scoring={'fantasy_points_ppr':1}, missing_rules=[]))
        tiered = dict(scoring={'sacks':1}, missing_rules=[], scoring_bonuses=[dict(stat='points_allowed',min=0,max=7,points=10)])
        self.assertEqual(fantasy_points(dict(sacks=3,points_allowed=0),tiered),13)
        self.assertEqual(fantasy_points(dict(sacks=3,points_allowed=7),tiered),3)

    def test_positional_scoring_never_uses_offensive_team_totals_for_defense(self):
        settings=dict(scoring={'receptions':1,'passing_yards':.04},missing_rules=[],
                      scoring_by_position={'DEF':dict(scoring={'def_sacks':1},missing_rules=[],
                          scoring_bonuses=[dict(stat='points_allowed',min=0,max=7,points=10)])})
        stats=dict(receptions=20,passing_yards=300,def_sacks=3,points_allowed=0)
        self.assertEqual(fantasy_points(stats,settings,['DEF']),13)
        self.assertEqual(fantasy_points(stats,settings,['QB']),32)
        with self.assertRaisesRegex(ValueError,'explicit DEF/K'):
            fantasy_points(stats,dict(settings,scoring_by_position={}),['DEF'])
        with self.assertRaises(ValueError):fantasy_points(stats,settings,['K'])

    def test_end_to_end_multiple_flex_and_immutable_corrections(self):
        with sqlite3.connect(self.path) as db: original = db.execute('SELECT payload FROM decisions').fetchone()[0]
        result = self.outcome()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['recommended_points'],27)
        self.assertEqual(result['best_feasible']['points'],39)
        self.assertEqual(result['lineup_regret'],12)
        self.assertEqual(result['points_gained_lost'],0)
        self.assertEqual(len([s for s in result['best_feasible']['assignments'] if s.startswith('FLEX:')]),2)
        first = append_outcome(self.path,result)
        self.assertEqual(first,append_outcome(self.path,self.outcome()))
        refreshed=self.outcome()
        refreshed['as_of']='2026-11-03T12:01:00Z'
        for art in refreshed['sources'].values(): art['fetched_at']=refreshed['as_of']
        self.assertEqual(first,append_outcome(self.path,refreshed))
        changed = self.outcome(results((10,9,8,21,1)))
        second = append_outcome(self.path,changed)
        self.assertNotEqual(first,second)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(original,db.execute('SELECT payload FROM decisions').fetchone()[0])
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM outcomes ORDER BY sequence')]
            self.assertEqual(len(rows),2); self.assertEqual(rows[1]['supersedes'],first)
            for query in ("UPDATE outcomes SET payload='{}'", 'DELETE FROM outcomes'):
                with self.assertRaises(sqlite3.IntegrityError): db.execute(query)
        self.assertEqual(rows[0]['lineup_regret'],12)
        later = copy.deepcopy(result)
        for art in later['sources'].values(): art['fetched_at']='2026-11-03T13:00:00Z'
        later['as_of']='2026-11-03T13:00:00Z'
        reverted=append_outcome(self.path,later)
        self.assertNotIn(reverted,(first,second))
        self.assertEqual(reverted,append_outcome(self.path,later))
        with sqlite3.connect(self.path) as db:
            saved=json.loads(db.execute('SELECT payload FROM outcomes ORDER BY sequence DESC LIMIT 1').fetchone()[0])
        self.assertEqual(saved['supersedes'],second)
        self.assertEqual(saved['lineup_regret'],12)
        req=dict(self.record['pack']['request'],as_of=later['as_of'])
        self.assertEqual(history(self.path,req)['evaluations'][0]['outcome_id'],reverted)

    def test_recommended_gain_against_recorded_baseline(self):
        record = copy.deepcopy(self.record)
        record['evaluation']['lineups'][0]['assignments']={'WR:1':'0','FLEX:1':'1','FLEX:2':'3'}
        outcome = self.outcome(record=record)
        self.assertEqual(outcome['points_gained_lost'],12)
        self.assertEqual(outcome['lineup_regret'],0)

    def test_illegal_historical_alternatives(self):
        record = copy.deepcopy(self.record)
        roster = record['pack']['snapshot']['snapshot']['roster']
        roster[3]['eligible_slots']=['QB']
        outcome = self.outcome(record=record)
        self.assertEqual(outcome['lineup_regret'],0)
        slots, players, fixed, excluded, _ = historical_options(record)
        for assignment in ({'WR:1':'0','FLEX:1':'0','FLEX:2':'1'}, {'WR:1':'3','FLEX:1':'0','FLEX:2':'1'}, {'WR:1':'waiver','FLEX:1':'0','FLEX:2':'1'}):
            self.assertFalse(legal(assignment,slots,players,fixed,excluded))

    def test_locked_bench_and_locked_starting_flex(self):
        record = copy.deepcopy(self.record)
        record['pack']['evidence'][3]['value']['kickoff']='2026-10-28T12:00:00Z'
        result = self.outcome(record=record)
        self.assertNotIn('3',result['best_feasible']['assignments'].values())
        record['pack']['evidence'][1]['value']['kickoff']='2026-10-28T12:00:00Z'
        result = self.outcome(record=record)
        self.assertEqual(result['best_feasible']['assignments']['FLEX:1'],'1')
        record['evaluation']['lineups'][0]['assignments']={'WR:1':'1','FLEX:1':'0','FLEX:2':'2'}
        self.assertEqual(self.outcome(record=record)['status'],'incomplete')

    def test_injury_at_cutoff_versus_later_dnp(self):
        record = copy.deepcopy(self.record)
        record['pack']['evidence'].append(dict(evidence_id='out3',subject='3',kind='injury',value={'game_status':'out'},
            observed_at='2026-10-28T12:00:00Z',fetched_at='2026-10-28T12:30:00Z'))
        self.assertEqual(self.outcome(record=record)['lineup_regret'],0)
        record['pack']['evidence'][-1]['fetched_at']=AFTER
        self.assertEqual(self.outcome(record=record)['lineup_regret'],12)
        art = results(); art['stats']['rows'].pop(0)
        self.assertIsNone(self.outcome(art)['recommended_points'])
        dnp = dict(subject='0', game_id='synthetic-game8', season=2026,week=8,season_type='REG', status='dnp',source_url='https://example.invalid/synthetic-inactive',revision='synthetic-dnp1',fetched_at=AFTER)
        result = self.outcome(art,participation=[dnp])
        self.assertEqual(result['actuals']['0']['points'],0)
        self.assertEqual(result['actuals']['0']['status'],'sourced_dnp')
        conflict = self.outcome(participation=[dnp])
        self.assertEqual(conflict['actuals']['0']['status'],'conflicting_dnp_statistics')

    def test_conflicting_cutoff_injuries_and_ambiguous_identities(self):
        record=copy.deepcopy(self.record)
        for status in ('out','questionable'):
            record['pack']['evidence'].append(dict(evidence_id='synthetic-'+status,subject='3',kind='injury',
                value={'game_status':status},observed_at='2026-10-28T12:00:00Z',fetched_at='2026-10-28T12:30:00Z'))
        outcome=self.outcome(record=record)
        self.assertEqual(outcome['status'],'incomplete')
        self.assertIsNone(outcome['lineup_regret'])
        record=copy.deepcopy(self.record)
        record['pack']['snapshot']['snapshot']['roster'][1]['gsis_id']='synthetic-0'
        with self.assertRaisesRegex(ValueError,'ambiguous historical GSIS'):self.outcome(record=record)

    def test_ir_bye_and_missing_kickoff(self):
        record = copy.deepcopy(self.record)
        record['pack']['snapshot']['snapshot']['roster'][3]['slot']='IR'
        record['pack']['snapshot']['snapshot']['roster'][4]['opponent']='BYE'
        self.assertEqual(self.outcome(record=record)['lineup_regret'],0)
        record['pack']['evidence'].pop(0)
        self.assertEqual(self.outcome(record=record)['status'],'incomplete')

    def test_incomplete_games_duplicate_rows_and_missing_statistics(self):
        art = results(); art['schedule']['rows'][0].update(result='',home_score='',away_score='')
        incomplete = self.outcome(art)
        self.assertEqual(incomplete['status'],'incomplete'); self.assertIsNone(incomplete['lineup_regret'])
        first = append_outcome(self.path,incomplete)
        second = append_outcome(self.path,self.outcome())
        self.assertNotEqual(first,second)
        future=results();future['schedule']['rows'][0]['gameday']='2026-11-08'
        self.assertEqual(self.outcome(future)['status'],'incomplete')
        for mutation in ('missing','wrongweek','wrongseason','duplicate','missingcolumn'):
            art=results()
            if mutation=='missing': art['stats']['rows'].pop(3)
            if mutation=='wrongweek': art['stats']['rows'][3]['week']='7'
            if mutation=='wrongseason': art['stats']['rows'][3]['season']='2025'
            if mutation=='duplicate': art['stats']['rows'].append(art['stats']['rows'][3])
            if mutation=='missingcolumn': art['stats']['rows'][3].pop('receptions')
            self.assertIsNone(self.outcome(art)['lineup_regret'],mutation)

    def test_assumptions_are_conditional_and_do_not_verify_yahoo(self):
        record=copy.deepcopy(self.record)
        record['pack']['snapshot']['snapshot']['league_settings']['verification']={}
        result=self.outcome(record=record,review=review(record))
        self.assertEqual(result['status'],'conditional'); self.assertEqual(len(result['uncertainty']),3)
        self.assertEqual(result['classification'],'insufficient evidence to judge')
        record['pack']['snapshot']['snapshot']['league_settings']['missing_rules']=['unknown']
        self.assertEqual(self.outcome(record=record)['status'],'incomplete')

    def test_reasoning_classifications_are_separate_from_outcomes(self):
        for judgment in ('good','poor'):
            for stats,outcome in ((results(),'bad'),(results((10,9,8,1,0)),'good')):
                result=self.outcome(stats,review=review(self.record,judgment))
                self.assertEqual(result['classification'],f'{judgment} decision, {outcome} outcome')
        self.assertEqual(self.outcome()['classification'],'insufficient evidence to judge')

    def test_hindsight_contamination_blocked(self):
        record=copy.deepcopy(self.record)
        record['pack']['evidence'][0]['published_at']=AFTER
        valid_review=review(record);valid_review['evidence_ids']=['1']
        result=self.outcome(record=record,review=valid_review)
        self.assertEqual(result['reasoning']['judgment'],'insufficient')
        bad=review(record);bad['evidence_ids']=['0']
        with self.assertRaisesRegex(ValueError,'outside original cutoff'): self.outcome(record=record,review=bad)
        bad=review(self.record);bad['blinded_to_results']=False
        with self.assertRaises(ValueError):self.outcome(review=bad)
        view=reasoning_view(self.record)
        self.assertNotIn('actuals',view);self.assertEqual(len(view['evidence']),5) # future kickoff is allowed
        altered=copy.deepcopy(self.record);altered['pack']['evidence'][0]['value']['kickoff']='2026-11-02T18:00:00Z'
        with self.assertRaises(ValueError):self.outcome(record=altered,review=review(self.record))

    def test_history_scoped_deduplicated_and_cutoff_safe(self):
        append_outcome(self.path,self.outcome(review=review(self.record,'poor')))
        append_outcome(self.path,self.outcome(results((10,9,8,21,1)),review=review(self.record,'poor')))
        req=dict(self.record['pack']['request'],as_of='2026-11-04T12:00:00Z')
        findings=history(self.path,req)
        self.assertEqual(len(findings['evaluations']),1);self.assertEqual(findings['recurring_errors'],{'small_sample':1})
        self.assertEqual(findings['confidence_patterns']['medium']['recommended_player_observations'],3)
        self.assertEqual(history(self.path,dict(req,team_id='other'))['evaluations'],[])
        self.assertEqual(history(self.path,self.record['pack']['request'])['evaluations'],[])

    def test_outcome_link_hash_and_stale_sources(self):
        result=self.outcome();result['decision_hash']='wrong'
        with self.assertRaises(ValueError):append_outcome(self.path,result)
        art=results();art['stats']['fetched_at']='2026-10-28T12:00:00Z'
        with self.assertRaisesRegex(ValueError,'stale'):self.outcome(art)
        art['stats']['fetched_at']='2026-11-04T12:00:00Z'
        with self.assertRaises(ValueError):self.outcome(art)

    def test_cli_reads_existing_ledger_and_appends_outcome(self):
        artifacts=Path(self.tmp.name)/'synthetic-results.json';artifacts.write_text(json.dumps(results()))
        run=subprocess.run(['python3','-m','src.fantasy_gm.outcomes','--ledger',str(self.path),'--decision-id',self.key,'--as-of',AFTER,'--artifacts',str(artifacts)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)['lineup_regret'],12)

    def test_clis_reject_ledger_inside_repo_outside_private(self):
        for module, extra in (('outcomes', ['--decision-id', 'x', '--as-of', AFTER]), ('automation', ['--job', 'job.json'])):
            run=subprocess.run(['python3','-m','src.fantasy_gm.'+module,'--ledger','docs/decisions.sqlite',*extra],capture_output=True,text=True)
            self.assertEqual(run.returncode,2,module)
            self.assertIn('data/private',run.stderr)
            self.assertFalse(Path('docs/decisions.sqlite').exists())

    def test_ledger_symlink_resolves_before_cache_path(self):
        from src.fantasy_gm.outcomes import require_private_ledger
        import argparse
        link=Path(self.tmp.name)/'link.sqlite'; link.symlink_to(self.path)
        self.assertEqual(require_private_ledger(argparse.ArgumentParser(),str(link)),str(Path(self.path).resolve()))
        inside=Path('docs/ledger-link.sqlite'); inside.symlink_to(Path(self.path).resolve())
        try:
            # Resolves outside the repo, so it is allowed, but the cache follows the real file.
            resolved=require_private_ledger(argparse.ArgumentParser(),str(inside))
            self.assertEqual(Path(resolved).parent/'cache',Path(self.path).resolve().parent/'cache')
        finally:
            inside.unlink()

    def test_provisional_record_is_hash_verified(self):
        from src.fantasy_gm.report import persist_provisional
        pack, assessments = ready_pack()
        result = evaluate(pack, assessments)
        path = Path(self.tmp.name)/'provisional.sqlite'
        key = persist_provisional(path, pack, result, render(pack, result), '2026-11-03T12:00:00Z', acknowledge_unverified=True)
        self.assertIn('record_hash', load_decision(path, key))
        with sqlite3.connect(path) as db:
            payload = json.loads(db.execute('SELECT payload FROM decisions WHERE decision_id=?', (key,)).fetchone()[0])
            payload['recommendation_status'] = 'final'
            db.execute('UPDATE decisions SET payload=? WHERE decision_id=?', (json.dumps(payload, sort_keys=True), key))
        with self.assertRaisesRegex(ValueError, 'decision hash mismatch'):
            load_decision(path, key)

    def test_provisional_rejects_lineups_illegal_for_the_snapshot(self):
        from src.fantasy_gm.report import persist_provisional
        pack, assessments = ready_pack()
        result = evaluate(pack, assessments); report = render(pack, result)
        path = Path(self.tmp.name)/'illegal.sqlite'
        cases = ((['QB:1'], [{'assignments': {'QB:1': '0'}}], 'slots differ'),
                 ([], [{'assignments': {}}], 'slots differ'))
        for slots, lineups, message in cases:
            bad = dict(copy.deepcopy(result), slots=slots, lineups=lineups)
            with self.assertRaisesRegex(ValueError, message):
                persist_provisional(path, pack, bad, report, '2026-11-03T12:00:00Z', acknowledge_unverified=True)
        swapped = copy.deepcopy(result); first = swapped['lineups'][0]['assignments']
        flex = next(k for k in first if k.startswith('FLEX')); wr = next(k for k in first if k.startswith('WR'))
        pack['snapshot']['snapshot']['roster'][int(first[flex])]['eligible_slots'] = ['FLEX']
        pack['snapshot_hash'] = digest(pack['snapshot']['snapshot'])
        pack['pack_id'] = digest({k: v for k, v in pack.items() if k != 'pack_id'})
        first[wr], first[flex] = first[flex], first[wr]
        with self.assertRaisesRegex(ValueError, 'invalid candidate lineup'):
            persist_provisional(path, pack, swapped, report, '2026-11-03T12:00:00Z', acknowledge_unverified=True)

    def test_provisional_excludes_ir_and_out_without_kickoff_evidence(self):
        from src.fantasy_gm.report import persist_provisional
        base_pack, assessments = ready_pack()
        result = evaluate(base_pack, assessments); report = render(base_pack, result)
        self.assertIn('0', result['lineups'][0]['assignments'].values())
        for variant in ('IR', 'out', 'inactive'):
            pack = copy.deepcopy(base_pack)
            status = 'questionable' if variant == 'IR' else variant
            pack['evidence'][0] = dict(pack['evidence'][0], kind='injury', value={'game_status': status})
            if variant == 'IR': pack['snapshot']['snapshot']['roster'][0]['slot'] = 'IR'
            pack['snapshot_hash'] = digest(pack['snapshot']['snapshot'])
            pack['pack_id'] = digest({k: v for k, v in pack.items() if k != 'pack_id'})
            with self.assertRaisesRegex(ValueError, 'invalid candidate lineup', msg=variant):
                persist_provisional(Path(self.tmp.name)/(variant+'.sqlite'), pack, result, report,
                                    '2026-11-03T12:00:00Z', acknowledge_unverified=True)

    def test_tampered_recommendation_rejected(self):
        with sqlite3.connect(self.path) as db:
            payload=json.loads(db.execute('SELECT payload FROM decisions WHERE decision_id=?',(self.key,)).fetchone()[0])
            lineup=payload['evaluation']['lineups'][0]['assignments']
            first, second=list(lineup)[:2]; lineup[first], lineup[second]=lineup[second], lineup[first]
            db.execute('UPDATE decisions SET payload=? WHERE decision_id=?',(json.dumps(payload,sort_keys=True),self.key))
        with self.assertRaisesRegex(ValueError,'decision hash mismatch'):
            load_decision(self.path,self.key)

    def test_legacy_decision_without_hash_is_labelled(self):
        with sqlite3.connect(self.path) as db:
            payload=json.loads(db.execute('SELECT payload FROM decisions WHERE decision_id=?',(self.key,)).fetchone()[0])
            payload.pop('record_hash')
            db.execute('UPDATE decisions SET payload=? WHERE decision_id=?',(json.dumps(payload,sort_keys=True),self.key))
        record=load_decision(self.path,self.key)
        self.assertEqual(evaluate_outcome(record,self.key,results(),AFTER)['decision_integrity'],'legacy_unverified')
        self.assertEqual(evaluate_outcome(self.record,self.key,results(),AFTER)['decision_integrity'],'verified')


class AutomationTests(unittest.TestCase):
    setUp = OutcomeTests.setUp
    outcome = OutcomeTests.outcome
    def job(self,task='postgame'):
        return dict(task=task,interval_seconds=3600,decision_ids=[self.key],context={k:self.record['pack']['request'][k] for k in ('league_id','team_id','season','season_type','week','mode')})

    def collector(self,kind,season,as_of,cache):
        art=results().get(kind,artifact([],kind));art['fetched_at']=as_of
        if kind=='players':art=artifact([dict(gsis_id='synthetic-'+str(i),display_name='Sample Player '+str(i)) for i in range(5)],kind)
        return art

    def test_scheduled_idempotency_and_crash_replay(self):
        calls=[]
        def collector(*args): calls.append(args[0]);return self.collector(*args)
        job=self.job();first=run_job(self.path,job,AFTER,collector)
        retry=run_job(self.path,job,AFTER,collector)
        self.assertEqual(first,retry);self.assertEqual(len(calls),3)
        ids=complete_outcomes(self.path,first)
        self.assertEqual(ids,complete_outcomes(self.path,retry))
        self.assertFalse(first['ai_inference'])
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],1)

    def test_source_failure_retry_caps_and_stale_research(self):
        def fail(*args):raise TimeoutError('synthetic secret server text must not be saved')
        for attempt in range(1,5):
            result=run_job(self.path,self.job(),AFTER,fail)
            self.assertEqual(result['attempt'],min(attempt,3));self.assertEqual(result['status'],'failed')
            self.assertNotIn('secret server text',json.dumps(result))
        def stale(*args):
            art=self.collector(*args);art['fetched_at']='2026-10-28T12:00:00Z';return art
        result=run_job(self.path,self.job(), '2026-11-03T14:00:00Z', stale)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['failures'])

    def test_concurrent_jobs_only_collect_once(self):
        calls=[]
        def collector(*args):calls.append(args[0]);return self.collector(*args)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs=list(pool.map(lambda _:run_job(self.path,self.job(),AFTER,collector),range(3)))
        self.assertEqual(len(calls),3);self.assertEqual(len({r['job_key'] for r in jobs}),1)

    def test_refresh_pack_history_changes_and_no_ai(self):
        append_outcome(self.path,self.outcome(review=review(self.record,'poor')))
        result=run_job(self.path,self.job('pregame'),AFTER,self.collector)
        self.assertEqual(result['status'],'success')
        output=result['outputs'][0];pack=output['pack']
        self.assertEqual(pack['history']['recurring_errors'],{'small_sample':1})
        self.assertTrue(output['material_changes'])
        self.assertTrue(any('snapshot stale' in g for c in pack['coverage'].values() for g in c['missing']))
        report_pack=copy.deepcopy(self.record['pack']);report_pack['history']=pack['history']
        self.assertIn('Historical findings',render(report_pack,self.record['evaluation']))
        self.assertEqual(pack['pack_id'],digest({k:v for k,v in pack.items() if k!='pack_id'}))
        self.assertEqual(self.record,load_decision(self.path,self.key))

    def test_injury_and_nfl_roster_monitor_preserve_unknowns(self):
        rows=[dict(gsis_id='synthetic-0',season='2026',week='8',season_type='REG',report_status='Out',date_modified='2026-11-03 10:00:00')]
        monitor=official_injuries(self.record,artifact(rows,'injuries'))
        self.assertEqual(monitor[0]['value']['game_status'],'out')
        self.assertIsNone(monitor[0]['observed_at'])
        self.assertNotIn('manually_verified_official',monitor[0])
        rows[0]['season_type']='POST'
        self.assertEqual(official_injuries(self.record,artifact(rows,'injuries')),[])
        body=b'gsis_id,team,season,week,season_type,report_status,date_modified\nsynthetic-0,BUF,2026,8,REG,Out,2026-11-03T10:00:00Z\n'
        self.assertEqual(parse_csv('injuries',body,2026)[0]['report_status'],'Out')
        def collector(*args):
            kind=args[0]
            if kind=='rosters':return artifact([dict(gsis_id='synthetic-0',season='2026',week='8',team='MIN')],kind)
            return self.collector(*args)
        result=run_job(self.path,self.job('research'),AFTER,collector)
        self.assertTrue(any(c['kind']=='nfl_roster' and c['subject']=='0' for c in result['outputs'][0]['material_changes']))

    def test_scheduled_corrections_retain_cutoff_review(self):
        first=self.outcome(review=review(self.record,'poor'))
        append_outcome(self.path,first)
        result=run_job(self.path,self.job(),AFTER,self.collector)
        complete_outcomes(self.path,result)
        summary=history(self.path,dict(self.record['pack']['request'],as_of=AFTER))
        self.assertEqual(summary['evaluations'][0]['reasoning']['judgment'],'poor')
        self.assertEqual(summary['recurring_errors'],{'small_sample':1})

    def test_context_mismatch_and_bad_task(self):
        job=self.job();job['context']['team_id']='other'
        with self.assertRaises(ValueError):run_job(self.path,job,AFTER,self.collector)
        job=self.job('ai_inference')
        with self.assertRaises(ValueError):run_job(self.path,job,AFTER,self.collector)

    def test_supplemental_csv_boundaries(self):
        rows=parse_csv('rosters',b'gsis_id,team,season,week\nsynthetic,LA,2026,8\n',2026)
        self.assertEqual(rows[0]['team'],'LAR')
        with self.assertRaises(ValueError):parse_csv('injuries',b'gsis_id,team\nsynthetic,BUF\n',2026)

    def test_inactive_sanitized_workflow(self):
        workflow=json.loads(Path('workflows/p24-deterministic-research.json').read_text())
        self.assertFalse(workflow['active']);self.assertEqual(workflow['pinData'],{})
        for node in workflow['nodes']:
            self.assertNotIn('credentials',node);self.assertNotIn('webhookId',node)
            if node['type'].endswith('scheduleTrigger'):self.assertTrue(node['disabled'])
            else:
                self.assertNotIn('{{',node['parameters']['command'])
                self.assertIn('src.fantasy_gm.automation',node['parameters']['command'])
