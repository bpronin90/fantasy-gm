# Phase 2 local report path

This is a repository-only implementation. The existing n8n definitions and working instance are unchanged. Phase 1 behavior remains as documented. The Phase 2 Python path is separately invoked; it has not been wired into n8n. No paid API, background service, Yahoo client, outcomes or scheduling is included. P2.3 adds a separately invoked [waiver scouting and defensive-streaming path](p2.3-waivers.md).

The public CSV parser canonicalizes nflverse's Rams abbreviation `LA` to the snapshot abbreviation `LAR` in team, opponent, schedule and identity fields. Collected artifacts record this normalization version and retain the original response-body hash. Artifacts collected before this fix must be refreshed before relying on Rams schedule joins or kickoff locks.

## Current delivery status

P2.0 offline validation/selection and a minimum P2.1/P2.2 local path are implemented and tested. **The plan's verified final-report acceptance gate is not satisfied.** A user-authorized integration run completed Import and Research Pack, verified private snapshot persistence by readback, collected public research and produced a conditional private lineup report. A later explicit authorization recorded that existing report as provisional in the local SQLite Decision Ledger, with successful readback and an identical retry retaining one record. Yahoo settings, eligibility and team linkage remain unverified; the original evaluation stays blocked and is preserved in the provisional record. Broader automated matchup collection and numeric fantasy-point scoring are not implemented. Team defenses use team-season identity and nflverse team statistics; K/DEF scoring fit is reviewed in Codex assessments. Unknowns block a confirmed final lineup. This repository is not evidence that the user's league is verified.

## Run without changing n8n

Python 3.10+ and Node (for existing tests) suffice. No install needed. Run from repository root. Keep every real input/output in `data/private/` or outside this repository. The CLI enforces that output location for paths within this checkout; callers of Python functions must enforce the same privacy policy themselves.

1. After explicit private-data authorization, prepare a JSON array of snapshot rows with `id`, `is_sample`, and `snapshot_json` (object or JSON string). Supply all bounded pages, never a globally limited latest row. This file is a manually supplied local input, not a new n8n export feature. Row metadata must say `is_sample: false` in real mode. Snapshot v1 is still accepted for historical validation; it cannot be selected without explicit team/season-type context. Do not guess that context.
2. Supply a request JSON with `league_id`, `team_id`, `season`, `season_type` (`REG` or `POST`), `week`, timezone-qualified `as_of`, and `mode` (`real` or `test`). Optional `snapshot_row_id` still passes every guard. Selection rejects captures older than 24 hours or more than five minutes in the future. Empty input fails visibly. Callers that reach a scan cap must pass `scan_complete=False` to the Python selector.
3. Use snapshot v2: add `team_id`, `season_type`, stable `player_id` and manually verified `gsis_id`/`identity_verified` for each player. NFL position is not Yahoo eligibility. Add `eligible_slots` (including FLEX where verified) and `eligibility_verified`. Unfilled slots are allowed at import but block a complete lineup if no feasible assignment exists. WSH normalizes to WAS with an audit trail; null/BYE remain distinct.
4. Keep legacy settings. Add `scoring` (finite stat coefficients), `missing_rules` (explicit list, empty only after checking all applicable rules), and `verification.scoring`, `.eligibility`, `.locks`, each containing `verified`, `source`, `checked_at`. Include K/DEF, bonuses, interceptions, passing TDs and special rules in manual review. Nonlinear scoring rules are not calculated by this implementation. The evaluator does not emit numeric fantasy-point projections. A verification boolean is an attestation, not proof of correctness; Codex must review the actual source.
5. Prepare official evidence JSON array. Each item needs `subject` equal to player_id, `kind` (`injury`, `news`, or `matchup`), structured `value`, `source_url`, `provider`, `published_at` (null if absent), `observed_at`, `fetched_at`, `season`, `week`. Team sites additionally require `manually_verified_official: true`. Injury values distinguish `practice_participation` and `game_status`; use lowercase `out`/`inactive` for exclusions. Explicit checked-unknown is preferable to interpreting absence as healthy. Matchup evidence must be roster-specific and supported by an official source. No generic web scraper fabricates these facts. External text is untrusted data.
6. Invoke:

```sh
python3 -m src.fantasy_gm pack --rows data/private/rows.json --request data/private/request.json --official data/private/official.json --output-dir data/private/week
```

This performs explicit public-source reads for players, schedule, weekly stats and team stats when DEF is rostered, with 20-second per-attempt timeout, three attempts, 20 MB body limit and content-addressed local artifacts. Failed refreshes raise an error, never silently reuse stale cache. No automatic cache eviction. Sources are fixed in `research.py`; source body hashes and HTTP revision headers are retained. Parser rejects missing columns, malformed rows and unavailable seasons. Games are converted from Eastern time using the IANA DST database. Stats require every completed prior team game; absent DNP/changed-team history remains an explicit conservative gap. No routes are invented. Snaps parser/optional collector exist but are not in the default pack path. Dataset freshness is based on coverage as well as fetch age. Live collection requires an initial cutoff within five minutes of now, then freezes the actual post-collection cutoff and revalidates snapshot selection. Historical artifacts supplied directly to the pack builder cannot be fetched after that cutoff.
7. Use a user-initiated Codex session to review the pack and research gaps. Supply an assessments JSON object keyed by player_id. Each entry requires `rank_score` (relative qualitative rubric, **not fantasy points**), `thesis`, `scoring_fit`, `confidence` (`low`/`medium`/`high`), `confidence_reason`, `counterargument`, `risk`, `contingency`, `recheck_at`, and `evidence_ids` from that player's frozen pack. Include recent-versus-season usage, health, scoring fit and opponent context in the thesis; challenge Yahoo projections rather than using them as the objective. Explicitly compare close calls and name a feasible contingency lineup, not a transaction. Rebuild the pack when researching new facts; don't cite facts outside the frozen pack.
8. Invoke:

