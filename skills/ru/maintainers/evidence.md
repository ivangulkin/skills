# Что измерено, а что остаётся редакторским суждением

Читай этот файл, если пользователь оспаривает правило или если меняется
catalog. Для обычной правки он не нужен.

Источники проверены по первичным публикациям 21.08.2026.

Подробные материалы:

- [StoryScope: метод, core features, model fingerprints и границы
  переноса](storyscope.md)
- [Prior art: что взято, адаптировано и отвергнуто](prior-art.md)
- [Register, audience и false positives](../references/context.md)
- [Claim/source verification и evidence registry](../references/source-integrity.md)

## Уровни доказательств

1. **Измерено.** Named corpus или experiment подтверждает точный claim в
   проверенном языке и жанре.
2. **Выведено.** Rule следует из редакторской традиции или measured mechanism,
   но не проверено на текущем типе текста.
3. **Heuristic.** Полезный вопрос или deterministic trigger без калиброванной
   связи с quality.

Population-level difference нельзя превращать в per-document verdict. Русское
редакторское правило нельзя представлять как corpus result.

## Почему аудит называет spans

Shaib, Chakrabarty, Garcia-Olano & Wallace, [*Measuring AI “Slop” in
Text*](https://arxiv.org/html/2509.19163v2), v2, январь 2026.

Авторы опросили 19 experts и наняли пять professional copy-editors для pilot.
Трое с 15–30 годами опыта продолжили full annotation. Corpus — English news и
question-answering passages.

Для binary label «is this slop?» pairwise Cohen's kappa равен -0.15, 0.29 и
0.06: poor-to-fair agreement. Нельзя сокращать результат до «редакторы
совпадают на уровне случайности»: paper отдельно предупреждает о low prevalence,
а Gwet's AC1 равен 0.12, 0.42 и 0.28 — fair-to-moderate.

Для location проблемного текста paragraph-level pairwise span precision равен
0.80, 0.65 и 0.68. Agreement по кодам слабее: theme-level Krippendorff
alpha-MASI 0.34 для Information Utility и Style Quality и 0.45 для Information
Quality.

Вывод для skill консервативный: located findings лучше воспроизводятся и
полезнее reader'у, чем один headline score. Paper не доказывает, что любой
score невозможен. Его собственный GPT-5 span extraction с human annotations
совпадает плохо.

Taxonomy paper:

- Information Utility: density, relevance;
- Information Quality: factuality, bias/subjectivity;
- Style Quality: repetition, templatedness, coherence, fluency, verbosity,
  word complexity, tone.

В short QA сильнее factuality и structure; в news — coherence, tone, density,
relevance и bias. Это прямое основание для register gating.

## Структура после surface editing: StoryScope

Russell, Rajendhran, Pham, Iyyer & Wieting, [*StoryScope: Investigating
idiosyncrasies in AI
fiction*](https://jenna-russell.github.io/assets/pdf/storyscope.pdf), v6,
август 2026, preprint under review. [Code и
data](https://github.com/jenna-russell/storyscope).

На 10,272 Books3-derived prompts, отвеченных одним human source и пятью model
sources, paper извлекает 304 features по десяти narrative dimensions. Strict
narrative variant исключает Style и ещё восемь style-dependent features,
оставляя 257.

| Binary human vs AI | Macro-F1 | AUPRC |
|---|---:|---:|
| Narrative | 93.2 | .959 |
| Style only | 85.8 | .867 |
| Narrative + Style | 96.0 | .982 |
| 30-feature core | 84.8 | .828 |

На 278 Gemini stories LAMP убирает семь surface artifact classes. Narrative
classifier меняется с 95.5 до 93.9 macro-F1: падение 1.6 points.

Перенос не измерен. StoryScope исследует длинную English fiction, использует
LLM-induced и LLM-assigned features, сравнивает published Books3 stories с
one-shot generations и проверяет один rewrite method на одном source model.
Его scores не относятся к documentation или Russian prose.

В structural pass перенесены только editorial questions: stated moral, vague
reference, one tidy thread, verdict cleaner than evidence, uniform skeleton.
Source-backed caveat, name или second thread нельзя добавлять из paper.

Все feature rates, fingerprints, rarity и confounds — в
[storyscope.md](storyscope.md).

## English vocabulary после 2022 года

Kobak, González-Márquez, Horvát & Lause, [*Delving into LLM-assisted writing
in biomedical publications through excess
vocabulary*](https://arxiv.org/html/2406.07016), *Science Advances* 11(27),
2025.

Исследование использует 15.1M English PubMed abstracts 2010–2024 и строит
counterfactual 2024 из frequencies 2021–2022 без labeled LLM corpus.

Measured ratios: `delves` 28.0, `underscores` 13.8, `showcasing` 10.7.
Frequency gaps: `potential` 0.052, `findings` 0.041, `crucial` 0.037. Из 379
excess style words 2024 года 66% — verbs, 14% — adjectives. Covid excess
vocabulary была почти полностью content words; 2024 — style words.

Paper задаёт две границы:

- анализ corpus-level и не определяет отдельный abstract;
- он не отличает direct LLM use от людей, перенявших LLM-preferred vocabulary.

English academic word list не переносится в Russian и не является banlist.

Liang et al., [*Mapping the Increasing Use of LLMs in Scientific
Papers*](https://arxiv.org/abs/2404.01268), называют `realm`, `intricate`,
`showcasing`, `pivotal` главными separators в одном arXiv CS setup против
GPT-3.5. Это также corpus-specific observation.

## Большие banlists портят output

Paech, Roush, Goldfeder & Shwartz-Ziv,
[*Antislop*](https://arxiv.org/html/2510.15061v2), октябрь 2025.

Авторы сравнивают words, bigrams и trigrams в 2,000 creative-writing samples
на model с wordfreq, Reddit и Project Gutenberg baselines.

Масштабы различаются на orders of magnitude: `Elara` 85,513x,
`heart hammered ribs` 1,192x, `voice trembling slightly` 731x. Формула “It's
not X, it's Y” — 6.3x в некоторых models. Один instance — слабая улика.

Hard token banning на 8,000 patterns роняет writing quality до 28/100. Soft
backtracking suppression и targeted FTPO сохраняют quality лучше в reported
tests.

Paper изучает generation/training в English creative writing, а не post-hoc
editor. Применимый вывод один: patterns требуют weights и context, а не blanket
bans.

## Русские данные

Batura, Bruches, Shvenk & Malykh, [*AINL-Eval 2025 Shared Task: Detection of
AI-Generated Scientific Abstracts in
Russian*](https://arxiv.org/html/2508.09622v1).

52,305 Russian scientific abstracts, 12 domains, human text и generations
GPT-4-Turbo, Gemma2-27B, Llama3.3-70B, DeepSeek-V3, GigaChat-Lite. Два domains
и DeepSeek withheld для test.

Best accuracy: 91.22% development, 86.35% unseen-domain/unseen-generator test.
TF-IDF logistic regression baseline: 81.05% test. Domain и generator shift
материальны.

Human abstracts в среднем 126.4 words, model corpora 49.6–85.7. Paper также
сообщает примерно в десять раз больше digit occurrences у humans. Это свойства
corpus, сгенерированного из titles и keywords. Они не оправдывают shortening,
добавление чисел или digit-density threshold в docs. Default scanner не должен
превращать их в findings.

Limitations paper: только abstracts; generation только из title+keywords;
whole-document labels, а не mixed/edited spans. Benchmark измеряет provenance
classification, не пользу редакторской правки.

Дополнительная важная проверка из cloned
`ilyautov/humanizer-ru` на AINL train: strict phrase catalog находил хотя бы
один hard ban в 35.9% human abstracts и 26.1–35.8% model abstracts — классы
почти не разделялись. Academic gating снижал human hits до 4.2%, но unseen
generator всё равно ловился хуже human. Это prior-art experiment, а не
peer-reviewed result; он подтверждает необходимость register gating и отказа
от authorship claims.

Earlier Russian benchmark: [RuATD 2022](https://arxiv.org/abs/2206.01583).
В проверенном наборе нет Russian study, которая измеряет editor agreement по
weak spans или reader benefit конкретной правки.

## Русские правила: derived, не measured

Нора Галь, «Слово живое и мёртвое», «Берегись канцелярита», описывает
механизмы: verb вытесняется participle, gerund и nominalization; chains nouns в
косвенных cases; лишние borrowings; active заменяется passive; tangled syntax;
cliche и серость.

Эти механизмы объясняют Russian grammar и bureaucratese catalog. Они не
доказывают AI provenance.

Guardrails:

- [Главред](https://glvrd.ru/about/) пишет, что score показывает долю
  stop-words, а не quality, и признаёт false positives.
- Александр Григорьев, [«Пиши,
  переписывай»](https://discours.io/articles/culture/write-rewrite), показывает,
  что universal stop-word removal игнорирует register и упрощает meaning.

## Markers не определяют автора

Shaib et al.: human text может восприниматься как slop, а model output не
обязан им быть.

[Wikipedia:Signs of AI
writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) называет
catalog descriptive, not prescriptive и предупреждает: cleanup видимого sign
может спрятать deeper problem. Основные underlying issues — unattributed
synthesis, promotional framing, fabricated/misattributed citations.

Это WikiProject advice page, не policy; в августе 2026 page помечена как
неполная для recent models. Она же предупреждает о non-trivial detector errors.

Tool-specific citation markup доказывает tool residue во fragment, но не
authorship документа. Invisible symbols, placeholders, curly quotes, perfect
grammar и generic formal prose не являются provenance.

## Слепое сравнение до и после

В `SKILL.md` до 16.09.2026 стояло: «Единственная слепая панель, которую
кто-либо проводил над русским редакторским инструментом, дала 24 голоса за
текст **до** правки против четырёх за текст после, и все семь обвинений в порче
достались текстам после неё», со ссылкой на этот каталог.

**Записи об этой панели в дереве навыка нет.** Ни инструмент, ни состав
выборки, ни дата, ни способ предъявления текстов не сохранились; в
`evals/results/` таких чисел тоже нет. Утверждение снято из рабочих инструкций
16.09.2026 по двум причинам:

1. **Область.** «Единственная, которую кто-либо проводил» — утверждение обо всех
   существующих исследованиях. Разбор одной панели его не доказывает; корректная
   форма — «в проверенных источниках на такую-то дату другой не найдено».
2. **Условия.** Числа 24, 4 и 7 без инструмента, выборки и критерия оценки не
   перепроверяются и к нынешнему `ru` отношения не имеют: он измерялся не этой
   панелью.

Практическое следствие осталось в `SKILL.md` и опирается не на числа, а на
устройство задачи: где дефекта нет, изменение текста работает против автора.

Если исходные данные панели найдутся, их место здесь — с инструментом, датой,
составом выборки и тем, что именно предъявлялось оценщикам.

## Известные пробелы

- Нет verified study reader benefit правок этого skill.
- Russian span-level quality judgments не измерены.
- StoryScope -> non-fiction — inference.
- Marker half-life неизвестен.
- Sentence CV, dash density, digit density и section CV не откалиброваны по
  registers; default thresholds запрещены.
- Clean regex run не валидирует facts, citations, coherence, register,
  Terminology Lock или voice.
- Detector benchmark не оправдывает detector evasion.

Отсутствие measurement не запрещает editing. Оно меняет claim: называй правило
editorial judgment или heuristic и сохраняй source meaning.
