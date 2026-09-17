# Постоянное ядро

Скилл грузится, когда модель сочла его нужным. Короткая реплика в чате его не
вызовет, а правила русской речи нужны и там. Поэтому у комплекта два слоя:
постоянное ядро в инструкциях и подробный свод в скилле.

Ядро вставляется в существующий блок `Prose` файла `claude/CLAUDE.global.md`.
Отдельного блока не заводим: там уже написано про текст для человека, и второе
место с теми же словами разъедется через месяц.

Язык ядра — английский, как и весь файл. Правила касаются русского текста, но
записаны на языке окружающих инструкций.

---

## Текст вставки

Блок `Prose` целиком после правки:

```markdown
# Prose

Write each artifact in the language its area and repository require. Text
written for a human in Russian — chat answers first of all, then commits, code
comments, docs — follows the `ru` rules.

Five that hold for any edit:

- keep facts, numbers, conditions, negation, certainty, status, code, quotations
  and chosen terms through every edit;
- fix what the source says before fixing how it reads: wording smoothed over an
  uncorrected claim only makes the wrong claim more convincing;
- let the request set how much you may change. "Fix the punctuation" is not
  permission to cut the padding;
- a number is a reason to look again, never a reason to rewrite;
- returning the text unchanged is a result.

## Russian, always on

The skill body loads when it is invoked, and a two-line chat reply never invokes
it. These fire on every Russian sentence, so they live here instead.

Each one is the short form of a card that carries the paragraph and the
exceptions. Where the short form and the card disagree, the card is right.

**Marks come from the construction, not from the pause.**

- The dash has positions, and outside them another mark is right: zero copula
  (`Kafka — распределённый лог`), ellipsis, an asyndetic sentence. Emphasis,
  contrast and an explanatory apposition are positions too — `Наш основной
  инструмент — Prometheus — собирает метрики` is correct [ПАС § 63].
- The colon points backwards: cause and explanation come *after* it (`Сборка
  упала: кончилось место`). Cause first takes a dash. Both marks are allowed in
  the same place often enough that swapping one for the other is not a
  correction [ПАС § 129, прим. 2].
- After a colon, lowercase — unless the word is capitalised for its own reasons:
  a proper name (`Города: Москва и Омск`), direct speech, a quotation, or a
  numbered item set as its own line.
- `однако`, `поэтому`, `тем не менее` and an opening adverbial **do not
  themselves take a comma** — `В 2024 году мы перешли…`, not `В 2024 году, мы…`.
  But a comma right after them can belong to what follows: `Однако, проверив
  логи, мы нашли ошибку` is correct, and so is `Несмотря на отказ одного узла,
  кластер продолжил работу`. Look at the next construction before deleting it.
- An indirect question ends with a full stop: `Непонятно, почему это
  происходит.` A direct question keeps its mark even without a question word —
  `Порт свободен?` — and a question inside a subordinate clause can still be one:
  `Подскажите, где находится журнал?`

**Sentences carry their own logic.**

- Pick the conjunction from the relation that actually holds. `и` does not
  become `поэтому`: that asserts a causality nobody established.
- A gerund clause and the main verb need **one agent**, not a visible subject.
  `Проверив подпись, можно запускать установку` is correct — the agent is shared
  with the infinitive [Розенталь § 212]. What is wrong is two different agents:
  `Проанализировав логи, стало ясно`.
- Verb aspect carries the status of the work: `исправляли` is not `исправили`,
  and an edit that changes the aspect changes the fact.
- Known first, new last is how to place emphasis, not a test for an error. A
  sentence that is already clear is not rewritten to satisfy it.

**Form follows the genre.**

- The heading takes the form its section's job asks for: a noun for a reference
  section (`Выполнение команд`), an infinitive for a how-to, a question for a
  FAQ. `Как работает кэш` over an explanation is right, not a defect to
  normalise. Title Case does not exist in Cyrillic.
- `вы` is lowercase in chat and documentation. The respectful capital belongs to
  a letter addressed to one person, and there it is the author's call.
- Do not open with `В современном мире`, and do not call something important
  instead of showing why.

**And do not imitate a human.** Typos, slang, filler interjections and ragged
sentence length for the sake of "liveliness" are as much a defect as officialese.
This is about what you write: a particle that carries meaning in someone else's
text stays where it is.

**A count is a reason to read again, never a reason to rewrite.** Sentence
length, heading density, how many hedges or short sentences in a row — none of
these establishes a defect on its own. A limit the task actually sets is
different and binding.

Invoke `ru` for anything longer than a couple of sentences: it carries these
rules with the paragraph each one stands on, plus the editorial layer —
wordiness, clichés, an assertion with no source behind it — in one procedure.
```

Ядро делится надвое, и деление намеренное.

**Пять пунктов про всякую правку** — язык артефакта, защита смысла, порядок
слоёв, объём правки по просьбе, возврат без изменений как результат. Порядок
слоёв стоит здесь потому, что его нельзя восстановить из справочника
постфактум: если модель сначала пригладила язык, неверное утверждение уже стало
убедительнее.

**Правила, которые срабатывают на каждой русской фразе** — знаки, построение
предложения, форма заголовка. Они здесь по другой причине: **тело скилла
грузится при вызове, а вызов решает модель.** Правило,
нужное в каждом втором предложении, не может жить только в справочнике, который
откроют по просьбе.

## Короткая форма не отменяет условий

Это главное требование к ядру, и его легко нарушить незаметно. Памятка короче
карточки, поэтому соблазн выбросить условие вместе с многословием велик — а
результат хуже отсутствия правила: агент уверенно правит грамотный текст.

