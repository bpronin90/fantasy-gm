# Phase 2 implementation plan

Planning date: 2026-10-28. Status: proposed; no implementation or deployment authorized by this document. The first delivery is a sourced, roster-specific weekly lineup report with a saved recommendation record. Waivers, retrospective analysis, and scheduling follow it.

## 1. Verified baseline

Read all 12 tracked repository files, including historical SDK references. Each local file's blob matched the corresponding GitHub default-branch file when inspected through the GitHub connector. GitHub has no issues returned by the repository issue search and no existing issue template in this checkout. Local HEAD is `3466d2f`. Current definitions are `workflows/*.json`; historical TypeScript is not a deployment source.

Read-only n8n MCP inspection confirmed:

| Artifact | Current finding |
| --- | --- |
| Import `h1I6YrxmgiL62zPY` | Four nodes; inactive; no published version; draft `3367085a-3bc4-4724-9a91-bd132495c8a5` |
| Research Pack `D2FpuQGkQlRXwU2z` | Three nodes; inactive; no published version; draft `f0f88221-1711-437d-9caa-066ed10e9bce` |
| Definitions | Every node parameter, embedded script, and connection inspected; live nodes match exports after excluding the instance webhook identifier |
| Import execution 8 | Status success, but destination and only executed node were Roster JSON Form; neither raw preservation nor snapshot persistence ran |
| Research Pack execution 9 | All three nodes ran; output was sample snapshot row 1 with unverified settings |
| Execution history | Latest listed executions remain Import 8 and Research Pack 9; no newer run observed |
| Data Tables | Two existing tables: Submissions with raw_json string; Snapshots with snapshot_json string, league_id string, is_sample boolean, captured_at **date** |

No workflow was executed, modified, published, replaced, or recreated. No private input or execution payload is included here. Execution 8's success proves only trigger completion. The earlier diagnostic WSH-to-WAS validation success is user-reported history, not a newly performed integration test.

All five existing offline tests pass (`python3 -m unittest discover -s tests -v`). They cover a valid synthetic snapshot, limited team/slot rejection, pack constraints, sanitized exports, and common secret patterns. They do not prove persistence, browser submission, current identities, research collection, or recommendation quality.

### Existing data flow and gaps

Import: Form → Preserve Raw Submission → Validate Snapshot → Save Snapshot. The validator parses schema v1, validates basic context, names, positions, teams and slot compatibility, then stores the complete JSON. Research Pack: Manual Trigger → highest snapshot row ID globally → grouping and assigned-slot gaps. It collects no external research. `src/` contains only guidance; schemas exist implicitly in Code nodes and the synthetic fixture, with no standalone contract files.

Concrete corrections required:

- `WSH` fails for team and opponent because only `WAS` is accepted. Normalize documented source aliases before canonical validation, recording changes; reject unknown aliases. Preserve null and BYE, and reject FA as an opponent.
- Highest row ID means newest import, not newest capture or correct context. A recently imported old/sample/other-league snapshot can win. There is no fantasy team identifier to separate two managers in one league.
- `ppr` and one verified boolean do not establish a full scoring system. Passing touchdowns/interceptions, bonuses, kicking, and DEF rules are unchecked. Duplicate IDs can evade detection when teams differ. Slot assignment counts can exceed configured capacity; a slot can be valid globally but absent from this league.
- ISO validation permits timestamps without explicit timezone; pack code trusts persisted JSON without revalidation. Missing snapshots can stop upstream with zero items, never reaching the intended Code-node error.
- The synthetic fixture has one FLEX. The product requires two; this is a fixture limitation, not proof of real league configuration. Preserve incomplete rosters as explicit gaps rather than rejecting every underfilled lineup.
- README isolated setup says captured_at is a string, but the live table uses date. Future implementation must reconcile that mapping and test timestamp round trips.
- Raw submissions are written before validation and retained indefinitely, including oversized/invalid attempts. Add a bounded retention proposal; do not delete anything without separate authorization.
- The existing prompt has sound guardrails but lacks a report contract, comparison rubric, cutoff checks, and a persisted decision reference.

