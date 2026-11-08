# Knowledge base (P1)

The tutor's educational knowledge lives in SQLite. The source of truth is the reviewed JSON file
`director/knowledge/seed.json`, which is built into `<shared>/state/knowledge.sqlite`. That file is runtime-only and
gitignored; `TD_KNOWLEDGE_DB` overrides its path. The database is rebuilt automatically whenever `seed.json` changes.

```bash
python -m director.knowledge build                       # validate + build (refuses inconsistent data)
python -m director.knowledge stats                       # fact status counts, demo status per concept
python -m director.knowledge query "How does a voltage divider work?"   # exactly what Nemotron is given
python -m director.knowledge graph                       # the knowledge map's nodes + edges (JSON)
```

## What is in it

| table | holds |
|---|---|
| `concepts` | 14 concepts: voltage, current, resistance, Ohm's law, power, series, parallel, LED, forward voltage, current limiting, transistor, BJT, MOSFET, voltage divider. Each has a `demo` status and a `depth` (its prerequisite level) |
| `aliases` | the phrases that name each concept (used for retrieval) |
| `relations` | `requires` (prerequisite), `is_a`, `uses`, `contrasts` |
| `facts` | short paraphrased facts, each with a `source` and a `status` (see below) |
| `sources` | OpenStax College Physics 2e, SparkFun tutorials, Lessons in Electric Circuits, and this project's own calculation modules |
| `misconceptions` | the wrong idea, its correction, a `trigger` regex (does the learner's question show it?) and an optional `claim` regex (does a model explanation state it?) |
| `templates` | demos that exist: the interactive LED tutor plus 3 reviewed lesson specs (Ohm's law, voltage divider, BJT vs MOSFET; stored as JSON), each tied to a calculator |
| `checks`, `concept_checks` | ids of the real calculators and verifiers, and which ones apply to each concept |
| `search` | FTS5 index over concepts, facts and misconceptions |

A concept's demo status is computed from the templates, not entered by hand:

- **interactive**: covered by the live LED tutor.
- **lesson**: covered by a reviewed 3D lesson spec.
- **explanation_only**: no template covers it. Parallel circuits is the only one, because there is no calculator for it yet.

A template is only accepted if its calculator is a real calculator check and its spec passes the lesson validator and
lint. A test also builds every template's fallback code through the sandbox check. Both new templates (Ohm's law and
voltage divider) were built and verified in headless Blender: 16/16 and 18/18 checks passed.

## Retrieval is not verification

The tutor distinguishes between information it looked up and information it actually checked:

- **Fact status is set by the build.**
  - `verified`: the fact names a calculator. The build re-runs that calculator, compares the expected outputs, and
    confirms that every number in the fact's text is one of the calculator's inputs or outputs. Six facts are verified.
  - `sourced`: cited to a source, not machine-checked.
  - `assumption`: a simplification the tutor's own model makes.
- **Numbers.** Explanations are number-checked against **calculated facts only**. Retrieved knowledge goes in a
  separate prompt block and is never part of the number check. If the model copies a number that appears only in the
  knowledge base (for example "about 20 mA"), the explanation is rejected (one retry, then the template).
- **Known misconceptions.** An explanation that states a listed misconception is rejected the same way. Sentences
  containing a negation are skipped, because they are corrections. This is a guard, not a proof: passing it does not
  make the text verified. A test runs every fact, correction and template key point through the guard, so the
  knowledge base's own true statements never trip it.
- **What the UI shows.** Every reply carries a `grounding` record, which the UI labels as:
  - `numbers`: either "calculated" (checked against the calculation), "cited" (taken from cited facts, nothing
    computed: the `explain_concept` answers) or "none".
  - The facts that were retrieved, each with its status badge and source.
  - The note "Only numbers are machine-checked; the wording is grounded in these facts, not proven."

## How it is used

1. **Retrieval** (`director/knowledge/retrieve.py`, deterministic) runs every tutor turn, as a `knowledge` event:
   - It matches alias phrases. A longer phrase hides the shorter ones inside it, so "voltage divider" does not also
     count as "voltage".
   - It checks the misconception triggers and adds an FTS5 nudge.
   - Follow-ups ("simpler, please") reuse the last turn's concepts.
   - The output is up to 3 concepts, their prerequisites, at most 8 facts (always including a verified example when
     the concept has one), up to 3 misconceptions, the best template for the main concept, and the applicable checks.
2. **Nemotron's tool choice** gets a `<knowledge>` block saying which concepts matched and which demo exists. There is
   a new tool, `explain_concept`, for explanation-only concepts. Its concept id is validated against the database
   (one retry).
3. **Nemotron's explainer** gets the facts and misconceptions as trusted background in the system prompt. Explanations
   are checked for numbers and for misconceptions.
4. **The lesson director**:
   - The matched template's reviewed spec replaces the generic LED example in the prompt.
   - The facts and misconceptions are added as trusted background for the key points.
   - Numbers still come only from calculator placeholders.
   - `python -m director.lesson` does the same; `--no-knowledge` turns it off.
5. **Follow-ups on generated lessons** are answered from that lesson's own calculations, with no scene change. The
   lesson must be the one on stage: Blender's read-back shows it and the circuit is gone.
6. **The knowledge map** (right column of the Poyo page; `GET /api/knowledge`) is drawn from the same database:
   - Concepts are arranged in columns by prerequisite depth.
   - Each concept is outlined by its demo status.
   - Concepts glow while they are discussed this turn and stay brighter once discussed.
   - Clicking a concept puts "What is …?" in the input.

Nothing read from the database is executed. Check ids are resolved through the allowlist in `registry.py`, and a
test keeps that list equal to the seed. Misconception regexes come only from the reviewed seed file.

## Adding knowledge

Edit `seed.json` and run `python -m director.knowledge build`. The build refuses anything inconsistent:

- unknown references;
- a fact without a source;
- a prerequisite cycle;
- bad regexes;
- a check that is not in the registry;
- a template without a working calculator, or whose spec fails validation;
- a worked example whose calculator gives different numbers.

To give a concept such as parallel circuits a 3D demo:

1. Add a calculator in `director/lesson/calculators.py`.
2. Add a reviewed spec in `director/lesson/examples.py`.
3. Register both in `registry.py` and `seed.json`.

Its status changes from explanation_only to lesson automatically.

## Limits (honest list)

- Source pointers are chapter- or tutorial-level, and the facts are paraphrased.
  - The SparkFun and OpenStax links were checked as reachable. All About Circuits blocks scripted requests, so that
    link could not be checked automatically.
  - The team should spot-check the wording.
- Retrieval uses phrases and full-text matching, not embeddings: an unusual paraphrase can miss a concept. The
  tutor still works without it, and off-topic questions retrieve nothing.
- The misconception guard only catches the patterns that are listed.