Три случая, на которых это уже ломалось:

| Было в ядре | Что это ломало |
|---|---|
| «после `однако` в начале предложения запятой нет» | `Однако, проверив логи, мы нашли ошибку` — запятая принадлежит обороту, а не слову. [Грамота, «Однако (же/ж)»] разрешает её прямо |
| «деепричастие относится к подлежащему» | `Проверив подпись, можно запускать установку` — безличное предложение с инфинитивом, [Розенталь § 212] его допускает |
| «после двоеточия строчная, кроме прямой речи» | `Города: Москва и Омск` — прописная у имени собственного не зависит от знака перед ним |

Отсюда правило отбора, и оно строже прежнего. В ядро попадает правило, которое
(1) срабатывает на обычной фразе, а не на документе, (2) ломается часто и
(3) **формулируется вместе со своим условием** в одну-две строки. Если условие в
эти две строки не влезает без искажения — в ядро идёт не правило, а действие
проверки: «посмотри, что идёт дальше», «спроси карточку».

И прямая оговорка в самом ядре: где короткая форма расходится с карточкой, права
карточка. Ядро — памятка, а не третья редакция нормы.

Цена: блок `Prose` вырос до 85 строк, около 1400 токенов, весь `CLAUDE.md` —
около 2500. Условия стоят этих строк: правило без условия правит то, что править
не нужно.

## Что переехало в ядро и почему

**Тип заголовка по жанру.** Раньше этого в ядре не было: правило без условия
ломает how-to (`Как запустить проверку` — законный заголовок) и FAQ. Условие
влезло в одну строку — справочник берёт существительное, инструкция инфинитив,
FAQ вопрос, — и правило переехало. Полная карта жанров осталась в
`references/headings.md`, H1–H6.

**Косвенный вопрос.** Раньше считалось, что в коротком ответе он почти не
встречается. Это оказалось неверно: агент задаёт вопрос в пояснении постоянно, и
это одна из тех ошибок, ради которых скилл и писался. Правило — в ядре, разбор
случаев — в `references/questions.md`, Q1–Q6.

## Чего в ядре по-прежнему нет

**Списков слов.** Ни запрещённых, ни рекомендованных. Под безусловный запрет
попадает правильная конструкция, и тогда правило ломает грамотный текст.

**Числовых порогов.** Их нет и в самом скилле; в ядре про это сказано отдельной
строкой — «число это повод перечитать, а не право переписать».

**Условий применимости.** Регистр, поверхность и аудитория решают, какое правило
к тексту применимо, и это целый справочник — `references/context.md`. В ядре
стоят только те правила, у которых условий нет: они верны для любой русской
фразы.

## Второй слой: напоминание на каждом ходу

Ядро читается один раз, при старте сессии, и в длинном разговоре уезжает в
дальний конец контекста. Хук `UserPromptSubmit` возвращает его к моменту ответа.

`claude/hooks/ru-reminder.sh` смотрит, есть ли в промпте кириллица, и если есть —
печатает пять строк. На английском ходу он молчит и не стоит ничего.

⚠️ **Третьей редакции нормы у хука быть не должно.** Он короче ядра, а ядро
короче карточки, и на третьем сокращении условие теряется гарантированно. Поэтому
в памятке стоят действия проверки — «посмотри, что идёт дальше», «вызови
карточку», — а не переформулированные правила, и она прямо говорит, что при
расхождении права карточка.

Механизм документирован: stdout хука `UserPromptSubmit` добавляется в контекст
модели, и сам хук выполняется до обработки промпта —
[документация](https://code.claude.com/docs/en/hooks).

Проверка кириллицы нарочно грубая — любой байт `D0`/`D1` в полезной нагрузке.
Ошибки здесь несимметричны: промолчать на русском ходу значит потерять то, ради
чего хук написан, а заговорить на английском стоит восьми строк контекста.

Хук **никогда не блокирует**: любой сбой оставляет ход в покое, все пути выходят
с нулём. Тесты — `claude/hooks/tests/ru-reminder-tests.sh`.

⚠️ **Хук не поедет автоматически, если `~/.claude/settings.json` разошёлся с
репозиторием.** Установщик такое назначение не перетирает, а сообщает о
расхождении и оставляет как есть. Что делать — в
[claude-code.md](claude-code.md), раздел «Установка». Для Codex — [codex.md](codex.md);
там постоянный слой тот же по смыслу, но напоминания на каждом ходу нет.

## Что ядро не гарантирует

`CLAUDE.md` — это контекст, а не конфигурация. Документация Claude Code
формулирует прямо: содержимое приходит как сообщение пользователя после
системного промпта, и строгого соблюдения нет.

Механизмов, которые дают больше, два, и в комплекте они на разных правах:

| Механизм | Состояние |
|---|---|
| хук `UserPromptSubmit` | **входит**: `claude/hooks/ru-reminder.sh` плюс регистрация в `claude/settings.json`. Он и описан выше в этом файле |
| output style (заменяет системный промпт) | **не входит**: цена — потеря встроенных инженерных инструкций, а необходимость не показана |

⚠️ Уровни не смешивать: хук лежит в репозитории `dotfiles`, а не в каталоге
навыка `ru`. Скилл работает и без него; хук добавляет напоминание на каждом ходу
в Claude Code. Что из этого фактически установлено на машине, показывает
`./install.sh --diff` и чтение `~/.claude/settings.json`, а не этот файл.