## 2. Keep the architecture small

Retain n8n ingestion/calculation/orchestration, structured Research Pack evidence, user-initiated Codex reasoning, and a Decision Ledger. Do not add an n8n AI Agent, vector database, separate service, paid model API, generic plugin framework, or prediction training pipeline. No Yahoo OAuth or transaction client is needed for the first report.

Use existing built-in HTTP/Data Table/Code nodes where practical. Python/Node standard-library helpers under src/ are justified only for cumbersome parsing or deterministic offline verification; do not duplicate executable rules across helpers and workflow scripts. Continue testing exported Code nodes directly. Start with CSV/JSON source artifacts rather than adding R, pandas, or parquet dependencies. Do not load an entire multi-season play-by-play archive into n8n for one weekly report.

Independent reasoning means a transparent comparison of role, opportunity, scoring fit, health, and matchup. It does not require inventing precise projections. Label any supplied Yahoo projections as a separate comparator; they are not evidence of role or the objective function.

## 3. Proposed contracts and storage

These are design proposals, not implemented schemas. Add human-readable contracts and dependency-free validation with the implementation issues. Version incompatible changes explicitly; retain v1 imports as historical inputs and never silently mark missing fields verified.

| Contract | Minimum contents and invariants |
| --- | --- |
| Snapshot v2 | Existing fields plus team_id; context includes league_id, season, current_week and season_type; timezone-qualified captured_at; normalization version and changes; stable per-player identity; scoring and eligibility verification metadata. v1 requires explicit team context before real use. |
| Settings | roster_slots with FLEX count 2 when confirmed; scoring rules keyed by stat, coefficients/thresholds and explicit missing rules; verification per section with source and checked_at; waiver type, weekly cap, used acquisitions and priority nullable; kickoff/IR rules explicitly verified or unknown. Preserve legacy verified=false. |
| Player identity | Internal stable key, optional Yahoo ID, GSIS ID and PFR ID; source mappings with resolution status, evidence and effective date; team defense keyed by canonical team plus season. NFL position is distinct from Yahoo eligible slots. Never auto-resolve ambiguous names. |
| Report request | Explicit league_id, team_id, season, season_type, week, as_of UTC, mode real/test, optional snapshot_row_id. Real mode forbids samples. No implicit global latest context. |
| Evidence item | evidence_id, subject/player/game IDs, kind, structured value and units, source URL and provider, published_at nullable, observed_at/report date, fetched_at, season/week or effective interval, source revision/hash, status, and conflict references. Missing publication time stays null. |
| Research Pack v2 | pack_id and version, request, snapshot reference/hash, settings status, resolved roster, schedule/kickoffs, evidence items, derived metrics with input IDs/formula version, coverage by player/category, freshness policy/results, conflicts, missing evidence, blocked decisions and advisory constraints. generated_at never substitutes for evidence age. |
| Decision record | decision_id, report_id, pack_id/hash, context, created_at, decision cutoff, settings version, recommended player-to-slot assignments, alternatives, evidence IDs, thesis, confidence ordinal/reason, risks, counterarguments and conditional actions. Record user adoption separately as unknown unless supplied. |
| Outcome record | decision_id, outcome version/as_of, source IDs, actual stats, scoring version, actual points, feasible alternative points, evaluation and correction state. Append corrections; never overwrite original reasoning. |

Snapshot selection: filter all context fields and is_sample=false **before** choosing. Validate candidates, reject future captures beyond a small documented clock tolerance, sort by captured_at then row ID, enforce age and week, and fail explicitly if no eligible candidate. An explicit row ID still passes all guards. Do not limit the global query to one row and filter afterward. For the initial small store, bounded pagination and JSON-field filtering avoid a premature table migration; if a scan limit is reached, report incomplete selection rather than silently choosing. Add indexed context columns later only when justified.

Keep existing tables. Propose one additional Evidence Cache table (cache_key string, payload_json string, fetched_at/expires_at date, status string) and one Decision Ledger table (record_id string, decision_id string, record_type string, context_key string, created_at date, payload_json string). Decision and outcome records share the ledger via record_type. Verify Data Table payload size and retry/idempotency behavior before choosing payload limits. Use stable record keys and explicit duplicate checks for this single-user system; no distributed event infrastructure.

