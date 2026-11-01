# skills

Скиллы для Claude Code и Codex.

## Что здесь

| Скилл | Что делает |
|---|---|
| [`ru`](skills/ru) | Пишет, правит и проверяет русский текст: знаки, заголовки, перечни, вода и штампы. Факты, числа, условия и термины правки не меняет |
| [`duo-claude-to-codex`](skills/duo-claude-to-codex) | Согласование плана (`--phase design`) и ревью работы (`--phase review`) с Codex из Claude Code в постоянном треде |
| [`duo-gemini-to-codex`](skills/duo-gemini-to-codex) | Согласование плана и ревью работы с Codex из Gemini / Antigravity в постоянном треде |
| [`duo-codex-to-gemini`](skills/duo-codex-to-gemini) | Согласование плана и ревью работы с Gemini / Antigravity из Codex в постоянной беседе |

## Установка

### Claude Code

```
/plugin marketplace add ivangulkin/skills
/plugin install ru@ivangulkin-skills
```

Поставленный плагином скилл вызывается как `/ru:ru`. Модель открывает его и сама,
по описанию.

### Claude Code и Codex через `npx skills`

```bash
npx skills add ivangulkin/skills --skill ru -g
```

Флаг `-a claude-code` или `-a codex` ограничивает установку одним клиентом.

### Вручную

```bash
git clone https://github.com/ivangulkin/skills.git ~/skills
ln -s ~/skills/skills/ru ~/.claude/skills/ru    # для Codex: ~/.codex/skills/ru
```

Новую версию подтягивает `git pull`.

## После установки

Скилл работает и так. Чтобы русский текст для людей надёжно попадал к нему, добавь
в `CLAUDE.md` короткий абзац: он в
[core-instructions.md](skills/ru/integration/core-instructions.md). Проверка и
отключение — в [claude-code.md](skills/ru/integration/claude-code.md) и
[codex.md](skills/ru/integration/codex.md).

## Разработка

Каждый скилл лежит в `skills/<имя>/` и самодостаточен: ссылки внутри него не
выходят за каталог. Правила для авторов скиллов — в [AGENTS.md](AGENTS.md).
Тесты `ru` запускаются локально:

```bash
cd skills/ru
python3 -m unittest discover -s evals -p 'test_*.py'
python3 scripts/validate_skill.py    # нужен PyYAML
```

Тесты скиллов `duo-*` идут против поддельных клиентов (`fake_codex.py` и `fake_agy.py`), без ключа и квоты:

```bash
bash skills/duo-claude-to-codex/tests/run-tests.sh
bash skills/duo-gemini-to-codex/tests/run-tests.sh
bash skills/duo-codex-to-gemini/tests/run-tests.sh
```

## Лицензия

MIT. Скилл `ru` несёт ещё уведомления о заимствованиях:
[THIRD_PARTY_NOTICES.md](skills/ru/THIRD_PARTY_NOTICES.md).
