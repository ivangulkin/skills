# Follow-up repair по замечаниям Claude — 24.08.2026

## Результат

Подтверждённые scanner, termguard, validator и instruction defects исправлены.
Минимальные repro сначала были добавлены как regression tests: исходный suite
стал красным с 21 failures и 1 error на 58 tests. После правок проходят 60
tests.

Standalone layout не менялся: root остаётся единственным implicit skill для
Codex и Claude. Git worktree намеренно не stage/commit.

## Исправленный runtime

### `slopspans.py`

- Confluence storage больше не наследует Markdown indented-code semantics.
- Непарная quote state сбрасывается на границе paragraph, а не глушит остаток
  файла.
- XHTML stack разматывается до matching close tag; лишний close не выпускает
  macro body, незакрытый nested tag не скрывает внешний prose.
- NBSP, narrow NBSP и figure space нормализуются без изменения длины текста.
- Visible CDATA вне protected macro участвует в scan.
- Line numbers считаются по `\n`, а не по дополнительным Unicode separators из
  `splitlines()`.
- `plain` не применяет Markdown blockquote rules; `chat`/`markdown` применяют.
- Placeholder matching стал case-insensitive.
- Ambiguous `, такие как` удалён из punctuation rule.
- Нормативный impersonal gerund с `нужно/необходимо/следует/можно` больше не
  объявляется dangling gerund автоматически.
- BOM остаётся `hygiene-contextual` намеренно: это report-only metadata, не
  provenance verdict.

### `termguard.py`

- Полное удаление editable content теперь hard failure, включая CLI path.
- Success output разделяет проверенный Fact-Lock и непроверенный Terminology
  Lock.
- Term specs проверяют source presence и сохраняют occurrence count.
- Default term использует whole-phrase boundary, case-insensitive matching и
  flexible whitespace; `a|b` задаёт allowed forms, `literal:VALUE` — exact
  literal.
- Comment в lock file начинается с `# `; hashtag `#payments-oncall` остаётся
  term.
- `p95`, `p99`, `v2`, `v3`, dotted versions и ordinary numbers входят в
  numeric invariants.
- Markdown indentation защищается только на Markdown/chat surfaces.
  Confluence защищает macro, task-list, code/pre и quoted XHTML blocks по tags.
- `.html`/`.xhtml` автоматически выбирают `confluence-storage` в CLI.
- Invalid term spec возвращает exit 2 вместо traceback.

### `validate_skill.py`

- Frontmatter парсится только между `---` через `yaml.safe_load`.
- Проверяются allowed keys, hyphen-case name, совпадение с folder, лимиты name
  и description, angle brackets и unfinished description TODO.
- `agents/openai.yaml` также парсится как YAML; implicit policy должна быть
  boolean `true`.
- Regression test подтверждает, что `name:` в body больше не маскирует
  отсутствующий frontmatter key.

## Instruction design и документация

- `SKILL.md` сокращён с 22 796 до 9 899 bytes. Entry point теперь содержит
  invariants, routing, modes и completion criteria; подробные rules остаются в
  references.
- Project glossary явно ограничен уровнем Terminology Lock и не может менять
  protected spans, facts или contracts.
- `registers.md` загружается для business/legal/scientific/marketing/chat и
  mixed surfaces независимо от длины текста.
- Trigger exclusions одинаковы во frontmatter и body.
- Marketing row сохраняет Fact-Lock и Function Contract.
- `metric` удалён из списка scanner tiers; observations описаны отдельно.
- Исправлены пять invariants в `AGENTS.md`, links/paths в README и статус
  declarative behavioral evals.
- `THIRD_PARTY_NOTICES.md` отделяет reproduced notices трёх adapted projects,
  review-only prior art и CC BY-SA attribution Wikipedia-derived descriptions.

## Проверки

| Check | Result |
|---|---|
| `python3 -m unittest discover -s evals -p 'test_*.py'` | 62 tests, PASS |
| OpenAI `quick_validate.py .` | PASS |
| `python3 scripts/validate_skill.py` | PASS |
| `jq empty` для трёх JSON | PASS |
| `git diff --check` | PASS |
| Self-scan runtime docs | 0 mechanical findings |
| Две Kafka RFC, Confluence scanner | 0 mechanical findings |
| RFC-013-2 termguard | Fact invariants + 3 term specs PASS |
| RFC-013-3 termguard | Fact invariants + 4 term specs PASS |
| `termguard perf_notes.md /dev/null` | exit 1, content collapse caught |
| Legal lock against identical source | 5 term specs PASS |
| Codex/Claude symlinks | оба ведут в root `no-ai-slop` |