Private pack/report copies may live in ignored data/private/ for reproducibility; table data and real reports never enter Git. Store the exact evidence values supporting decisions, not just mutable URLs. Default proposal: raw submissions 14 days, cache bodies 30 days, cited evidence and ledger through the season plus retrospective review; deletion requires approval.

### Proposed runtime flow

1. User requests league/team/week analysis and supplies or confirms a private snapshot and settings.
2. Import normalizes and validates, preserves unknowns, and saves. Confirm Save Snapshot and persisted row, not merely execution status.
3. Research Pack selects exact context and resolves identities; missing context blocks real analysis.
4. n8n fetches/reuses source data, checks season/week/revision, normalizes evidence and computes opportunity/trend metrics. Official injury/news research can initially be manually reviewed by Codex and attached as cited structured evidence; do not delay the first report for a general-purpose scraper.
5. Pack records coverage and blocked decisions. Codex obtains any remaining current evidence, then freezes a report input pack before issuing recommendations.
6. Codex compares feasible complete lineups, produces the report, and saves a validated decision record through a future explicitly authorized ledger operation. A persistence failure is visible; an unsaved report is not called recorded.
7. Later, ingest actual results and append outcome evaluation using the same scoring and original decision cutoff.

## 4. Free research sources and limits

Documentation/pages checked on the planning date. Documentation availability is not verification that every current-season data artifact is complete or that a local collector works.

| Need | Recommended source | Cadence, reliability and limits |
| --- | --- | --- |
| Game IDs, opponents, kickoff | nflverse schedules; cross-check material changes against official NFL/team schedules | Publisher documents five-minute schedule refresh. Proposed local cache 1 hour, tightened to 15 minutes near locks. Rescheduling and time zones require explicit handling. |
| Player identity and team | nflverse players plus dated rosters; official team roster/transactions for discrepancies | Players uses GSIS primary keys; roster refresh documented daily at 07:00 UTC. Cache 24 hours, refresh identity/team conflicts immediately. Yahoo eligibility and mappings require manual verification; never infer from NFL position. |
| Weekly results and opportunities | nflverse weekly player/team stats | Supports CSV and weekly summaries. Updates follow game-day/nightly pipelines; corrections arrive later. Require expected completed games, season and week, not just a recent download. Compute carries, targets, team target share, and last-three-games versus season trends with denominators. |
| Snap usage | PFR snap counts distributed through nflverse | Polling documented four times daily, upstream dependent. Use PFR ID crosswalk; snaps do not establish routes run. Mark missing games incomplete. |
| Injuries | Official NFL injury page plus dated team practice/game-status reports and inactive announcements | Event-driven publications; manually reviewed first. Proposed cache at most 6 hours during report preparation, refresh at final decision and when status changes. Distinguish practice participation, game status and active/inactive; blank/absent is not healthy. No stable public JSON API is assumed. |
| NFL news and role changes | Dated official team news, transactions, press conferences and NFL reporting | Use direct links, timestamps and attributable claims. Team comments can be optimistic; distinguish quotes, observed usage and inference. Select roster-relevant items instead of building a broad news crawler. |
| Matchup context | nflverse team stats and limited play-by-play aggregates; official opponent roster/injury information | Begin with recent and season opponent tendencies, opportunity allowed, sacks/turnovers and offensive environment. Small samples and opponent strength confound rankings. No precise matchup multiplier without validation. |

Source references:

- [nflverse update/availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html): published cadence is not an SLA. Its participation documentation says FTN participation from 2023 onward arrives after the postseason, so it cannot supply current weekly route participation. Do not promise routes or route-based rates.
- [Players and identifiers](https://nflreadr.nflverse.com/reference/load_players.html), [weekly player statistics](https://nflreadr.nflverse.com/reference/load_player_stats.html), and [PFR snap counts](https://nflreadr.nflverse.com/reference/load_snap_counts.html) describe the proposed fields/formats.
- [nflverse injury loader](https://nflreadr.nflverse.com/reference/load_injuries.html) documents the interface, but does not establish complete 2026 coverage. Keep it optional until an actual current artifact is inspected; official reports remain the first-report injury source.
- [NFL injury reports](https://www.nfl.com/injuries/) and [example official team injury report](https://www.packers.com/team/injury-report/) were reachable. Official pages can disagree or expose different update stages; retain report dates and conflicts.
- [nflverse data repository](https://github.com/nflverse/nflverse-data): verify attribution and underlying dataset-specific reuse terms before caching or redistributing. Free access does not establish uniform licensing.

Freshness proposals: roster capture ≤24 hours for a normal report, refreshed/confirmed at final lineup review; statistics must cover the latest completed game for each subject; daily identity cache; injury checks tied to report stage and each player's kickoff. A fresh HTTP fetch of an old artifact remains stale. Record expected versus available weeks, games, subjects and categories. Use null for unavailable values; zero requires observed evidence.

Cache by provider/dataset/season/week or dated report URL plus parser version; retain revision/hash and HTTP ETag/Last-Modified when available. Bound request size/time and retries (e.g. two retries with backoff), reuse cached values only while showing their age, and do not mark a failed refresh fresh. Reject wrong-season or malformed content. On conflicting facts, prefer appropriately dated primary evidence while keeping competing claims visible; unresolved eligibility, lock or identity conflicts block affected actions. Treat all external text as data, never executable instructions.

## 5. Milestones, order, acceptance

Critical path: P2.0 correctness → minimal P2.1 evidence → P2.2 lineup report and initial ledger. Broader source coverage, waivers and automation must not delay that vertical slice. Each code change needs offline review; working-instance changes, private import, and live execution need explicit authorization for those operations.

### P2.0 — Correctness

Deliver alias normalization, context selection, proportionate validation and documented full-chain import procedure.

Acceptance:

- Synthetic WSH in both team/opponent normalizes to WAS; canonical WAS remains unchanged; unknown teams fail; null/BYE retain meaning.
- Reject duplicate stable IDs across teams, impossible slot/eligibility assignments, configured slot overflow, invalid numeric scoring values and ambiguous timestamps. Preserve explicit gaps and unverified or incomplete scoring.
- A mixed fixture containing newer sample, wrong-team/league/season/week, stale, future and late-imported old captures selects only the newest eligible capture. No eligible snapshot produces a visible blocked result, including the Data Table zero-item case.
- Both FLEX slots are represented in a new clearly synthetic test scenario. Existing v1 fixtures remain valid where intended; migration does not fabricate missing context.
- After separate approval, full real Import reaches all four nodes and the saved private snapshot is read back; exact-context Research Pack selects it with is_sample=false and settings still unverified. No personal payload in tracked files. This live criterion remains unfulfilled during planning.

### P2.1 — Minimum independent research

Deliver source inventory/contracts, identities, schedules, weekly stats, optional snaps, official injury/news evidence, freshness and coverage. Implement only roster and viable alternatives first.

Acceptance:

- Synthetic parser fixtures cover valid/missing columns, malformed content, wrong season/week, timeout, stale cache, correction, conflicting reports and ambiguous IDs. All failures remain explicit.
- Every roster player has an identity, kickoff/opponent, latest-game coverage status and injury evidence status; unresolved subjects are labeled and affected recommendations blocked or conditional.
- Derived target/carry shares and trends have tested denominators, sample sizes and input references; missing snaps/routes never become zero or invented metrics.
- After authorized collection, inspect actual current-season artifacts for latest completed games; confirm source URLs and timestamps are usable. No claim of working integration based only on documentation.

### P2.2 — First useful weekly GM report

Deliver lineup comparison, report prompt/contract and initial persistent ledger. Verify actual rules manually; no Yahoo API prerequisite.

Acceptance:

- Report names league/team/season/week, pack and snapshot references, evidence cutoff, verified rules, assumptions and blockers. It includes every required starter slot, K/DEF where applicable, both FLEX slots, ranked bench alternatives and at least one meaningful close-call comparison when one exists.
- Deterministic feasibility tests cover multi-position players, no double use, two FLEX assignments, IR exclusions, byes/out status, individual kickoff locks, timezone/DST boundaries, and unknown eligibility. Preserve already locked assignments. Prefer later kickoffs in FLEX when it preserves optionality without changing chosen players.
- Each material recommendation cites evidence, explains opportunity/scoring fit, confidence with reasons, counterargument, risk and a feasible contingency with a recheck deadline. Discuss recent usage versus season baseline and challenge projection/brand-name anchors where evidence warrants it.
- Unknown critical scoring/eligibility produces a conditional comparison or blocked final lineup, never false certainty. Full-PPR assumption remains labeled until verified; do not compute precise points with unspecified rules.
- Save immutable decision/evidence references before kickoff; retry does not duplicate records, and failed persistence is reported. Synthetic tests ensure hostile research text cannot change report constraints or request transactions.
- One authorized real weekly report passes manual factual/source and lineup review. This is Phase 2's first product delivery gate, not satisfied by a synthetic report alone.

### P2.3 — Waivers and defensive streaming

Depends on P2.2 plus a timestamped, manually supplied availability list and verified waiver rules.

Acceptance:

- Recommend add/drop pairs using role/opportunity trend, replacement value, roster fit, upcoming schedule and drop cost; explicitly account for rolling priority, pending claims and five acquisitions/week.
- Enforce remaining acquisitions = limit minus confirmed used, with unknown remaining budget labeled. Alternative claims in one contingency group must not be counted as simultaneous guaranteed acquisitions.
- Availability has known available/rostered/unknown plus source and checked_at. Unknown candidates remain watchlist or conditional; never promise a successful claim.
- DEF analysis uses opponent offense, sacks/pressure proxies actually available, turnovers and scoring environment with uncertainty; no reliance solely on Yahoo rank or last week's DEF points. Compare at least two feasible candidates when available.
- Synthetic tests cover unavailable players, unknown priority/usage, exhausted acquisition limits and competing drop targets. No roster mutation path exists.

### P2.4 — Evaluation and scheduling design

Depends on recorded pregame decisions and verified actual statistics/scoring; scheduling requires further authorization.

Acceptance:

- Outcomes join by stable identity/game, handle DNP and scoring corrections, and preserve original recommendation and adoption status. Compare only alternatives that were eligible and actionable at the decision cutoff.
- Evaluate total points and feasible-lineup regret separately from evidence quality, missing information and injury/usage surprises. Do not equate every losing choice with bad reasoning or claim causal proof from a few weeks.
- Review confidence versus results descriptively with sample sizes; record recurring errors and version future rubric changes without rewriting history. Trade evaluation remains a later report extension requiring both sides' assets/context, roster costs and explicit uncertainty; no premature trade-value model.
- Design deterministic n8n schedules for research/cache/report-ready notifications with timeouts, freshness checks and duplicate prevention. Scheduled AI reasoning is a separate capability to verify; an existing Codex subscription is not proof of an unattended integration. Start with user-opened Codex reports and no extra paid AI API.
- No schedules/publication enabled until separately approved and tested; transactions remain advisory only.

## 6. Recommended issue backlog

No GitHub issues were created during this task. These are ready-to-file scopes following the repository's simple style, with no invented project taxonomy. Keep live deployment checks as separately approved operations rather than implicit effects of merging code.

| Order / proposed issue title | Scope and definition of done | Depends on |
| --- | --- | --- |
| 1. P2.0: Normalize source team aliases in Import | WSH→WAS for team/opponent, normalization audit metadata, synthetic edge cases, unknown rejection; reviewed JSON export and offline tests; no live workflow changes | Implementation authorization |
| 2. P2.0: Select snapshots by explicit report context | team/context contract, capture-time selection, real/test gate, freshness, zero-item blocked result, mixed-context fixtures | 1 |
| 3. P2.0: Validate league rules and verify full Import path | Scoring/eligibility status, slot capacities/two-FLEX fixture, ID uniqueness, timezone checks, table date mapping and private verification checklist; authorized live check tracked separately | 1, 2 |
| 4. P2.1: Define evidence contract and verify source coverage | Small source registry, current artifact inspection, identity crosswalk strategy, dataset terms, fixtures, freshness/coverage rules | 2; can overlap 3 |
| 5. P2.1: Collect the minimum weekly evidence pack | Schedule/IDs/weekly stats, optional snaps, bounded cache/retries, metrics with provenance, official injury/news attachment and conflict handling | 3, 4 |
| 6. P2.2: Compare legal lineups and specify the GM report | Eligibility/locks/two FLEX feasibility, transparent comparison rubric, report contract, conditional injury scenarios and prompt update | 3, 5 |
| 7. P2.2: Persist recommendations and deliver the first weekly report | One ledger table, idempotent decision write, private evidence retention, manual report review and authorized real report acceptance | 6 |
| 8. P2.3: Rank advisory waiver add/drop opportunities | Availability contract, rolling priority, five-acquisition budget, replacement value and claim contingencies | 7 |
| 9. P2.3: Compare defensive streaming candidates | DEF scoring, independent matchup evidence, candidate alternatives and limitations | 5, 8 |
| 10. P2.4: Append actual outcomes and weekly retrospective | Corrected results, versioned scoring, feasible alternatives, reasoning-versus-luck rubric and error review | 7 and completed games |
| 11. P2.4: Design and validate scheduled research readiness | Deterministic schedule proposal, notification destination/approval, duplicate suppression and explicit AI capability boundary; no automatic publication | 5, 7 |

Each issue inherits milestone acceptance above; split oversized collection work by source only if actual implementation warrants it. Review the full staged diff and run the existing unittest command before any commit. Do not add tests that merely restate schemas; prioritize selection errors, computation errors, lock constraints and evidence failure modes.

## 7. Risks, unknowns and approval decisions

- **Immediate authorization:** approve issue 1 as a repository-only implementation task. That does not authorize changing or running Import `h1I6YrxmgiL62zPY` or Research Pack `D2FpuQGkQlRXwU2z`. Working-instance modification, real snapshot import/readback execution, publication and schedules need specifically scoped authorization later.
- **League truth:** actual team context, scoring including K/DEF and bonuses, two FLEX eligibility, kickoff/IR behavior, rolling priority and weekly reset/acquisition accounting need private manual confirmation. Yahoo API remains pending; verifying screenshots/manual settings does not require it. Do not mark settings verified simply because a real roster imports.
- **Freshness/retention policy:** proposed TTLs are engineering defaults, not guarantees; approve or adjust before deletion or scheduled polling. Recheck injury information at actionable cutoffs.
- **Source fragility:** documented schedules and reachable pages do not prove current data completeness. Third-party feeds can fail, HTML can change, IDs can be missing, and reuse rights differ. A manually sourced injury/news step is an acceptable first delivery.
- **Privacy:** ledger and snapshots are personal even in a private repository. Test fixtures must stay fictional. Never export Data Tables or execution outputs into Git; no public form or remote notification destination by default.
- **Reasoning quality:** confidence is qualitative initially, not a calibrated win probability. Current role and matchups must be evidenced; route data and unknown availability cannot be inferred. Historical corrections must not leak into pregame evaluation.
- **Execution boundary:** the form-only execution is an operational testing mistake to prevent with node-completion and persistence checks, not evidence that the workflow graph needs rebuilding.

## Executive summary

Phase 1 is a small, usable ingestion foundation, but no real full-chain persistence or researched GM report has been verified. Keep it. First fix source team aliases, then eliminate ambiguous snapshot selection, verify private league settings/full Import, and collect the minimum attributable weekly evidence. Deliver a two-FLEX, lock-aware lineup report and persistent decision record before expanding to waivers or automation.

Recommended first implementation issue: **P2.0: Normalize source team aliases in Import**, restricted to repository changes and synthetic offline tests until deployment is explicitly authorized. Decisions needed now: whether to authorize that scope and whether to accept the proposed first-report path with manual official injury/news verification. Private league-setting confirmation and approval for exact live operations follow when the reviewed changes are ready. Planning completion does not complete any Phase 2 milestone.
