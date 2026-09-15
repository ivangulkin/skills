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

**Marks come from the construction, not from the pause.**

- The dash has positions, and outside them another mark is right: zero copula
  (`Kafka — распределённый лог`), ellipsis, an asyndetic sentence. It does not
  replace a comma around a parenthesis.
- The colon points backwards: cause and explanation come *after* it (`Сборка
  упала: кончилось место`). Cause first takes a dash (`Кончилось место — сборка
  упала`).
- After a colon, lowercase — except direct speech and a quotation.
- No comma after `однако`, `поэтому`, `причём`, `тем не менее` opening a
  sentence, and none after an opening adverbial of time: `В 2024 году мы
  перешли…`, not `В 2024 году, мы…`. Both are calques of English commas.
- An indirect question ends with a full stop: `Непонятно, почему это
  происходит.`

**Sentences carry their own logic.**

- Known first, new last. The end of the sentence is the emphatic position.
- Pick the conjunction from the relation that actually holds. `и` does not
  become `поэтому`: that asserts a causality nobody established.
- A gerund clause belongs to the subject of its sentence. `Проанализировав
  логи, стало ясно` has no subject to belong to.
- Verb aspect carries the status of the work: `исправляли` is not `исправили`,
  and an edit that changes the aspect changes the fact.

**Form follows the genre.**

- A reference section takes a noun heading (`Выполнение команд`), a how-to takes
  an infinitive, a FAQ takes a question. Title Case does not exist in Cyrillic.
- `вы` is lowercase. No colon after a section heading.
- Do not open with `В современном мире`, and do not call something important
  instead of showing why.

**And do not imitate a human.** Typos, slang, particles and ragged sentence
length for the sake of "liveliness" are as much a defect as officialese. The
goal is Russian that is clear and exact, not Russian that passes a detector.

Invoke `ru` for anything longer than a couple of sentences: it carries these
rules with the paragraph each one stands on, plus the editorial layer, in one
procedure — wordiness, clichés, an assertion with no source behind it.
```

Ядро делится надвое, и деление намеренное.

**Пять пунктов про всякую правку** — язык артефакта, защита смысла, порядок
слоёв, объём правки по просьбе, возврат без изменений как результат. Порядок
слоёв стоит здесь потому, что его нельзя восстановить из справочника
постфактум: если модель сначала пригладила язык, неверное утверждение уже стало
убедительнее.

**Пятнадцать правил, которые срабатывают на каждой русской фразе** — знаки,
построение предложения, форма заголовка. Они здесь по другой причине: **тело
скилла грузится при вызове, а короткая реплика в чате скилл не вызывает.**
Правило, которое нужно в каждом втором предложении, не может жить только в
справочнике, который откроют по просьбе.

Отбор такой: сюда попадает то, что (1) срабатывает на обычной фразе, а не на
документе, (2) ломается у агентов чаще всего и (3) формулируется одной строкой
без условий. Всё, что требует условия, регистра или параграфа, остаётся в
карточке.

Цена: блок `Prose` — 62 строки, около 900 токенов, весь `CLAUDE.md` — около
2000. Это на каждую сессию, независимо от того, будет ли в ней русский текст.

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
печатает восемь строк с теми же правилами. На английском ходу он молчит и не
стоит ничего. Новых правил он не вводит: всё, что он говорит, уже есть в ядре.

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
[claude-code.md](claude-code.md), раздел «Установка».

## Что ядро не гарантирует

`CLAUDE.md` — это контекст, а не конфигурация. Документация Claude Code
формулирует прямо: содержимое приходит как сообщение пользователя после
системного промпта, и строгого соблюдения нет.

Механизмы, которые дают больше, — output style (заменяет системный промпт) и хук
`UserPromptSubmit` (впрыскивает текст в каждый запрос). В этот комплект они не
входят: необходимость не показана, а цена — либо потеря встроенных инженерных
инструкций, либо расход контекста на каждом ходу. Заводить их стоит, когда
найдётся конкретный отказ, который они чинят.
