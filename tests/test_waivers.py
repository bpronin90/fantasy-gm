"""Fictional synthetic P2.3 fixtures; no personal league data or live calls."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.fantasy_gm.research import digest
from src.fantasy_gm.waivers import (RULES, OFFENSE, DEFENSE,
    build_scouting_pack, render_scouting, scout, seal)

NOW = '2026-10-28T13:00:00Z'
CHECK = '2026-10-28T12:45:00Z'
REQ = dict(league_id='synthetic-league', team_id='synthetic-team', season=2026,
           season_type='REG', week=8, mode='test', as_of=NOW)


def fixture():
    def player(pid, pos, slot, team='BUF'):
        return dict(player_id=pid, name='Sample ' + pid, team=team, opponent='MIN',
                    positions=[pos], slot=slot, gsis_id='synthetic-' + pid,
                    identity_verified=True, eligibility_verified=True,
                    eligible_slots=[pos] + (['FLEX'] if pos in ('RB','WR','TE') else []))
    roster = [player('wideout', 'WR', 'WR'), player('runner', 'RB', 'FLEX'),
              player('tightend', 'TE', 'FLEX'), player('pittsburgh', 'DEF', 'DEF', 'PIT'),
              player('reserve', 'WR', 'BN'), player('backup', 'RB', 'BN')]
    candidates = [player('alpha', 'WR', 'BN'), player('bravo', 'WR', 'BN'),
                  player('streamer', 'DEF', 'BN', 'JAX'), player('quarterback', 'QB', 'BN')]
    check = dict(verified=True, source='synthetic manual Yahoo check', checked_at=CHECK)
    s = dict(schema_version=2, platform='yahoo', league_id=REQ['league_id'],
             team_id=REQ['team_id'], season_type='REG', season=2026, current_week=8,
             captured_at=CHECK, is_sample=True, roster=roster, waiver_players=candidates,
             league_settings=dict(verified=True, ppr=1, roster_slots=dict(WR=1,FLEX=2,DEF=1,BN=2),
                                  scoring={'receptions':1, 'sacks':1}, missing_rules=[],
                                  verification={k:check for k in ('scoring','eligibility','locks')}))
    manual = dict(REQ, rules=dict(check, values=RULES),
                  account=dict(REQ, **check, acquisitions_used=3, waiver_priority=None),
                  droppable_ids=['reserve','backup','pittsburgh','runner'],
                  availability=[dict(REQ, **check, player_id=p['player_id'], status='free_agent') for p in candidates])
    pack = build_scouting_pack(dict(row_id=1, snapshot=s), REQ, {}, [], manual)
    # Controlled complete research used to isolate decision rules from HTTP/parser tests.
    pack['evidence'] = []
    for p in roster + candidates:
        for kind in ('identity','statistics','schedule','injury','matchup','news'):
            value = {'kickoff':'2026-11-01T18:00:00Z'} if kind == 'schedule' else {'game_status':'checked_no_designation'} if kind == 'injury' else {'synthetic':True,'ol_qb_injury_check':'verified'}
            pack['evidence'].append(dict(evidence_id=p['player_id']+':'+kind, subject=p['player_id'],
                kind=kind, value=value, season=2026, week=7 if kind == 'statistics' else 8,
                source_url='https://example.invalid/synthetic', observed_at=CHECK, fetched_at=CHECK))
    pack['coverage'] = {p['player_id']:dict(missing=[]) for p in roster+candidates}
    scores = dict(wideout=60,runner=60,tightend=60,pittsburgh=50,reserve=20,backup=25,
                  alpha=80,bravo=70,streamer=65,quarterback=90)
    assessments = {}
    for p in roster+candidates:
        pid = p['player_id']; refs = [pid+':statistics',pid+':injury',pid+':matchup']
        assessments[pid] = dict(rank_score=scores[pid], thesis='Synthetic role comparison.',
            confidence='medium', confidence_reason='Synthetic limited sample.',
            scoring_fit='Synthetic full PPR or defense scoring review.',
            counterargument='Synthetic regression risk.', risk='Synthetic uncertainty.',
            contingency='Keep current roster if claim fails.', recheck_at='2026-10-29T12:00:00Z',
            evidence_ids=refs, factors={k:dict(text='Synthetic evidence for '+k, evidence_ids=refs)
                for k in DEFENSE if p['positions']==['DEF']} if p['positions']==['DEF'] else
                {k:dict(text='Synthetic evidence for '+k,evidence_ids=refs) for k in OFFENSE})
    def pair(add, drop):
        return dict(add=add,drop=drop,drop_penalty=5,replacement_value='Synthetic improvement.',
                    drop_cost='Synthetic loss of reserve upside.',need_fit='Synthetic two-FLEX depth need.',
                    contingency='Use next alternative only if first fails; otherwise retain roster.',
                    evidence_ids=[add+':statistics',drop+':statistics'])
    review = dict(players=assessments,roster_outlook=dict(strengths='Synthetic starters.',
        weaknesses='Synthetic reserve depth.',positional_needs='Synthetic WR/FLEX improvement.',
        evidence_ids=['reserve:statistics']),comparisons=[pair('alpha','reserve'),pair('bravo','reserve'),pair('streamer','pittsburgh')])
    return seal(pack), review


class WaiverTests(unittest.TestCase):
    def test_rank_add_drop_cost_limits_and_defense(self):
        pack, review = fixture(); original = copy.deepcopy(pack)
        result = scout(pack, review)
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['remaining'], 2)
        self.assertIsNone(result['waiver_priority'])
        self.assertEqual(result['success_path'], [1,3])
        self.assertEqual(result['proposals'][0]['net_gain'], 55)
        self.assertEqual(result['proposals'][0]['conflicts_with'], [2])
        self.assertEqual(result['defenses'][0]['team'], 'JAX')
        self.assertEqual(result['defenses'][0]['versus_incumbent'], {'pittsburgh':15})
        self.assertTrue(result['proposals'][2]['lineup'])
        self.assertEqual(pack, original)
        report = render_scouting(pack,result)
        for text in ('sacks_pressure','drop_cost','waiver priority: unknown','free_agent','example.invalid','Mutually exclusive'):
            self.assertIn(text,report)

    def test_unknown_unavailable_stale_conflicting_availability(self):
        for change, expected in (({'status':'unavailable'},'unavailable'),
            ({'checked_at':'2026-10-27T12:00:00Z'},'unknown'),({'league_id':'other'},'unknown'),
            ({'checked_at':'2026-10-29T12:00:00Z'},'unknown')):
            pack, review = fixture(); pack['scouting']['manual']['availability'][0].update(change)
            result = scout(seal(pack),review)
            self.assertEqual(result['availability']['alpha']['status'],expected)
            self.assertNotIn(1,result['success_path'])
        pack, review = fixture(); manual=pack['scouting']['manual']
        manual['availability'].append(dict(manual['availability'][0],status='unavailable'))
        self.assertEqual(scout(seal(pack),review)['availability']['alpha']['status'],'unknown')
        manual['availability']=[]
        self.assertFalse(scout(seal(pack),review)['success_path'])

    def test_waivers_unknown_priority_and_clearance(self):
        pack, review=fixture(); record=pack['scouting']['manual']['availability'][0]
        record.update(status='waivers',clears_at='2026-10-30T07:00:00Z')
        result=scout(seal(pack),review)
        self.assertEqual(result['availability']['alpha']['status'],'waivers')
        self.assertIn(1,result['success_path'])
        record['clears_at']='2026-11-02T07:00:00Z'
        result=scout(seal(pack),review)
        self.assertNotIn(1,result['success_path'])
        self.assertIn('waivers clear after kickoff; no immediate matchup value',result['proposals'][0]['reasons'])
        for clear in (None, '2026-10-27T07:00:00Z'):
            record['clears_at']=clear
            self.assertEqual(scout(seal(pack),review)['availability']['alpha']['status'],'unknown')

    def test_acquisition_rules_and_unknown_account(self):
        for used, remaining in ((5,0),(4,1),(None,None),(-1,None),(True,None),(6,None)):
            pack,review=fixture(); pack['scouting']['manual']['account']['acquisitions_used']=used
            result=scout(seal(pack),review)
            self.assertEqual(result['remaining'],remaining)
            self.assertLessEqual(len(result['success_path']),remaining or 0)
        pack,review=fixture(); pack['scouting']['manual']['rules']['values']=dict(RULES,flex=1)
        self.assertFalse(scout(seal(pack),review)['success_path'])
        pack,review=fixture(); pack['scouting']['manual']['account']['week']=7
        self.assertIsNone(scout(seal(pack),review)['remaining'])

    def test_drop_protection_feasibility_and_negative_gain(self):
        pack,review=fixture(); pack['scouting']['manual']['droppable_ids']=[]
        self.assertFalse(scout(seal(pack),review)['success_path'])
        pack,review=fixture(); review['comparisons'][0]['drop_penalty']=90
        alpha=next(p for p in scout(pack,review)['proposals'] if p['add']=='alpha')
        self.assertIn('replacement gain does not justify drop cost',alpha['reasons'])
        pack,review=fixture(); review['comparisons'][0].update(drop='pittsburgh',evidence_ids=['alpha:statistics','pittsburgh:statistics'])
        alpha=next(p for p in scout(pack,review)['proposals'] if p['add']=='alpha')
        self.assertIn('no complete feasible lineup',alpha['reasons'])

    def test_missing_stale_injury_locked_and_eligibility(self):
        mutations = [lambda p:p['coverage']['alpha']['missing'].append('usage incomplete'),
            lambda p:p['snapshot']['snapshot']['waiver_players'][0].update(eligibility_verified=False),
            lambda p:next(e for e in p['evidence'] if e['evidence_id']=='alpha:injury').update(fetched_at='2026-10-26T12:00:00Z'),
            lambda p:next(e for e in p['evidence'] if e['evidence_id']=='alpha:injury')['value'].update(game_status='out'),
            lambda p:next(e for e in p['evidence'] if e['evidence_id']=='alpha:schedule')['value'].update(kickoff='2026-10-27T18:00:00Z')]
        for mutate in mutations:
            pack,review=fixture(); mutate(pack); pack['snapshot_hash']=digest(pack['snapshot']['snapshot'])
            result=scout(seal(pack),review)
            self.assertNotIn(1,result['success_path'])
            self.assertTrue(result['candidate_gaps']['alpha'])
        pack,review=fixture(); del review['players']['alpha']
        result=scout(pack,review)
        self.assertFalse(any(p['add']=='alpha' and p['rank'] in result['success_path'] for p in result['proposals']))
        pack,review=fixture(); del review['players']['streamer']['factors']['sacks_pressure']
        with self.assertRaisesRegex(ValueError,'sacks_pressure'): scout(pack,review)

    def test_hash_citations_and_real_synthetic_boundary(self):
        pack,review=fixture(); pack['request']['mode']='real'
        with self.assertRaisesRegex(ValueError,'synthetic'): scout(seal(pack),review)
        pack,review=fixture(); pack['generated_at']='tampered'
        with self.assertRaisesRegex(ValueError,'hash'): scout(pack,review)
        pack,review=fixture(); review['comparisons'][0]['evidence_ids']=['alpha:statistics']
        with self.assertRaisesRegex(ValueError,'both'): scout(pack,review)
        pack,review=fixture(); review['players']['streamer']['factors']['turnovers']['evidence_ids']=['alpha:statistics']
        with self.assertRaisesRegex(ValueError,'citations'): scout(pack,review)

    def test_builder_includes_candidates_without_changing_snapshot(self):
        pack,_=fixture(); s=pack['snapshot']; original=copy.deepcopy(s)
        built=build_scouting_pack(s,REQ,{},[],pack['scouting']['manual'])
        self.assertEqual(built['snapshot'],original)
        self.assertIn('alpha',built['coverage'])
        self.assertIn('identity',built['coverage']['alpha']['missing'])
        self.assertFalse(built['constraints']['automatic_transactions'])

    def test_unknown_health_and_public_identity_duplicates(self):
        pack,review=fixture()
        next(e for e in pack['evidence'] if e['evidence_id']=='streamer:matchup')['value']['ol_qb_injury_check']='unknown'
        result=scout(seal(pack),review)
        self.assertIn('opponent OL/QB injuries unverified',result['candidate_gaps']['streamer'])
        self.assertNotIn(3,result['success_path'])
        pack,review=fixture()
        next(e for e in pack['evidence'] if e['evidence_id']=='alpha:injury')['value']['game_status']='unknown'
        self.assertIn('injury status unknown',scout(seal(pack),review)['candidate_gaps']['alpha'])
        pack,review=fixture()
        pack['snapshot']['snapshot']['waiver_players'][0]['gsis_id']='synthetic-reserve'
        pack['snapshot_hash']=digest(pack['snapshot']['snapshot'])
        with self.assertRaisesRegex(ValueError,'duplicate public identity'): scout(seal(pack),review)

    def test_combined_claims_cannot_remove_last_eligible_starter(self):
        pack,review=fixture()
        for p in pack['snapshot']['snapshot']['waiver_players'][:2]:
            p.update(positions=['QB'],eligible_slots=['QB'])
        manual=pack['scouting']['manual']
        manual['account']['acquisitions_used']=2
        manual['droppable_ids'].append('wideout')
        review['comparisons'][1].update(drop='wideout',evidence_ids=['bravo:statistics','wideout:statistics'])
        pack['snapshot_hash']=digest(pack['snapshot']['snapshot'])
        result=scout(seal(pack),review)
        bravo=next(p for p in result['proposals'] if p['add']=='bravo')
        self.assertNotIn(bravo['rank'],result['success_path'])
        self.assertIn('incompatible with earlier success-path claims; re-evaluate separately',bravo['reasons'])

    def test_end_to_end_candidate_and_defense_evidence(self):
        pack,review=fixture(); selection=pack['snapshot']; snapshot=selection['snapshot']
        players=snapshot['roster']+snapshot['waiver_players']
        teams={p['team'] for p in players}|{'MIN','CIN','IND'}
        schedule=[dict(game_id=team+str(week),season='2026',week=str(week),game_type='REG',
            home_team=team,away_team={'BUF':'MIN','PIT':'CIN','JAX':'IND'}[team],gameday='2026-10-25' if week==7 else '2026-11-01',
            gametime='13:00',home_score='20' if week==7 else '',away_score='10' if week==7 else '')
            for team in {p['team'] for p in players} for week in (7,8)]
        identities=[dict(gsis_id=p['gsis_id'],display_name=p['name']) for p in players if p['positions']!=['DEF']]
        stats=[dict(player_id=p['gsis_id'],season='2026',week='7',season_type='REG',team=p['team'],targets='3',carries='1')
               for p in players if p['positions']!=['DEF']]
        team_stats=[dict(team=team,season='2026',week='7',season_type='REG',sacks_suffered='4',passing_interceptions='1',
                         fumbles_lost_total='1',passing_epa='-5') for team in teams]
        def art(rows): return dict(rows=rows,url='https://example.invalid/synthetic',provider='synthetic',fetched_at=CHECK,sha256=digest(rows))
        artifacts={k:art(rows) for k,rows in dict(schedule=schedule,players=identities,stats=stats,team_stats=team_stats).items()}
        official=[dict(subject=p['player_id'],kind=kind,value={'game_status':'checked_no_designation'} if kind=='injury' else {'synthetic':True,'ol_qb_injury_check':'verified'},
            source_url='https://example.invalid/synthetic',manually_verified_official=True,provider='synthetic',
            published_at=CHECK,observed_at=CHECK,fetched_at=CHECK,season=2026,week=8)
            for p in players for kind in ('injury','matchup','news')]
        built=build_scouting_pack(selection,REQ,artifacts,official,pack['scouting']['manual'])
        for pid in built['coverage']: self.assertFalse(built['coverage'][pid]['missing'],(pid,built['coverage'][pid]))
        refs={(e['subject'],e['kind']):e['evidence_id'] for e in built['evidence']}
        def convert(ids):return [refs[tuple(eid.split(':'))] for eid in ids]
        for a in review['players'].values():
            a['evidence_ids']=convert(a['evidence_ids'])
            for factor in a['factors'].values():factor['evidence_ids']=convert(factor['evidence_ids'])
        for c in review['comparisons']:c['evidence_ids']=convert(c['evidence_ids'])
        review['roster_outlook']['evidence_ids']=convert(review['roster_outlook']['evidence_ids'])
        result=scout(built,review)
        self.assertEqual(result['success_path'],[1,3])
        self.assertTrue(any(e['subject']=='streamer' and e['kind']=='opponent_statistics' for e in built['evidence']))
        metric=next(d for d in built['derived'] if d['subject']=='streamer' and d['metric']=='opponent_sacks_suffered')
        self.assertEqual(metric['value'],4)
        self.assertIn('schedule_outlook',{e['kind'] for e in built['evidence']})
        artifacts['team_stats']['rows']=[r for r in team_stats if r['team']!='IND']
        missing=build_scouting_pack(selection,REQ,artifacts,official,pack['scouting']['manual'])
        self.assertIn('opponent latest-game statistics incomplete',missing['coverage']['streamer']['missing'])

    def test_cli_writes_only_private_files_and_no_ledger(self):
        pack,review=fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'pack.json').write_text(json.dumps(pack)); (root/'review.json').write_text(json.dumps(review))
            command=[sys.executable,'-m','src.fantasy_gm.waivers','report','--pack',str(root/'pack.json'),'--review',str(root/'review.json'),'--output-dir',str(root/'output')]
            run=subprocess.run(command,text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual({p.name for p in (root/'output').iterdir()},{'scouting-report.md','scouting-evaluation.json'})
            command[-1]='data/samples/forbidden-output'
            run=subprocess.run(command,text=True,capture_output=True)
            self.assertNotEqual(run.returncode,0)
            self.assertIn('ignored data/private',run.stderr)


if __name__=='__main__': unittest.main()
