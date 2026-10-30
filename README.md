# Fantasy Football GM — Phase 1

An advisory fantasy-football toolkit with optional local n8n workflows and a
separately invoked Python research/report path. No automatic roster transactions
or paid AI API is required. Sample data is fictional.

## Start from a fresh checkout

Install Git, Python 3.10+, and Node.js (CI uses Node 22). No Python or npm package
installation is needed for the offline helpers and tests:

```sh
git clone https://github.com/bpronin90/fantasy-gm.git
cd fantasy-gm
python3 -m unittest discover -s tests -v
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making changes. The offline
[Phase 2 usage guide](docs/phase-2-usage.md) does not require n8n or private agent
configuration. To use the Phase 1 forms, first follow the isolated n8n setup below.
The templates were developed against n8n 2.45.5 and are inactive by default.

## Enter your roster

1. Copy data/samples/sample-roster.json to an ignored location outside data/samples/ and replace all sample players, league_id, season, current_week, captured_at, roster slots, and waiver players with your Yahoo data.
2. Set is_sample to false. Keep league_settings.verified false until checked against Yahoo. ppr: 1 is the provisional full-PPR assumption. Additional scoring rules may be included in league_settings.
3. Use uppercase NFL abbreviations, DEF for team defenses, FLEX for RB/WR/TE, SUPER_FLEX for QB/RB/WR/TE, BN for bench, and IR or IR+ as appropriate. Translate Yahoo labels to these names. Unknown matchups should be null; a known bye can be BYE. Optional player_id can hold a Yahoo player ID.
4. Open your imported **Fantasy GM — Import** workflow in the local n8n editor and open Roster JSON Form. Click its test/listen action, open the displayed Test URL on localhost, and paste the complete JSON into Snapshot JSON. Submit it and verify Save Snapshot succeeds. No publication is required for test forms.
5. Ask Codex: “Run Fantasy GM — Research Pack through n8n MCP and analyze my latest roster.” If you have configured n8n MCP access, select your imported workflow by its local name/ID, authorize manual execution, and read its execution output. Otherwise, run it manually in the editor and review the result.

An explicitly authorized n8n MCP call can also import the JSON using execute_workflow with executionMode manual, triggerNodeName Roster JSON Form, and inputs.formData.snapshot_json containing the JSON as a string.

## Storage and behavior

- `Fantasy GM Submissions` preserves every raw form submission, including invalid attempts.
- `Fantasy GM Snapshots` stores only validated complete snapshots as JSON plus league ID, sample flag, and capture timestamp.
- Research Pack selects the most recently imported snapshot by row ID. This phase assumes a single league; importing another league changes the research context.
- Names receive structural validation, not live identity verification. Team abbreviations and position/slot compatibility are checked; current player affiliations and Yahoo eligibility need later verification.
- Roster needs describe unfilled assigned slots, not optimized lineup availability. Matchups are supplied manually and labeled unverified.
- No statistics or injury reports are generated or fetched. Codex must obtain sourced current research before recommendations.
- No paid AI API, Yahoo credentials, schedules, public services, or automatic roster transactions are configured.
- With no valid snapshots, the Data Table returns no items and no research pack is emitted.
- Raw submissions and snapshots accumulate; this foundation has no retention cleanup policy.

## Validation boundaries

Before repository setup, the original drafts were checked with synthetic form
payloads: valid snapshots were persisted and retrieved, while malformed JSON,
missing fields, invalid teams/positions, and blank names were rejected. This
historical validation does not prove a fresh-instance import or deployment.
Browser rendering and interactive submission were not exercised.

The automated suite checks the embedded Code nodes offline, including rejection
paths, synthetic-data markers, and absence of credential/runtime fields. The
public table placeholders must be remapped and tested on an isolated instance
before using private data.

Sample names are deliberately fictional fixtures, clearly marked is_sample: true; they are not claims about actual NFL players. Replace the sample with your real roster before requesting recommendations.


## Architecture and development

GitHub is the source of truth for code, prompts, and workflow definitions. Local n8n executes the import and research pipeline using persistent Data Tables. Codex can perform user-initiated AI analysis when available; no paid AI API is required. Yahoo API integration is not implemented. All recommendations are advisory; automatic roster transactions are prohibited.

- `workflows/*.json`: authoritative, sanitized workflow templates with placeholder table selectors.
- `workflows/reference/*.ts`: preserved earlier SDK build sources; not synchronized with current drafts. Do not deploy these in place of JSON exports.
- `prompts/`: instructions for Codex analysis.
- `src/`: offline integration and research helpers.
- `tests/`: dependency-free automated validation.
- `data/samples/`: synthetic fixtures only.

The offline validation command above executes the exported JavaScript Code nodes using Node.js. It does not require an n8n instance, credentials, or personal data.

Before committing, inspect every staged file and run the validation suite. Keep real inputs and generated research in ignored `data/private/` or outside this checkout. Ignore rules cannot detect personal data placed in source files: manual review is mandatory.

## Local deployment and workflow provenance

The original definitions were exported read-only on 2026-10-28. The checked-in
files exclude credentials, execution history, pinned runtime data, workflow/version
identifiers, and instance webhook identifiers. Public templates replace Data Table
selectors with explicit placeholders:

| Placeholder | Select your local table |
| --- | --- |
| `REPLACE_WITH_SUBMISSIONS_TABLE_ID` | `Fantasy GM Submissions` |
| `REPLACE_WITH_SNAPSHOTS_TABLE_ID` | `Fantasy GM Snapshots` |

These placeholders are not usable table IDs. They must be remapped before running
an imported template. Keep real table/workflow IDs and deployment notes in ignored
local configuration or private documentation; do not commit the remapped exports.
Node IDs remain internal graph identifiers, not service credentials or endpoints.

For an isolated development instance:

1. Bind n8n to localhost and use a separate persistent volume/database. Do not point it at the working instance's storage.
2. Create Data Tables `Fantasy GM Submissions` with string column `raw_json`, and `Fantasy GM Snapshots` with string columns `snapshot_json`, `league_id`, a **date** column `captured_at`, and boolean column `is_sample`. n8n provides row IDs and timestamps.
3. Import each authoritative JSON file as a **new** workflow using the n8n editor's Import from File action. Keep both inactive. Never import over a working workflow.
4. Remap both Import table selectors and the Research Pack snapshot selector to the new local tables. Use the placeholder mapping above. Replace the two Import selectors and the one Research Pack selector in your local editor; verify none still contains `REPLACE_WITH_` before execution.
5. Manually test Import with the synthetic fixture, then Research Pack. Confirm snapshot validation, persistence, grouping, and advisory constraints before using private data locally. Browser form behavior and a fresh-instance import were not tested during this setup.
6. Review future changes in Git first; deployment to the working instance requires explicit authorization and a backup. Export definitions only, never table contents or execution output.

Automated AI execution is not implemented or claimed tested. The earlier validation record above is preserved project documentation; this setup runs only offline code-node and repository safety checks, without calling live workflows.

## Phase 2 local implementation

The separately invoked, dependency-free Python report path is documented in [Phase 2 usage](docs/phase-2-usage.md). It validates and selects exact-context snapshots, collects public research, evaluates cited lineup assessments, and saves a private local Decision Ledger. Existing n8n workflows remain unchanged; their Phase 1 limitations above still apply. A fresh-instance deployment and real-data behavior must be verified separately; offline tests do not establish either.

P2.3 adds [local waiver scouting and independent defensive streaming](docs/p2.3-waivers.md), reusing the research/reporting foundation with manually verified availability, acquisition limits, cited add/drop costs and claim contingencies. See the [conditional public-data sample](docs/p2.3-public-scouting-example.md). This does not deploy n8n workflows or add Yahoo integration.

P2.4 adds [append-only outcome evaluation and deterministic research jobs](docs/p2.4-outcomes-automation.md). Recorded recommendations can be scored against public NFL results with historical lineup constraints and separate cutoff-only reasoning reviews. Refreshed packs include historical findings. The new n8n draft is inactive with disabled timers; live deployment, execution, private ledger access and schedule activation still require explicit approval.
