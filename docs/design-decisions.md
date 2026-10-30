# Design decisions

Short records of the choices that shaped the project, with the alternative that was rejected and why.

## 1. Deterministic ingestion, LLM only for questions
Source data here is structured JSON. Parsing it with Pydantic models is faster, free, reproducible and testable; an LLM in that path would add cost and occasional silent errors. The LLM is used where language is the input: understanding questions and phrasing answers.

## 2. Adapter + canonical model
Each source implements three methods and returns source-agnostic records (`ingestion/canonical.py`). The pipeline, schema and everything downstream never see a source's format. The `source_ids` table maps a source's IDs to internal IDs, which is what makes a second source possible without rewriting anything.

## 3. Raw cache before normalisation
Every response is stored untouched (`data/raw`). Re-running normalisation after a schema change costs no network traffic, and when a source changes format the evidence is on disk. It also doubles as the HTTP cache.

## 4. One transaction per match, upserts everywhere
A match is fetched and validated completely before anything is written, then loaded in a single transaction; a failure leaves no partial state and the run continues with the next match. Upserts on natural keys make re-runs and `--force` reloads idempotent (verified by tests and by comparing row counts after a forced reload).

## 5. Views for the model, not just tables
`team_matches`, `shots` and `passes` encode knowledge that models routinely get wrong: every match has two perspectives, penalty shootouts (period 5) are not goals, "progressive pass" needs a definition. Moving that logic into views turns many multi-join queries into single-table ones and makes errors less likely and easier to spot.

## 6. Parse SQL, do not pattern-match it
The validator uses `sqlglot` to build a syntax tree and checks node types, tables and functions. Regex filters on SQL are famously bypassable (comments, casing, stacked statements). The validator is only one of three layers; the database session itself is read-only and time-limited, so a validator bug alone cannot cause damage.

## 7. Entity linking before generation
Asking the LLM to spell "Alejandro Grimaldo García" correctly from "Alex Grimaldo" is a lottery. A deterministic linker (accent/umlaut folding, nickname table, fuzzy match for typos) resolves mentions to database IDs and passes them as hints. Shared surnames are resolved by activity: if one player has at least three times the events of the runner-up (Luis Suárez among eight Suárezes) he wins; otherwise the candidates are listed, most active first, and flagged as ambiguous instead of guessed (six players called Müller). Fuzzy matching is restricted to capitalised, longer mentions after all exact matches, because an early version fuzzy-matched "Leverkusen do" before the exact word "Leverkusen" got its turn.

## 8. A grounding check instead of trusting the answer model
The answer model is told to use only the result rows, but instructions are not guarantees. The check extracts every number from the answer and requires it to appear in the rows, the question, or the SQL (with handling for German decimal commas, thousands separators and percentages). On failure the user sees a plain rendering of the rows. The cost is that some correct but unusual phrasings are rejected, which is the right side to err on for a data product.

## 9. Execution accuracy, with a self-tested harness
Comparing SQL strings punishes correct but differently written queries; comparing results does not. The harness matches rows order-insensitively, allows extra columns, and requires row alignment when columns are mapped. An oracle mode answers with the gold SQL and must score 100%, which catches broken benchmarks and broken matching logic before any model is blamed.

## 10. SQLite by default, PostgreSQL supported and tested
SQLite makes the project runnable in one command. PostgreSQL is covered by an integration suite and a CI service container. Supporting both found a real bug: Postgres has no `round(double precision, int)`, so model-written `ROUND(AVG(xg), 2)` failed. Numeric columns are therefore `NUMERIC` on Postgres and `FLOAT` on SQLite. Flags are stored as `INTEGER 0/1` for the same portability reason.

## 11. No orchestration framework
The flow is a short, linear function (`nl2sql/service.py`) with one retry loop. A framework would hide exactly the parts worth being able to explain: prompts, retries, and where validation happens.

## 12. Ollama and a small default model
Runs locally, needs no API key and no data leaves the machine. `num_ctx` is set explicitly because Ollama's default context window is small enough to silently cut off the schema. The model sits behind a one-method `LLMClient` protocol, so evals and tests substitute their own, and a hosted model would be a few lines.

## 13. Say when the data is incomplete
The free Barcelona seasons are missing 4-5 match weeks each. Silently answering "Barcelona got 72 points in 2019/20" would be a confident, wrong statement. At startup the app detects gaps in each season's match weeks, states them in the prompt ("INCOMPLETE: match weeks 1, 2, 3, 4, 7 are not loaded"), and instructs the answer model never to present a partial total as the full-season figure. It also detects datasets where one team is in every match (so "league top scorer" is unanswerable). Both facts come from the data itself, not from hard-coded notes, so they stay correct when new seasons are loaded.

## 14. Season as a first-class column
Once several seasons are loaded, "who scored the most goals?" is ambiguous. `shots`, `passes` and `team_matches` carry `season_name` and `competition_name`, and the benchmark questions that would be ambiguous name the season explicitly. A bare "2019" is treated as ambiguous between 2018/19 and 2019/20.
