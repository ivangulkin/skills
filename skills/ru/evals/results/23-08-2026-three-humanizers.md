# Аудит трёх `humanizer-ru` и решение для `no-ai-slop`

Дата проверки: 23.08.2026.

⚠️ **Исторический документ.** Скилл `no-ai-slop`, о котором он говорит, слит в
`ru` и удалён; alias не оставлен. Выводы о трёх upstream-реализациях остаются в
силе, все упоминания `no-ai-slop` как действующего имени читать как
исторические — [maintainers/prior-art.md](../../maintainers/prior-art.md),
«Архитектурное решение».

Обновление после решения пользователя: выбран standalone skill без plugin
packaging. Оба `humanizer-ru` удалены из discovery Codex и Claude; P0/P1
изменения реализуются в корне `no-ai-slop`.

## Вердикт

Оставить `no-ai-slop` единственным активным implicit-редактором. Ни один из
трёх `humanizer-ru` не стоит устанавливать рядом с ним под теми же triggers и
тем более использовать вместо него для IT-документации, RFC и Confluence.

Это не означает, что upstream-репозитории бесполезны. У Vladimir сильнее всего
устроены registry tool artifacts, provenance и fixtures. У smixs есть полезная
идея сохранять функцию текста, а не только факты. У ilyautov больше
исполняемых tests и несколько хороших отрицательных eval-сценариев. Эти идеи
нужно переносить выборочно под наши locks, а не подключать весь runtime.

Причина решения не в предпочтении стиля. На одинаковых IT fixtures два
upstream scanner'а считают дефектами корректные тире, формулы, code и
Confluence headings; третий пропускает неподтверждённые обобщения. В
репозитории ilyautov все семь сохранённых humanized outputs добавляют факты,
личный опыт или измерения, которых нет в исходниках. При этом его
детерминированный eval объявляет все семь результатов улучшенными. Для рабочей
документации это хуже, чем пропустить стилистический штамп.

## Что именно проверялось

Проверены текущие `main`, документация, `SKILL.md`, runtime references,
scanner'ы, tests/evals, packaging и installation layout:

| Проект | Зафиксированная версия | Commit | Роль в проверке |
|---|---|---|---|
| `no-ai-slop` | рабочее дерево 23.08.2026 | локальная версия | Целевой skill |
| [Vladimir-Human/humanizer-ru](https://github.com/Vladimir-Human/humanizer-ru/tree/3106135980c9de04889047f52a13f31e6fa06726) | `v3.14.0` | `3106135980c9de04889047f52a13f31e6fa06726` | Evidence-oriented upstream |
| [smixs/humanizer-ru](https://github.com/smixs/humanizer-ru/tree/91f70df11f7fb30722e6fcf18803d402e2d86a53) | `1.6.1` по metadata | `91f70df11f7fb30722e6fcf18803d402e2d86a53` | Compact rule-based adaptation |
| [ilyautov/humanizer-ru](https://github.com/ilyautov/humanizer-ru/tree/f2d43d6e8d61903660cc3091160d8b987c2ad613) | `v3.16.1` | `f2d43d6e8d61903660cc3091160d8b987c2ad613` | Detector- and rhythm-oriented variant |

Три независимых аудита шли параллельно, после чего выводы были сверены с
личным чтением файлов и общими fixtures. Для `no-ai-slop` также использованы
[семантический forward test](21-08-2026-forward.md) и две реальные Kafka RFC,
ранее спулленные из Confluence. Визуальные таблицы лежат вне этого репозитория,
в каталоге сравнения `humanizer-it-comparison-2026-08-22`: пофайловое сравнение
RFC — `comparison-table.html`, сводный verdict по четырём решениям —
`three-humanizers-audit.html`.

Scanner'ы решают разные задачи, поэтому их числовые score нельзя сравнивать
между собой как единую метрику качества. В таблицах ниже показано, какое
решение они приняли по одному и тому же input.

## Это три разных skill, а не три копии

У проектов общая тема и заметный общий prior art, но runtime-политики
существенно разошлись:

| Проект | Основной механизм | Что считается успехом | Главный риск для IT-текста |
|---|---|---|---|
| Vladimir | Hard artifacts, soft navigator, evidence registry | Удалены artifacts и накопленные soft signals | Authorship verdict, detector/watermark ветка и шум на technical structure |
| smixs | Большой каталог + `ERROR/WARN` linter | Снижен суммарный severity | Корректная IT-пунктуация и notation объявляются ошибками |
| ilyautov | Catalog, gates, rhythm/burstiness и score | Текст менее предсказуем для detector | Принудительное «оживление» добавляет голос и новые факты |

`smixs` — самостоятельная адаптация общего humanizer prior art; из Vladimir у
него заимствована часть artifact registry, а не весь skill. `ilyautov` развил
отдельную ветку с detector metrics, voice passport и pattern mining. Поэтому
установка всех трёх не увеличивает полезный coverage линейно. Она создаёт три
конкурирующих редакционных политики с одинаковым именем и похожими triggers.

## Решающий сравнительный срез

| Критерий | `no-ai-slop` | Vladimir | smixs | ilyautov |
|---|---|---|---|---|
| IT terminology | Явный Terminology Lock; project glossary; identifiers byte-stable | Technical guide требует сохранять terms, но правило лежит глубоко и scanner не имеет `technical` genre | Примеры требуют сохранять отдельные terms, но общий hard ban на tech jargon противоречит этому | В таблице жанров terms сохраняются, но scanner не имеет technical profile и требует заменять предсказуемые слова |
| Facts | Fact-Lock включает numbers, names, negation, status, causality, uncertainty, mechanism и scope | Есть запрет на новые числа, имена и causal links; нет полноценного status/negation lock | Декларирует Fact-Lock, но собственные rewrite examples добавляют dates, проценты и actors | Fact-Lock в основном про числа, даты и names; сохранённые outputs систематически добавляют claims |
| Protected spans | Code, quotes, logs, configs и technical literals защищены; scanner умеет исключать spans | Soft scanner защищает fenced code, но не весь mixed document | Защищены fenced/inline code и URL; quotes, blockquotes, logs и config не моделируются | Scanner ищет patterns во всём тексте; срабатывает внутри quote и fenced code |
| Register | `technical`, `legal`, `scientific`, `marketing`, `personal`; отдельные правила аудитории | `neutral`, `fiction`, `legal`, `academic`, `marketing`, `chat`; technical отсутствует | Нет полноценной register/surface/audience matrix | Genre hints есть в prose, но исполняемый scanner знает только `default`, `marketing`, `academic`, `legal`, `fiction` |
| Confluence surface | Surface-aware scanner и termguard: `--surface confluence-storage` извлекает visible prose и защищает tags, macros и attributes | Correct H1 и technical triples дают false positives | Тире и notation дают массовые errors | Нет Confluence-aware защиты; default mode шумит на тире |
| Clean-text no-op | Обязателен и отдельно проверен | Navigator может сделать no-op при малом числе categories | Aggregate severity толкает к rewrite даже на корректном тексте | Long-term path предлагает профилактически менять чистые предложения |
| Authorship | Не выносит verdict и не оптимизирует под detector | Разрешает verdict по одному Class A artifact | Даёт невалидированные пороги «AI-like» | Detector evasion — часть design goal |
| Watermark/C2PA | Не удаляет и не обещает обход | References и scripts описывают removal, backtranslation и other-family rewrite | Явной отдельной сильной подсистемы нет | Опирается на adversarial detector literature и ASR |
| Source integrity | Отдельный модуль `references/source-integrity.md` плюс checklist в `SKILL.md` | Сильный evidence registry и аккуратные offline anomalies | Проверка уже, DOI/ISBN и claim alignment почти не покрыты | Sources catalog подробный, но runtime смещён к detector signals |
| Исполняемые tests | 62 unit tests + forward semantic eval с зафиксированным fail/repair | 43 gates, 173 unittest, 39 marker cases, 107 soft selftests | Только reproducible linter self-test; scenarios в JSON без runner/CI | Большой runnable suite, но fidelity humanized outputs не является gate |

Колонка `no-ai-slop` описывает состояние после repair-проходов 23.08.2026 и
24.08.2026. Три остальные колонки зафиксированы на commit'ах из таблицы выше и
позже не перепроверялись.

## Общие IT fixtures

### Две реальные Kafka RFC

| Input | `no-ai-slop` | Vladimir | smixs | ilyautov |
|---|---|---|---|---|
| RFC `2123659605` | Mechanical findings: 0. Semantic Fact-Lock: удалено 1 неподтверждённое сравнение частот | Hard artifacts: 0. Soft: 8 корректных technical triples + 1 Confluence H1 false positive; auto-edit остановлен | 61 `ERROR`, 5 `WARN`, severity 188; основа — 34 тире и 27 notation signs | Default: exit 1, 33 dash bans, 3 markers, но score одновременно `100/clean`; `academic` скрывает findings полностью |
| RFC `2129173138` | Mechanical findings: 0. Semantic Fact-Lock: 4 локальные правки unsupported duration/frequency/generalization | Hard artifacts: 0. Soft: 7 technical triples + 1 H1 false positive; четыре semantic gaps пропущены | 118 `ERROR`, 8 `WARN`, severity 362; основа — 59 тире и 59 notation signs | Default: exit 1, 58 dash bans, 4 markers, но score `98/clean`; `academic` снова даёт 0 findings |

На обеих RFC все ветки, которые реально редактировались, сохранили `Kafka`,
`Strimzi 0.49.1`, broker/controller, ISR, OOM, `client.rack`, config keys,
формулы, macro IDs, Jira keys, attachments и URLs. Следовательно, риск
буквальной замены `pipeline` на «трубу» не подтвердился. Реальный риск —
ложные структурные findings и изменение semantic claims.

Подробные diff лежат в том же внешнем каталоге сравнения:

- RFC-013-2 — `2123659605.diff-analysis.md`;
- RFC-013-3 — `2129173138.diff-analysis.md`.

### Защитные и калибровочные fixtures

| Fixture | `no-ai-slop` | Vladimir | smixs | ilyautov |
|---|---|---|---|---|
| Уже хороший IT-текст | No-op; forward eval проверяет byte identity | Soft findings: 0 | 2 errors на корректных тире | Exit 1 из-за двух тире, хотя score говорит `97/clean` |
| Negative fixture с quote/code/range | Clean; protected spans сохранены | Hard/soft findings: 0 | 2 errors: корректное тире и диапазон `3–4` | 4 bans: фраза внутри quote, `Данный` в fenced code и два тире |
| IT terms + намеренный slop | 6 точечных candidates | Soft findings: 0 — coverage gap | 0 errors, 1 warning, итог clean | 6 нужных findings, но общий score `37/rewrite` |

`no-ai-slop` не выигрывает потому, что выдаёт меньшее число. Он разделяет
mechanical candidates и semantic review, не превращает count в verdict и
считает false positive дороже пропуска. Именно это нужно для production docs.

## Аудит каждого upstream

### Vladimir-Human: лучший источник инженерных идей, но не второй редактор

Сильная часть проекта — не language rewrite, а provenance pipeline:

- versioned artifact registry;
- immutable source URL и access date;
- positive, negative и boundary fixtures;
- stable core отдельно от быстро меняющихся tool artifacts;
- blind paired eval и публикация отрицательных результатов.

Native suite проходит полностью. Однако это в основном self-consistency regex
и packaging. В human corpus нет реальных README/RFC/runbook fixtures, а
technical genre и Confluence surface отсутствуют. На наших RFC navigator
ошибочно принял реальные enumerations за `rule of three`, а обычные H1 — за
сломанный Markdown.

Более серьёзный конфликт находится внутри policy. README отстраивает проект от
detector evasion, но runtime references описывают statistical watermark
removal, C2PA/EXIF/XMP stripping, backtranslation, rewrite моделью другого
семейства и даже code renaming. Основной `SKILL.md` также допускает authorship
verdict по одному Class A artifact. Tool residue доказывает наличие конкретного
fragment artifact, но не авторство всего документа.

Решение: использовать checkout как vendor/reference source. Не экспонировать
полный skill в discovery. Перенести evidence registry и fixtures, отвергнуть
authorship и detector/watermark ветки.

### smixs: компактный, но hard bans несовместимы с IT/RFC

У smixs понятный linter и хороший принцип «убрать воду, не удалить функцию
текста». В marketing это означает сохранить CTA, offer, deadline, link и
contact. Для IT этот принцип можно адаптировать как Function/Surface Contract:
не терять operator action, preconditions, rollback, acceptance criteria,
links и ownership.

Текущий runtime переносить нельзя:

- em/en dash всегда `ERROR`;
- `+`, `=`, `→`, `vs` и похожая notation всегда `ERROR`;
- negative parallelism и tech jargon объявлены hard bans;
- aggregate `errors * 3 + warnings` определяет rewrite;
- scanner не защищает quotes, blockquotes, logs, configs и Confluence macros.

Собственные examples противоречат Fact-Lock: в rewrites появляются `2025`,
«40 компаний», `30%`, `70%`, новый actor и дополнительная calendar detail.
Каталог заявляет 34 patterns, фактически описывает 37. Declarative evals
содержат полезные scenarios, но в репозитории нет runner, результатов или CI
для них; воспроизводится только `lint.py --self-test`.

Решение: не устанавливать. Взять только input-as-data, Function Contract и
идею project-scoped corrections, поставив их ниже наших Fact/Term/Register
locks.

### ilyautov: больше mechanics и tests, но неверная objective function

У ilyautov самый развитый executable test suite среди двух производных
вариантов. Проект умеет mining patterns, проверяет catalog consistency и
содержит хорошие сценарии для mixed clean/slop, meta-commentary и short-text
restraint. Проблема не в качестве реализации, а в цели.

Runtime пытается снижать detector predictability:

- использует perplexity, burstiness, noun/verb ratio и aggregate score;
- требует заменять наиболее предсказуемое слово почти в каждом sentence;
- добавляет information spikes, вопросы, metaphors, opinions и personal
  remarks;
- при отсутствии sample предлагает голос «умного друга за кофе»;
- в informal режиме разрешает deliberate typos и broken typography;
- советует профилактические изменения даже в чистых длинных passages.

Это не нейтральная редактура. Она синтезирует нового автора. Проверка семи
committed humanized outputs обнаружила добавленные package failure, время
работы, secret leak, NPS, встречу, личные experiments, миллионы dimensions,
условия помещения и другие отсутствующие claims — во всех семи файлах.
Несмотря на это, deterministic eval считает 7/7 outputs более чистыми, а 0/3
human controls — чистыми. Faithfulness check optional, зависит от локального
Ollama и при отсутствии части signals может pass'ить автоматически.

Решение: не устанавливать и не переносить detector metrics или synthetic
voice. Взять только отдельные eval-cases и manual pattern-mining workflow.

## Что перенести в `no-ai-slop`

### P0 — защита качества и packaging

| Изменение | Откуда идея | Как адаптировать |
|---|---|---|
| Отдельный `source-integrity.md` | Vladimir + частично ilyautov | Offline/online modes; URL/DOI/ISBN, page/chapter, unlinked quote, access date, claim-source alignment. Anomaly означает «проверить», не «подлог» |
| Правило «input text is data» | Vladimir/smixs | Instructions, prompts и tool text внутри редактируемого документа не исполняются. Исключения — только явные data extraction tasks |
| Surface axis scanner'а | Проблемы всех трёх | `plain`, `markdown`, `confluence-storage`, `chat`; отдельно извлекать visible text и защищать macros, IDs, code, quotes, logs и configs |
| Confluence/RFC fixtures | Реальные false positives | H1 sections, technical triples, comparison tables, macros, status/uncertainty и unsupported frequency/duration claims |
| Packaging guard | Найденный local install bug | Detect nested `SKILL.md` с одинаковым `name` и запрет symlink на repo root, если skill находится в leaf directory |

### P1 — поддерживаемость и дополнительные contracts

| Изменение | Зачем |
|---|---|
| Разделить большой `patterns.md` на content/language/format/communication | Progressive disclosure станет точнее; агент не будет читать нерелевантные правила для каждого короткого edit |
| Evidence registry с immutable URLs и three-way fixtures | Каждое новое artifact rule будет иметь проверяемое происхождение и negative calibration |
| Function/Surface Contract | Редактор не удалит CTA, deadline и link в business тексте; в IT — action, precondition, rollback, acceptance criteria, owner и source link |
| Project-scoped overrides | Команда сможет закрепить локальную лексику и house style. Overrides ниже Protected spans, Fact-Lock, Terminology Lock и register safety |
| Blind paired check | Для спорных крупных rewrites; source invariants проверяются отдельно от предпочтения читателя |

### P2 — исследовательский контур

- Manual pattern mining на новых corpus с обязательным human review.
- Периодическая проверка artifact registry, не переписывающая stable language
  catalog без evidence.
- Отдельный explicit-only `humanizer-artifact-audit` нужен только если
  пользователю понадобится узкий provenance audit. Сейчас его функции почти
  полностью покрыты `slopspans.py`, поэтому новый skill преждевременен.

## Что не переносить

| Запрет на перенос | Почему |
|---|---|
| Hard bans на слова, тире, notation, bullets, headings и sentence length | Context определяет корректность; в IT это часть interface и exact syntax |
| Aggregate style score и thresholds вроде `6+ → full rewrite` | Count смешивает разные severities и создаёт edit pressure без reader-facing defect |
| Authorship probability или verdict | Text features и tool residue не доказывают автора документа |
| Perplexity, burstiness, noun/verb quotas и ASR | Objective смещается с ясности и fidelity на detector behavior |
| Prophylactic edits чистого текста | Ломают idempotence и авторский голос |
| Deliberate typos, slang, profanity, particles, idioms, emotions, questions и metaphors по квоте | Эти признаки можно сохранять у автора, но нельзя синтезировать как рецепт «человечности» |
| Backtranslation, other-family rewrite, watermark/C2PA stripping и code renaming | Это detector/evidence removal, а не редактура документа |
| Обязательный full rewrite | Повышает blast radius и риск semantic drift; default должен быть локальным edit |

## Как оформить standalone skill

Рекомендуемая схема — один focused implicit skill, которым является корень
repository:

```text
no-ai-slop/
  SKILL.md
  agents/openai.yaml
  references/
    registers.md
    terminology-lock.md
    contracts.md
    source-integrity.md
    structure.md
    patterns.md
    patterns/
      content.md
      language.md
      format.md
      communication.md
    evidence.md
    storyscope.md
    prior-art.md
  scripts/
    slopspans.py
    termguard.py
    validate_skill.py
    rules.json
```

Количество references здесь вырастет примерно с 7 до 13. Это не попытка
повторить объём Vladimir. Split оправдан только routing: короткий IT edit не
должен загружать research methodology и весь language catalog. В
`SKILL.md` остаются invariants и decision tree; детали подгружаются по типу
задачи. Такой layout соответствует progressive disclosure из официальной
документации [Build skills](https://learn.chatgpt.com/docs/build-skills).

Для постоянного local use plugin не нужен. Codex и Claude ссылаются на один и
тот же root skill, а не хранят копии:

```text
~/.codex/skills/no-ai-slop
  -> ~/git/home/ai/no-ai-slop

~/.claude/skills/no-ai-slop
  -> ~/git/home/ai/no-ai-slop
```

На 23.08.2026 Codex-симлинк лежал в `~/.agents/skills`. Личный каталог skills
переехал в `~/.codex/skills` 24.08.2026; путь выше отражает состояние после
переезда.

До исправления установка Vladimir была сделана неверно:

```text
~/.codex/skills/humanizer-ru
  -> ~/git/home/ai/humanizer-ru
```

В root checkout лежит один `SKILL.md`, а внутри — ещё один
`dsh/skills/humanizer-ru/SKILL.md`. Поэтому Codex обнаруживает два одинаковых
`humanizer-ru`. Оба symlink удалены из active discovery 23.08.2026.

Official skill guidance также говорит держать skill focused на одной задаче,
делать description scope-aware и не рассчитывать на merge одноимённых skills.
Это ещё одна причина не устанавливать три implicit humanizer'а одновременно.

## План внедрения

1. Убрать оба full-repo symlink Vladimir из Codex/Claude discovery — выполнено.
2. Добавить P0: source integrity, input-as-data, surface axis, Confluence
   fixtures и duplicate-skill packaging gate.
3. Разделить pattern references и добавить Function/Surface Contract.
4. Запустить прежние 26 unit tests, forward fixtures и обе RFC через новый
   `confluence-storage` path.
5. Провести blind review только для edits, которые прошли deterministic
   Fact/Term/Protected checks.

После этих изменений `no-ai-slop` остаётся standalone implicit skill.
Установка отдельного upstream humanizer или выпуск plugin не нужны.

## Результат внедрения

На 23.08.2026 выполнено:

- skill перенесён в root `no-ai-slop`; plugin manifest и marketplace удалены;
- `agents/openai.yaml` явно оставляет implicit invocation включённым;
- Codex `~/.codex/skills/no-ai-slop` и Claude
  `~/.claude/skills/no-ai-slop` ссылаются на один root folder;
- оба `humanizer-ru` удалены из discovery, три vendor checkout перемещены в
  macOS Trash;
- добавлены `source-integrity.md`, `contracts.md`, четыре routed pattern
  references и dated evidence registry;
- scanner и termguard получили `--surface confluence-storage`;
- добавлены standalone packaging gate, Confluence fixtures, source audit,
  runbook contract и embedded-instruction evals;
- после follow-up repair 24.08.2026 проходят 62 deterministic tests; детали —
  в `24-08-2026-claude-findings-repair.md`;
- обе Kafka RFC дают 0 mechanical findings в новом Confluence mode;
- `termguard --surface confluence-storage` подтверждает сохранность numeric
  tokens, URLs, protected spans и markup для обеих RFC. Terminology Lock
  считается проверенным только при переданных term specs.

## Ограничения вывода

- Две RFC относятся к Kafka/Confluence и не представляют весь русский IT
  corpus.
- Native test counts измеряют self-consistency каждого проекта, а не одну
  общую quality metric.
- Semantic audit humanized outputs выполнен по committed examples, а не по
  всем возможным prompts.
- Вывод касается рабочей IT-прозы с высокой ценой factual drift. Для marketing
  copy или личного блога допустим другой register, но detector optimization и
  invented facts всё равно не становятся безопасными.
