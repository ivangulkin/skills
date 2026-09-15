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
comments, docs — follows the `ru` rules:

- keep facts, numbers, conditions, negation, certainty, status, code, quotations
  and chosen terms through every edit;
- fix what the source says before fixing how it reads: an edit to the wording
  laid over an uncorrected fact only makes the wrong claim smoother;
- connect sentences by the relation that actually holds, and pick the mark from
  the construction rather than from the pause;
- let the request set how much you may change. "Fix the punctuation" is not
  permission to cut the padding;
- returning the text unchanged is a result.

Invoke `ru` for anything document-sized: it carries the norm with its sources
and the editorial layer in one procedure. `no-ai-slop` is its former name and
points at the same rules.
```

Пять смысловых пунктов: язык артефакта, защита смысла, порядок слоёв (факты
раньше формы), выбор знака по конструкции, объём правки по просьбе. Плюс строка
о том, что возврат текста без изменений — результат, и куда идти за
подробностями.

Порядок слоёв стоит в ядре не для красоты: он единственный, который нельзя
восстановить из справочника постфактум. Если модель сначала пригладила язык, а
потом взялась за факты, неверное утверждение уже стало убедительнее.

## Чего в ядре намеренно нет

**«Заголовки номинативные».** Это предпочтение для отчётов. Без условия оно
ломает инструкции (`Как запустить проверку` — законный заголовок how-to) и FAQ.
Правило с условиями живёт в `references/headings.md`, H1–H6.

**Различение прямого и косвенного вопроса.** Занимает место, а в коротком ответе
почти не встречается. Правило — в `references/questions.md`, Q1–Q6.

**Списки слов.** Ни запрещённых, ни рекомендованных. Жёсткий бан лексики
измеримо роняет качество письма, а модель обходит его синонимом.

**Числовые пороги.** Их нет и в самом скилле.

## Что ядро не гарантирует

`CLAUDE.md` — это контекст, а не конфигурация. Документация Claude Code
формулирует прямо: содержимое приходит как сообщение пользователя после
системного промпта, и строгого соблюдения нет.

Механизмы, которые дают больше, — output style (заменяет системный промпт) и хук
`UserPromptSubmit` (впрыскивает текст в каждый запрос). В этот комплект они не
входят: необходимость не показана, а цена — либо потеря встроенных инженерных
инструкций, либо расход контекста на каждом ходу. Заводить их стоит, когда
найдётся конкретный отказ, который они чинят.
