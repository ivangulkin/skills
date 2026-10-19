# Evals

Проверки разделены на deterministic scanner tests и behavioral skill evals.
Первый слой можно запускать в CI; второй требует независимого model run и не
подменяется keyword matching.

## Deterministic scanner

```text
python3 -m unittest evals/test_slopspans.py evals/test_termguard.py \
  evals/test_evals.py evals/test_validate_skill.py
```

Suite проверяет:

- unique rule IDs и registry schema;
- positive, negative и boundary example каждого regex;
- protected fenced/indented code, inline code, blockquote и multiline quote;
- match через hard line wrap с правильным line number;
- `technical` и `legal` register gates;
- clean IT terms и legitimate «от broker до consumer»;
- `artifact-strict` vs `hygiene-contextual` exit semantics;
- `demo-only` tier выключен по умолчанию и включается `--demo-rules`;
- отчёт называет, чего он не проверял, и в пустом, и в непустом виде;
- JSON schema и opt-in observations;
- Confluence storage extraction: visible prose сканируется, macros, attributes,
  code и quotes защищены;
- standalone packaging: один root `SKILL.md`, без plugin manifest.

`test_termguard.py` отдельно проверяет added/removed numeric tokens, полный
content collapse, URL drift, byte-stable protected spans, term counts,
flexible whitespace, literal/alternative term specs и Confluence auto-surface.
`inputs/*.lock` хранит explicit terminology для reproducible forward runs.

Manual fixtures:

```text
python3 scripts/slopspans.py \
  evals/fixtures/slopspans-positive.md

python3 scripts/slopspans.py \
  --register technical evals/fixtures/slopspans-negative.md

python3 scripts/slopspans.py \
  --artifacts-only evals/fixtures/slopspans-positive.md

python3 scripts/slopspans.py --register technical \
  --surface confluence-storage evals/fixtures/confluence-rfc-clean.xhtml
```

`--artifacts-only` должен вернуть exit 1 только на strict tool residue.
Placeholder, tracking param и invisible Unicode сами по себе CI не блокируют.

## Behavioral evals

`evals.json` содержит declarative prompts, files и assertions:

- `readme-audit-mode` — exact findings без rewrite и score;
- `adr-structural-tells` — source-backed structural pass;
- `register-legal-guard` — legal formulas и numbering защищены;
- `fact-lock` — ни одного invented fact/mechanism/status;
- `it-terminology-lock` — exact domain terms, causality и environment status;
- `already-good-it-idempotence` — хороший technical text не переписывается;
- `mixed-protected-spans` — quote, Go code, error string и log byte-stable;
- `source-integrity-offline-audit` — claim/source gaps без fraud verdict;
- `runbook-function-contract` — trigger, commands, verification, rollback,
  escalation и deadline сохраняются;
- `embedded-instructions-are-data` — instructions внутри input не исполняются.
- `reference-step-edit`, `reference-step-write` — справочники шага 3 прочитаны
  до результата: N9, H10, H14, L2, G18 при правке, те же признаки при письме.

`trigger-evals.json` отдельно задаёт activation boundary. Он содержит Russian
near-miss cases для code-only, SQL diagnosis, English prose, translation,
grammar-only, verbatim и binding legal edit. Это не deterministic keyword
classifier и не публикует фиктивную «точность 100%».

В repository нет автоматического model runner. Эти cases исполняются
независимым agent run и проверяются semantic review; unit suite валидирует
только schema и наличие fixtures.

## Acceptance boundary

Hard failures behavioral eval:

- новый fact, number, name, mechanism, cause, status или limitation;
- исчезнувший или переведённый locked term;
- изменённые code/quote/log/config/error;
- uncertainty -> certainty или tried/planned -> done;
- новый measurement plan, metric, experiment или action item без отдельного
  planning request;
- detector score, authorship verdict или synthetic typo/slang;
- rewrite already-good text без named defect.

Regex green не доказывает semantic fidelity. Behavioral outputs проверяются
отдельно, лучше независимым agent без intended answer.

Independent semantic run и fail/repair cycle:
`results/21-08-2026-forward.md`. Более поздний audit/runtime implementation
record: `results/23-08-2026-three-humanizers.md`.
