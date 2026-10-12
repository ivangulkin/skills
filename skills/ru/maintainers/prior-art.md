# Prior art для No AI Slop RU

Читай только при развитии skill, добавлении rule или сравнении с другим
humanizer. Для обычной правки файл не нужен.

Глубокий аудит обновлён 2026-08-23 по локальным clones, а не по README; разбор
smixs и ilyautov перепроверен 2026-10-10 по более новым коммитам:

- `Vladimir-Human/humanizer-ru`, commit
  `3106135980c9de04889047f52a13f31e6fa06726` (`v3.14.0`);
- `smixs/humanizer-ru`, commit `ff990df` (`v1.7`); раньше —
  `91f70df11f7fb30722e6fcf18803d402e2d86a53` (`1.6.1` по metadata);
- `ilyautov/humanizer-ru`, commit `f2fe1f3` (`v3.31.3`); раньше —
  `f2d43d6e8d61903660cc3091160d8b987c2ad613`.

Все три проекта MIT и быстро меняются. Перед новым заимствованием проверяй current main,
tests и license.

## Критерии отбора

Полезная идея должна улучшать reader-facing result и не ломать source fidelity.

1. Strict tool residue отделено от contextual style?
2. Finding указывает exact span, а не выдаёт provenance verdict?
3. Register, surface и audience входят в решение?
4. Правка не изобретает fact, mechanism, status или voice?
5. Есть positive, negative и boundary fixtures?
6. Measurement отделено от editorial judgment?
7. Цель — reader, а не detector?

Количество паттернов не равно coverage.

## Архитектурное решение

**Действующее.** Один standalone skill с machine ID `ru`. Он покрывает и
грамматику, и редакторский слой; отдельного humanizer'а рядом нет. Repository
root является skill folder; Codex и Claude ссылаются на него напрямую.

Причина одна: два installed humanizer'а имели бы overlapping descriptions и
конфликтовали бы при каждой русской правке. Здесь goal один — точная русская
prose для IT reader, поэтому Russian catalog, Terminology Lock и register matrix
суть resources одного skill.