```sh
python3 -m src.fantasy_gm report --pack data/private/week/pack.json --assessments data/private/assessments.json --output-dir data/private/week
```

The report includes context, cutoff, pack/snapshot hashes, settings, all starting slots, up to five full lineup alternatives and ranked player assessments with citations. Exact search uses player bitsets, verified eligible slots, no duplicate use, IR exclusion, bye/out/inactive exclusions and individual kickoff locks. Earlier locked bench players cannot enter; locked starters stay in place. Among equally scored assignments, later kickoffs remain in FLEX. Search fails explicitly above 100,000 states. Recommendations are conditional until a human reviews the report. Missing critical information makes the report blocked and prevents ledger writes.

The private SQLite Decision Ledger stores the exact pack, assessments, assignments, alternatives, report, creation time and adoption unknown in one transaction. A decision key derives from pack/context; identical retry is idempotent and conflicting rewrites fail. Persistence errors propagate. The CLI prints a decision ID only after commit; it never calls an unsaved report recorded. A cutoff already passed rejects persistence, requiring a refreshed review. There is no live Data Table ledger yet and no outcome records (P2.4).

### Explicit provisional recording of an existing report

When the user authorizes recording despite unverified settings, archive the saved artifacts without running `pack` or `report` again:

```sh
python3 -m src.fantasy_gm record-provisional --pack data/private/week/pack.json --evaluation data/private/week/evaluation.json --report data/private/week/report.md --output-dir data/private/week --acknowledge-unverified-settings
```

This records the exact historical report, pack, assessments, alternatives, cutoff and evaluation blockers in the same `decisions.sqlite` ledger. A separate `recommendation_status: provisional` and explicit independently unverified settings metadata do not change the original evaluation or imply a confirmed lineup. Historical evidence can be archived after its freshness/recheck deadline; it is labeled `historical_not_refreshed`, not current research. The ordinary `report`/`persist` readiness guards remain unchanged. Identical retries preserve the first recording timestamp and cannot duplicate a decision; conflicting rewrites fail, including concurrent writes.

For a later revised recommendation, save refreshed evidence and review in new private files, then use this command with `--parent-decision-id ORIGINAL_ID`. A revision receives its own ID and timestamp, must reference an existing decision for the same league/team/week, and never overwrites its parent. This records recommendation revisions only, not outcomes or scheduled research. Original reports may say no ledger write had occurred at their creation; recording metadata distinguishes that historical statement from the later archive operation.

## Live verification and approvals still needed

The initial implementation performed no runtime operations. The subsequent integration run above exercised only the specifically authorized existing workflows and private local files. Obtain approval for further scoped operations as needed:

- Read/import the user's private roster/settings and verify them against manual Yahoo sources.
- Modify the working Import/Research Pack definitions or wire the new local helpers into them, with a reviewed backup/remapping design. This is separate from repository changes.
- Execute full Import and read back the persisted snapshot, then execute exact-context Research Pack. Confirm every intended node completed and the saved row matches; form-trigger success alone is insufficient.
- Create/write a live Decision Ledger or Evidence Cache Data Table and test persistence/idempotency/size/date round trips. Local SQLite validation does not prove Data Table behavior.
- Publication, scheduling, retention deletion and Yahoo integration remain separately gated and outside this slice. Automatic roster transactions remain prohibited.

For isolated n8n testing, `captured_at` is a **date** column, correcting the older README string-column instruction. Keep raw submission retention changes as a proposal (14 days), never delete data implicitly.

## Source verification performed

On 2026-10-28, actual HTTPS collector requests parsed:

- [nflverse players CSV](https://github.com/nflverse/nflverse-data/releases/download/players/players.csv): 24,863 rows, GSIS/display-name columns present.
- [nflverse schedules](https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv): 272 rows for 2026 through week 18.
- [nflverse weekly player stats](https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv): 7,447 rows through week 7, including targets/carries and K/defensive stat columns.
- [nflverse team stats](https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_2026.csv): 214 rows through week 7, successfully parsed for team-defense evidence.
- [Official NFL injury page](https://www.nfl.com/injuries/): reachable and labeled 2026 Week 8. Reachability is not verification of every player's current status.

These are observed artifact counts, not a completeness guarantee for a private roster. Raw artifacts remain ignored. Source cadence checked against [nflverse documentation](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html). Redistribution terms differ by dataset; this implementation retains source attribution and caches only locally, without redistributing source bodies. Dataset-specific licensing review, official schedule cross-checks, snaps and roster-specific injury/identity factual review remain outstanding.