## Осознанно не добавлено

- Automatic Russian morphology: allowed forms задаются явно через `a|b`, чтобы
  heuristic stemmer не смешивал domain entities.
- Added-term hard failure: добавление canonical term само по себе не является
  drift; gate блокирует disappearance и count reduction source terms.
- BOM stripping: BOM остаётся contextual hygiene finding.
- Batch error aggregation: CLI fail-fast на первом unreadable input сохранён и
  документирован.
- Automatic behavioral model runner: `evals.json` остаётся declarative, потому
  что model execution требует отдельного harness/auth/cost boundary.

## Второй проход того же дня

Повторный аудит на исправленном коде нашёл одну регрессию, внесённую первым
проходом, и три остатка. Оба runtime-исправления закрыты regression tests, счёт
вырос с 60 до 62.

- `termguard.py`: поддержка версий поставила альтернативу `\d+(?:\.\d+){1,3}`
  впереди альтернативы с суффиксом величины, поэтому `4.2 млн` распадалось на
  токен `4.2`, и замена `млн` на `тыс` проходила gate. Dotted version теперь
  требует две и более группы, а `млн|млрд|тыс` входят в число. До правки
  `4.2 млн` → `4.2 тыс` давало `ok: true`.
- `slopspans.py`: правила с якорем `(?:^|[.!?]\s+)` съедают точку предыдущего
  предложения, и номер строки указывал на неё. Строка считается от первого
  значащего символа матча; `Однако,` на второй строке теперь и отчитывается как
  вторая.
- `validate_skill.py`: link check покрывал только `SKILL.md` и `references/`.
  Теперь проверяются все markdown репозитория, кроме `fixtures/` и `inputs/` —
  там битые citation artifacts лежат намеренно, как вход сканера.
- README называет PyYAML: `slopspans.py` и `termguard.py` работают на stdlib, а
  `validate_skill.py` и весь suite без неё падают на импорте.
- Устаревшие ячейки в сравнительной таблице `23-08-2026-three-humanizers.md`
  (Confluence surface, source integrity, число тестов) приведены к фактам, под
  таблицей сказано, что колонка `no-ai-slop` описывает состояние после repair, а
  три остальные зафиксированы на своих commit'ах. Та же ячейка поправлена в
  `three-humanizers-audit.html`. Четыре ссылки на внешний каталог сравнения
  заменены на текстовые пути: в клоне они не разрешались.

## После независимой перепроверки

Третий проход по findings внешнего review. Blocker'ов review не нашёл; счёт
тестов остался 62.

- Discovery path. Личный каталог skills переехал из `~/.agents/skills` в
  `~/.codex/skills` 24.08.2026, уже после repair-прогонов. README и
  `23-08-2026-three-humanizers.md` названы по фактическому пути, старый указан
  как историческая справка. Официальная документация OpenAI называет user scope
  `~/.agents/skills` — это расхождение зафиксировано в README отдельной строкой,
  чтобы установка на другой машине не пошла по неверному пути.
- Два счётчика `60` пропущены вторым проходом: «Результат внедрения» в
  `23-08-2026-three-humanizers.md` и статусная строка в
  `three-humanizers-audit.html`. Обе теперь 62.
- `slopspans.py` срезает sentence anchor и для отображения, не только для номера
  строки: выдаётся `Однако,` вместо `. Однако,`. Матч, состоящий только из этой
  пунктуации, остаётся целым.
- `_skill_files` в `validate_skill.py` переведён на `relative_to(root)` — тот же
  дефект, что раньше был исправлен в link check.
- Восемь must-fire ID из `trigger-evals.json` запинены в `test_evals.py`:
  раньше удаление trigger-кейса suite не заметил бы.

Граница link checker осталась прежней: проверяются markdown links, пути в
backticks — нет. Эвристика по всем backticks дала бы ложные срабатывания на
командах и именах флагов.

## Ограничение

Unit и deterministic gates не доказывают semantic fidelity model rewrite.
После этих repairs нужен новый independent behavioral run на raw fixtures без
передачи ожидаемого ответа. Текущий repair не заменяет такой run.