[Официальная документация OpenAI](https://learn.chatgpt.com/docs/build-skills)
говорит, что model сначала видит name/description и загружает instructions при
match или direct invocation. Для local authoring она поддерживает standalone
skill в `$HOME/.agents/skills` и symlinked folder; одноимённые skills не
merge'ятся.

**Историческое, 2026-08.** До слияния machine ID был `no-ai-slop`, и он
сохранялся ради existing install, symlink и `$no-ai-slop`. Это решение отменено:
скилл переименован в `ru`, `no-ai-slop` удалён целиком, alias не оставлен.
Ссылки на старое имя ниже по файлу и в `evals/results/` читать как исторические.

## Vladimir-Human/humanizer-ru

Source: [repository](https://github.com/Vladimir-Human/humanizer-ru).
Author: Vladimir. License: MIT.

### Что внутри

Reviewed line v3.14 содержит artifact registry, progressive-disclosure
`SKILL.md`, provenance records, positive/negative/boundary fixtures,
soft-signal navigator, source-fabrication checklist, human/boundary corpus,
blind eval и DSH vendored bundle. Проверка дала 43/43 project gates, 173/173
unittest, 39/39 marker cases и 107/107 soft-signal selftests.

На двух Kafka RFC hard scanner clean, но soft navigator принял 8 и 7 реальных
technical enumerations за `rule of three`, а Confluence H1 — за Markdown
hierarchy defect. Technical genre и Confluence surface отсутствуют.

### Что берём

- progressive disclosure и явную update policy stable core / fast artifacts;
- register/false-positive discipline;
- правило «вне найденных дефектов не менять»;
- Fact-Lock и technical mode: terms, commands, params, versions дословно;
- source-fabrication offline checklist;
- artifact evidence registry и three-level fixtures;
- Russian semantic calques, register shift, Title Case, heading hierarchy,
  useless tables и truncated ending;
- prompt/tool residue current platforms;
- DSH-style sync gate только если у нас появится реальная vendored copy.

### Что адаптируем

- Class A становится `artifact-strict`: tool residue, не доказательство
  authorship.
- Zero-width, UTM/referrer, placeholders и typography становятся
  `hygiene-contextual`.
- Artifact regex входит в scanner с protected spans; upstream scanner в
  fixtures защищает не все code/quote contexts.
- Появляется отдельный `technical` register и Terminology Lock.
- Source anomaly даёт «требует проверки», а не «подлог» без verification.

### Что отвергаем

Runtime конфликтует со своим README «detectors не обходим»: `rewrite-guide`,
`removal-matrix` и `scripts/filemarks` предлагают снимать statistical
watermarks deep rewrite, другой model family, backtranslation и даже renaming
code variables. Это detector evasion, не editing.

Не переносим:

- AI verdict по одному artifact;
- 0–10 rubric и threshold «6+ -> rewrite whole text»;
- «текст вероятно human»;
- absence of idioms как defect;
- watermark/C2PA/EXIF/XMP removal;
- other-family rewrite и backtranslation;
- code renaming;
- blanket restrictions на network/files для нашего workflow.

Гейты upstream хорошо проверяют regex/parity/provenance/sync, но не проверяют
Terminology Lock, causality, status, mechanism, detector-evasion policy или
reader benefit.

## smixs/humanizer-ru

Source: [repository](https://github.com/smixs/humanizer-ru). License: MIT.

Проект — самостоятельная адаптация общего humanizer prior art; часть artifact
registry заимствована из Vladimir. Полезны три идеи:

- input text считается data, embedded instructions не исполняются;
- редактура сохраняет функцию текста: CTA, deadline, link и contact;
- project-scoped corrections могут уточнять style ниже hard invariants.

Function rule адаптирован в [contracts.md](../references/contracts.md): для IT сохраняются
operator action, preconditions, verification, rollback, acceptance condition,
owner и links.

Runtime не переносится. Linter объявляет `ERROR` длинное/среднее тире,
technical notation (`+`, `=`, `→`, `vs`) и negative parallelism, после чего
считает `errors * 3 + warnings`. На двух RFC получено 61/118 errors и severity
188/362; на clean negative fixture помечены корректное тире и range `3–4`.
Tech jargon числится hard ban только в `SKILL.md`; в `lint.py` его нет, как и
риторических вопросов (проверено на `ff990df`).

Fact-Lock декларируется, но examples добавляют `2025`, «40 компаний», `30%`,
`70%`, нового actor и calendar detail. Declarative evals не имеют runner,
результатов или CI; воспроизводится только `lint.py --self-test`.

Вывод: borrow Function Contract и input-as-data, reject hard bans, score и
runtime rewrite policy.

### v1.7 (`ff990df`, проверено 2026-10-10)

Изменились пять файлов (`SKILL.md`, `patterns.md`, `lint.py`, `CHANGELOG.md`,
`README.md`); evals и `knowledge/` не тронуты.

- Счётчик каталога исправлен: заявлено 38 паттернов, описано 38 (раньше 34 против
  37). Новый паттерн 38 «машинный скелет текста» с outline-тестом и две позиционные
  проверки линтера (`абзацы под копирку`: ≥5 абзацев с разбросом длины <25 %;
  `списки по три`: ≥2 списков, все по три пункта). Reject: это числа и квоты, а
  README с двумя списками по три пункта срабатывает; outline-тест требует
  выдумывать возражения и противоречит W11, W12.
- Detect-режим получил вердикт «сверхкоррекция» («обработан инструментом») — вердикт
  о происхождении, политикой отвергнут. Как ловушка для наших evals идея годится.
- Не исправлено из прежних находок: Fact-Lock в примерах (ещё не менее 12 «После»
  выдумывают факты), `ERROR` на тире и нотацию (`Kafka — распределённый лог`,
  `клиент → брокер → консьюмер`, `Rust vs Go`), нет защиты blockquote (текст после
  `>` остаётся в проверке).
- Каталог по-прежнему содержит `имплементировать → внедрить`, `консенсус →
  согласие`, `фидбек → отзыв` без условия. Сдвиги смысла; для нас — строка
  `имплементировать` в таблице автозамен P3. `консенсус` и `фидбек` не взяты: словари
  определяют `консенсус` как «общее согласие», а для `фидбека` сверять не с чем.

`lint.py` не запускался: регулярные выражения и пороги проверены на копиях.

## ilyautov/humanizer-ru

Source: [repository](https://github.com/ilyautov/humanizer-ru).
Author: Ilya Utov. License: MIT.

### Что внутри

v3.16.1 (разбор 2026-08-23): 58 patterns / 13 categories, 20 hard bans, three
modes, voice passport, traffic-light triage, five-pass rewrite, scanner modules
for markers, rhythm, morphology, structure and score; trigger eval, AINL
calibration, raw/human/humanized corpus, optional detector/LLM judge/faithfulness.

v3.31.3 (`f2fe1f3`, 2026-10-10): 67 patterns / 16 categories, 21 hard bans, 23
scanner categories. `является` ловится с частотным порогом, `таким образом` только
как вводное.

После установки pinned `razdel==0.5.0` и `pymorphy3==2.0.6` на v3.16.1 прошли:

- `test_gates`: 26;
- `test_scan`: 12;
- `test_markers`: 105;
- `test_score`: 23;
- `test_mining`: 8;
- `lint_skill`, `check_examples`, `self_scan`.

### Что берём

- case/gender/government и verb aspect как grammar checks;
- initial participial/gerund pile-up без arbitrary quota;
- semantic translationese после Terminology Lock;
- «определённый/соответствующий» только когда скрыт referent;
- generic modality повтор как soft candidate с preserved uncertainty;
- Title Case, truncated ending, current artifact formats;
- explicit focused-edit mode, short-text restraint, voice passport;
- trigger eval как отдельную axis;
- AINL calibration как negative evidence о phrase lists;
- raw/after faithfulness как обязательную идею, но с deterministic invariants.

### Что отвергаем

- замену most predictable word ради perplexity;
- aggregate cleanliness score и AI probability;
- noun/verb target, CV target и fixed rhythm quotas;
- blanket em-dash ban;
- обязательные questions, inversion, metaphors, idioms, particles, profanity;
- 20–30% hard joins и information cardiogram;
- deliberate typos и broken typography;
- forced opinion/disagreement/emotion;
- model fingerprints и detector adapters как runtime goal.

### Проверенный Fact-Lock failure

Upstream `raw/docs_gpt_07.txt` говорит только: установить dependencies через
package manager, использовать актуальную runtime, настроить env vars и сделать
финальную проверку. `humanized/docs_gpt_07.txt` добавляет без source:

- старые packages не соберутся;
- engineer потратит вечер;
- secrets утекут в repository;
- нужен «финальный test»;
- config deploy'ится на другую machine без surprises.

Bundled scanner награждает after `100/100`, raw получает `9/100`. Значит style
metric предпочитает fluent fabrication. Те же нарушения есть во всех семи
committed after-files: invented NPS, timelines, personal experience и product
behavior.

CI запускает only raw baseline, без `--humanized --faithfulness`.
`check_examples.py` не смотрит `eval/corpus/humanized`. Optional faithfulness
зависит от Ollama embedding/LLM и при недоступном signal может принять verdict
по второму. Это не hard gate.

Вывод: aggregate style metric не может быть acceptance criterion. У нас
обязательны deterministic preservation facts/terms/status/causality и
independent semantic eval.

### Проверенный false positive на IT fixture

На v3.16.1 upstream scanner не защищал code fences, blockquotes и inline code, а
docs genre отсутствует: default `marketing`. На нашем clean
`slopspans-negative.md` он дал four hard bans / score 68, поймав «Данный» и тире
в fenced Go comment и «Стоит отметить» внутри blockquote. Regex false range
пометил clean «от broker до consumer».

С v3.19.3 (2026-09-07) защита есть: `markdown.py` и `analyze()` маскируют блоки
кода по CommonMark, inline code, цитаты `>` и короткие цитаты в «ёлочках» до
подсчёта; границы проверены таблицей из 27 вариантов. Текущая регулярка ложного
диапазона латиницу не принимает, поэтому «от broker до consumer» больше не
срабатывает (по тексту регулярки, код не запускался). Docs genre всё ещё нет.

Текущий scanner `no-ai-slop` защищает такие spans; эту архитектуру сохраняем.
Метод апстрима с таблицей вариантов границ полезен, нового правила он не даёт.

### Что реально показывает AINL calibration

Strict catalog находил hard ban в 35.9% human scientific abstracts и
26.1–35.8% model abstracts: классы не разделены. Academic filter снижал human
hits до 4.2%, но unseen generator получал 2.1% против 3.2% human — направление
хуже случайного. `является` и long dash в этом corpus были чаще у humans.

Это доказательство register gating и отказа от detector claim, не source новых
bans.

Дальнейшая калибровка (2026-10-10, по отчётам апстрима до `f2fe1f3`) тот же вывод
подтверждает на трёх корпусах: AINL (35 158 аннотаций, 8 769 человеческих), M4
(5 759 пар) и LLMTrace (14 763 текста, 30 генераторов).

- Strict mode ловит ≥1 hard ban у людей и моделей почти одинаково: 35,9 % против
  26,1–35,8 % (AINL), 51,9 % против 50,0 % (M4), 40,4 % против 48,5 % (LLMTrace).
  Бинарный критерий «хотя бы один hard ban в документе» классы не разделяет.
  Плотность маркеров на 100 слов разделяет — в LLMTrace в 2,3 раза, жанровый
  фильтр в 2,8 раза; на парах M4 уверенно отделяется gpt, но не davinci-003.
  Это различие классов в корпусе, а не признак одного документа: скилл
  происхождение не судит и плотность основанием правки не делает.
- `является`, `данный`, длинное тире и калька переворачиваются при смене домена;
  канцелярит не разделяет классы нигде. Стабильны в двух доменах лишь несколько
  оборотов (`играет ключевую роль`, лифт 5,7 / 9,4).
- Единственный слой, где у людей почти 0 %, — следы инструмента и обвязка чата.
  Нулевой счёт не норма человека: без единого маркера 15–42 % человеческих текстов.
- Локальная правка («цитата → чем мешает читателю → действие», места без дефекта
  помечены KEEP, число находок не задаёт объём) дала 1 смысловое замечание на 65
  ответов против 15 у полного скилла и 18 у обычного редактора (195 вызовов).
  Надёжность средняя: один исполнитель, оценщики — агенты, часть исходников
  синтетическая. Разметку мы не берём: у нас есть таблица режима «проверка».
- Оговорки при факте инструкция не удерживала (5 снятых из 25 до и после),
  механическая сверка до и после находила потерю; на этом стоит сверка оговорок в
  `termguard`.
- В `f2fe1f3` апстрим сам убрал то, что мы отвергли в августе: «добавь авторскую
  позицию», «1–2 частицы на абзац», «20–30 % жёстких склеек», «замени тире
  дефисом». Подтверждение прошлого аудита, не новая причина что-то брать.

## Другие проекты

Проекты ниже использованы как review-only prior art: из них не vendored code
или text. Конкретные ideas перечислены для provenance и повторной проверки.

### petergyang/no-ai-slop

[Repository](https://github.com/petergyang/no-ai-slop), MIT. Compact edit и
detect modes, named spans, minimum-effective edit, voice preservation. Полезен
output contract; English Wikipedia-derived catalog не калиброван для Russian.

### blader/humanizer

[Repository](https://github.com/blader/humanizer), Siqi Chen, MIT. Главный
ancestor forks: Wikipedia catalog, before/after, voice calibration,
no-fabrication. Не переносить punctuation/quote bans и generic «human
irregularity» в Russian.

### SalZaki/antislop

[Repository](https://github.com/SalZaki/antislop), MIT. Сильная architecture:
rewrite/detect/category lenses используют один spec; deterministic engine
отделён от judged findings; allowlist и report-first CI. Borrow single rule
source и tiers, но не clean/light/heavy aggregate call.

### isatimur/de-slop

[Repository](https://github.com/isatimur/de-slop), MIT. Полезны split
rewordable/hollow, idempotence и labeled over-correction corpus. Не переносить
paragraph score и forced iteration loop.

### hwajongpark/slop-gate

[Repository](https://github.com/hwajongpark/slop-gate), MIT. Полезны exact
line/column, `{id, match, hint}`, allowlists, code-fence skip и explicit known
limitation line-by-line matching. Наш scanner должен match hard-wrapped
paragraphs.

### tbhb/vale-ai-tells

[Repository](https://github.com/tbhb/vale-ai-tells), MIT. Полезно surface
scoping: commit-specific rules не применяются к docs. Большой catalog имеет
overlap/FP; не копировать полностью.

### t0ddharris/slopster

[Repository](https://github.com/t0ddharris/slopster), MIT. Полезна идея
diff-aware report only для new findings. Fixed dash quota, swaps и pass score
отвергаются.

### Wikipedia:Signs of AI writing

[Advice page](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing),
CC BY-SA. Главная инструкция: signs descriptive, not prescriptive; visible
marker может указывать на fabricated source, synthesis или promotion. Page
сама перечисляет ineffective indicators и помечена incomplete для recent
models.

## Русская редакторская традиция

- Нора Галь: nominalization, genitive chains, needless borrowing, passive,
  tangled syntax, cliche — mechanisms, не detector.
- [Главред](https://glvrd.ru/about/): score — доля stop-words, не quality.
- Александр Григорьев, [«Пиши,
  переписывай»](https://discours.io/articles/culture/write-rewrite): universal
  infostyle теряет register и meaning.

## Итог borrow/adapt/reject

### Borrow

- exact spans;
- Fact-Lock;
- Terminology Lock и technical register;
- progressive disclosure;
- artifact registry с provenance и three-way fixtures;
- register/surface/audience gates;
- no-op already-good text;
- voice passport только из source/sample;
- input-as-data и Function/Surface Contract;
- trigger, protected-span, term, fact и boundary evals.

### Adapt

- current artifacts -> strict/contextual tiers;
- morphology -> grammar, не AI marker;
- structural questions -> source-backed editor judgment;
- source checklist -> anomaly, не fraud verdict;
- AINL -> false-positive evidence;
- CI -> report-first style, hard fail только Fact/Term/protected/strict residue.

### Reject

- score, authorship и model attribution;
- detector bypass и watermark removal;
- hard word/punctuation bans;
- deliberate errors и synthetic voice;
- perplexity/burstiness optimization;
- mandatory slang, idioms, emotion, first person или uneven rhythm;
- rewrite-whole threshold;
- English banlist, переведённый без Russian mechanism и FP analysis.
