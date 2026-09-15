# StoryScope: результаты, ограничения и перенос

Читай этот файл только при развитии structural pass или когда нужно обосновать,
почему после line edit проверяется shape текста.

Первичные источники, проверены 2026-08-21:

- Russell, Rajendhran, Pham, Iyyer & Wieting, *StoryScope: Investigating
  idiosyncrasies in AI fiction*, v6, август 2026:
  [paper](https://jenna-russell.github.io/assets/pdf/storyscope.pdf),
  [arXiv](https://arxiv.org/abs/2604.03136).
- [Код, taxonomy, features и model
  weights](https://github.com/jenna-russell/storyscope).

Paper — preprint under review. Он исследует English short fiction, а не
documentation, essays, business writing, Russian prose или reader-perceived
editorial quality.

## Corpus

Авторы извлекают 10,272 human stories из Books3. Gemini 2.5 Flash восстанавливает
из каждой story один writing prompt. Пять models отвечают на тот же prompt:
Claude Sonnet 4.6, GPT-5.4, Gemini 3 Flash, DeepSeek V3.2, Kimi K2.5. Вместе с
human source получается 61,608 planned instances; mean length 4,753 words,
paper сообщает 24 refusals.

Human stories в среднем 6,403 words. Model lengths различаются: Claude 6,817,
GPT 6,651, Kimi 3,274, Gemini 3,155, DeepSeek 2,946. Length-matched ablation
закрывает простой length confound, но не confound «published/edited book vs
one-shot generation».

## Десять dimensions и 304 features

| Dimension | Features | Что покрывает |
|---|---:|---|
| Agents | 54 | Roles, complexity, introduction, agency, emotion, trajectories |
| Social Networks | 39 | Relationships, community, power, reconciliation, gossip |
| Style | 39 | Diction, syntax, figurative texture, rhythm, prose-level allusion |
| Plot | 28 | Conflict, resolution, subplot, thematic unity, moral framing |
| Setting | 27 | Place, spatial detail, atmosphere, ecology, sensory description |
| Events | 26 | Event chain, causality, escalation, density, schemas |
| Revelation | 25 | Surprise, suspense, withholding, twist placement, recontextualization |
| Situatedness | 25 | Genre awareness, themes, morals, intertextuality, audience relation |
| Temporal Structure | 24 | Order, flashbacks, flash-forwards, duration, pacing, retrospection |
| Perspective | 17 | Person, focalization, narrator distance, reader address, interior access |

Feature types: categorical 124, ordinal 59, scale 45, binary 44, multi-select
32. Strict narrative model исключает 39 Style features и восемь
style-dependent features из других dimensions: остаётся 257.

## Как строится taxonomy

1. GPT-5.1 превращает story в structured JSON template по десяти dimensions:
   characters/motivations, social ties, concrete events/causes, plot, setting,
   time, revelation, perspective, style.
2. Held-out discovery pool: 100 prompts × 6 sources = 600 stories. Source labels
   скрыты, order рандомизирован.
3. GPT-5.1 с high reasoning сравнивает шесть templates. 32 batches содержат в
   среднем 3.1 prompts.
4. Десять dimension-specific prompts предлагают closed-form discriminative
   questions. Три runs дают 408 candidates.
5. Features кодируются F2LLM-4B и cluster single-linkage при cosine 0.85.
   Представитель у centroid каждого cluster сокращает 408 до 304.
6. Gemini 3 Flash с minimal thinking назначает features всем stories, по одной
   dimension на call. В small comparison aspect-based calls покрывают 95.4%
   features, single call — 68.4%.

Intermediate template не нейтрален. В early top-20 comparison pipeline на raw
text и template pipeline совпадают только по шести features. Raw-text variant
находит style, template variant — structure.

## Проверка annotation

Пять repeated Gemini assignment runs: Krippendorff nominal alpha 0.90, mean
pairwise Cohen's kappa 0.89. На 240 story-feature items в 12 stories два human
annotators согласуются с model при kappa 0.906 и 0.772, mean 0.839. Human-human
kappa 0.739.

Это поддерживает repeatability на small sample. Systematic bias остаётся:
Gemini размечает собственные stories, а consistency не означает independence.

## Classifiers и split

Все splits prompt-grouped. Layout: 7,383 train, 1,405 validation, 1,384 test
prompts. Final XGBoost retrained на train+validation: 8,788 prompts / 52,707
stories. Test: 8,301 stories.

| Binary human vs AI | Features | Macro-F1 | AUPRC |
|---|---:|---:|---:|
| Narrative | 257 | 93.2 | .959 |
| Core only | 30 | 84.8 | .828 |
| Core + fingerprints | 101 | 91.1 | .934 |
| Narrative + Style | 304 | 96.0 | .982 |
| Style only | 39 | 85.8 | .867 |
| ModernBERT raw text | — | 99.9 | 1.000 |
| Stylometric XGBoost | 144 | 99.8 | .999 |
| TF-IDF XGBoost | 5,000 | 99.7 | .999 |
| Binoculars zero-shot | — | 55.9 | .404 |

Фраза «retaining over 97%» сравнивает 93.2 с 96.0. Она не означает, что
narrative сильнее raw text: supervised raw-text baselines почти perfect в этом
in-distribution test.

Six-way attribution: Narrative 68.4 macro-F1; Core 46.5; Core+fingerprints
63.4; Narrative+Style 77.3; Style only 60.4. Raw text 99.5–99.8. Chance 16.7.

## Surface-edit robustness

LAMP применяется к 278 Gemini stories. Он использует 25 examples
professional writers и переписывает семь surface artifact classes, включая
cliches, purple prose и redundant exposition. Gemini переписывает свои же
stories.

Narrative detection меняется с 95.5 macro-F1 / .996 AUPRC до 93.9 / .988:
падение 1.6 points.

Поддерживается узкий claim: эти семь local edits почти не меняют features
classifier на Gemini subset. Не проверены structural rewrite, human editor,
другой rewriter, prompt steering, fine-tuning или adversarial optimization.

Paper не проводит experiment «avoid these patterns», temperature, planning или
reasoning при story generation. Minimal vs full thinking сравнивается только
для feature assignment: small test сообщает -0.45% binary downstream и
+11.61% six-way для minimal thinking. Это annotation result, не prose quality.

## Core differences

Table 15 содержит 33 direction-specific feature-option rows для 30-feature
core: несколько base questions дают и human-leaning, и AI-leaning option.
Scale values — means, percentages — prevalence. AI column усредняет пять
models.

| Feature | Human | AI |
|---|---:|---:|
| Thematic explicitness/moralizing, 1–5 | 3.28 | 3.94 |
| Moral/philosophical weighting, 1–5 | 3.26 | 3.68 |
| Thematic unity, 1–5 | 4.41 | 4.74 |
| Narrator comments on theme | 52% | 77% |
| Dialogue as philosophical debate | 34% | 59% |
| References as implicit echoes | 50% | 72% |
| Emotion as embodied metaphor | 38% | 81% |
| Setting mirrors inner state, 1–5 | 3.58 | 4.07 |
| Environmental/ecological emphasis, 1–5 | 2.83 | 3.21 |
| Olfactory imagery | 57% | 82% |
| Sensory density, 1–5 | 3.66 | 3.93 |
| Interior access depth, 1–5 | 3.67 | 3.93 |
| Main causal-chain continuity, 1–5 | 3.92 | 4.20 |
| Spatial granularity, ordinal mean | 2.27 | 2.53 |
| Resolution by protagonist choice | 46% | 69% |
| Character introduced by external description | 30% | 52% |
| No subplots | 57% | 79% |
| Resolution by internal understanding | 27% | 47% |
| Opening spatial grounding, ordinal mean | 2.12 | 2.33 |
| Pre-threat character investment, 1–5 | 2.76 | 2.99 |
| Explicit named intertextual reference | 47% | 24% |
| Balanced explicit/implicit references | 37% | 16% |
| Fourth-wall permeability, ordinal mean | 0.67 | 0.39 |
| Direct reader address, ordinal mean | 0.28 | 0.07 |
| Surprise recontextualization, 1–5 | 3.28 | 2.95 |
| Chronological discontinuity, 1–5 | 2.40 | 2.12 |
| Nonlinear framing delays disclosure, 1–5 | 1.96 | 1.68 |
| Anachrony intensity, 1–5 | 2.58 | 2.31 |
| Location variety, ordinal mean | 1.34 | 1.08 |
| Dialogue/narration proportion, 1–5 | 2.95 | 2.70 |
| Thematically parallel subplot | 42% | 21% |
| Morally ambivalent protagonist | 59% | 38% |
| Explicit emotion labels | 29% | 8% |

Body paper описывает pattern как over-determination: models чаще проговаривают
theme, держат continuous causal line, дают protagonist-driven/internal
resolution и показывают emotion через body/setting. Human corpus чаще называет
references, контактирует с reader, использует time discontinuity, subplots,
explicit emotion и moral ambiguity.

Это associations. Нельзя вставлять subplot, direct address, smell, ambiguous
ending или named work по quota.

## Model fingerprints

Selected fingerprints: Human 32, Claude 26, GPT 11, Gemini 11, DeepSeek 7,
Kimi 3.

- **Claude:** weakest event escalation, low event-type diversity, uniform
  voice, epilogues/flash-forwards, few dreams, uncanny setting, reverent
  continuation of tradition. Самый separable AI source narrative-only.
- **GPT:** gossip/rumor plot mechanism (64% vs 44–55%), distant retrospective,
  expectation subversion (41% vs 27–36%), ensemble social networks,
  partial/ambiguous reconciliation.
- **Gemini:** expanded social trajectory, direct speech, siege/ordeal schema,
  named characters, frequent flashbacks, high secondary-character density,
  extended denouement, tidy closure, bleak/oppressive setting в 88%.
- **DeepSeek:** visible narrator, emotion through behavior, plot/atmosphere
  orientation, evenly interleaved backstory, embedded storytelling, early
  placement of crucial context.
- **Kimi:** in-action character introduction, in medias res, little explicit
  trait labeling; только три fingerprints, generic center AI cluster.

Fingerprint version- и task-specific. Он не становится phrase detector модели.

## Diversity и rarity

Feature vectors z-scored. Mean human-AI centroid distance 6.6 против AI-AI
4.3. Closest human-AI pair дальше most distant AI-AI: 6.2 vs 6.0. Mean radius
human 33.2 vs AI 27.4; median 10-NN radius 33.1 vs 29.2.

Per-story rarity — mean Euclidean distance до 25 nearest neighbours в pooled
train+validation narrative space, затем percentile относительно distribution.

- mean percentile 0.71 human vs 0.49 AI;
- Cohen's d 0.83, AUC 0.73;
- human answer rarest среди шести prompt-matched versions в 57.8%, chance
  16.7%;
- 24.7% human stories попадают в corpus-wide top decile, AI 7.1%;
- top percentile: 3.0% human vs 0.6% AI.

Фраза «humans are 24.7% of top decile» неверна: 24.7% — доля human stories,
попавших в top decile, а не human share внутри decile.

## Confounds и threats to validity

У paper нет отдельного limitations section. Из method следуют риски:

- Books3 stories published/edited, model stories one-shot.
- Gemini 2.5 восстанавливает prompt из human story: human text идеально
  реализует свой source, models получают lossy restatement с theme/mood/details.
- Prompt включает target length human story, model adherence различается.
- GPT-5.1 создаёт taxonomy из 600 stories, Gemini назначает features: это
  model-induced representation.
- 304 features проходят selection criteria, но conventional multiple-testing
  correction для full discovery не reported.
- Robustness: один method, один source model, 278 stories, same model rewriter.
- Нет human-reader quality study и проверки, что удаление feature улучшает
  story.
- Models и prompts изменятся.
- Books3 несёт copyright/representativeness issues.
- Pangram Labs, commercial detector company, дала OpenRouter credits. Это
  disclosed support, не доказательство invalidity, но часть conflict review.

Нarrow checks: length-only classifier 55.9; Narrative остаётся 93.2 на
length-matched subset. Memorization-risk prompt removal меняет scores не более
чем на 0.27 points. Topic F1 90.0–96.2, omnibus test not significant.

## Что переносится в skill

Только как editorial questions:

- explicit moral -> sentence о значении evidence, которое уже говорит само;
- vague allusion -> unnamed tool/paper/team/authority;
- single-track plot -> argument, потерявший source-backed exception/failure;
- clean resolution -> verdict, скрывший documented cost/uncertainty;
- tight thematic unity -> разные sections, насильно проведённые через один
  template.

Fiction-specific и не импортируется: embodied emotion, smell, dreams,
character introduction, protagonist agency, subplots, flashbacks, epilogues,
reader address, revelation placement, social networks, setting mood.

93.2, 85.8, 96.0 и 1.6-point result нельзя представлять как performance на
non-fiction. Рабочий вывод уже: после lexical cleanup проверять source-backed
structure, а не изобретать новые word bans.
