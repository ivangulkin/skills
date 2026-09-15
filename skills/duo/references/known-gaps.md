# Известные пробелы

Что найдено, что закрыто и что **сознательно не закрыто**. Каждый пункт —
реальный отказ, а не стилистика. Список ведётся, чтобы пробел не потерялся, и
чтобы никто не принял молчание за отсутствие проблемы.

f1–f6 нашёл Codex на живых прогонах 03.09.2026, f7 — наблюдение перезапуска
сессии 04.09.2026. Дальше — двадцать шесть раундов `duo-review` с `gpt-6-astra`
на `max` 05.09–07.09.2026: 69 записей в таблице ниже, из них
семь нашёл не Codex, а собственная проверка тех мест, о которых его
спрашивали. Фаза закрылась как `converged` 07.09.2026: согласие продержалось
два раунда подряд, и до этого дважды не удерживалось — оба раза по делу.

## Закрыто 04.09.2026 реализацией слоя состояния v2

f1–f7 и три p1 из раунда 3 закрыты в `scripts/duo/state.py` и
`scripts/duo/consult.py`; каждое — тестом в `tests/test_duo.py`:

| Было | Стало | Тест |
|---|---|---|
| f1 нет сериализации | постоянный `flock` на ключе на весь вызов | `TestStateLock`, `test_the_lock_is_reported_not_ignored` |
| f2 orphan между `thread/start` и `save` | durable `creating`-intent до вызова, `thread_id` после | `test_crash_after_thread_start_leaves_an_invisible_row_and_no_duplicate_turn` |
| f3 fsync каталога | есть | `test_the_rename_is_made_durable` (v1) |
| f4 таймаут без результата | `--request-id` = `clientUserMessageId`, восстановление через `thread/turns/list` | `test_a_silent_turn_is_interrupted_and_resumable` |
| f5 битый state = отсутствующий | только `ENOENT` = отсутствие; карантин с tombstone | `TestTombstone` |
| f6 `unlink` маскирует ошибку | свой `try/except` | в `_write_atomic` |
| f7 CLI-id не переживает перезапуск | ключ — host-id, автомиграция, `recover --from` | `TestMigration`, `test_recover_pulls_an_old_key` |
| окно доставки результата | `open_request` с `result_unacknowledged`, чужой id отказывается | `test_a_lost_request_id_cannot_start_a_second_turn` |
| окно `save → unlink(.blocked)` | `block_id` / `resolves_block_id` | `test_a_matching_replacement_releases_the_block` |
| перенос не crash-atomic | двухфазный `migrating` на источнике; существующее назначение всегда побеждает | `test_a_half_done_move_is_finished_not_served`, `test_an_existing_destination_for_the_same_thread_wins` |
| ревью-1: результат и счётчик раундов в разных `save` | один `_commit_result` с `decision` внутри | `test_crash_after_save_replays_with_the_decision` |
| ревью-1: `previous_binding` не читался | intent продолжается по тому же `request_id`, старый тред уходит в `archive_debt` | `test_new_crash_after_intent_keeps_the_old_thread_owed` |
| ревью-1: битые вложенные записи молча терялись | строгий парсер → карантин | `test_a_malformed_open_request_is_a_schema_error` |
| ревью-1: миграция в незалоченный ключ | цели полуготовых миграций входят в lock set до захвата | `test_migration_crash_after_marking_source_finishes_next_run` |
| ревью-1: форк виден до записи id | `forking`-intent, подметание по `forkedFromId` | `test_fork_crash_after_response_is_swept_next_round` |
| ревью-2: принятый ход + Desktop открыл тред до повтора | при busy в recovery — сначала форк, потом история форка | `test_accepted_turn_then_desktop_holds_the_thread_does_not_duplicate` |
| ревью-2: восстановление не подтверждало создание треда | один переход `_confirm_creation` на оба пути | `test_new_over_an_unconfirmed_creation_owes_both_threads` |
| ревью-2: `--new` поверх незавершённого создания терял прежний тред | долг наследуется вместе с полусозданным тредом | `test_new_over_an_unconfirmed_creation_owes_both_threads` |
| ревью-2: начатую миграцию можно было перенаправить | источник, помеченный в другое место, — ошибка; `recover` сначала дозавершает | `test_a_started_move_cannot_be_pointed_somewhere_else` |
| ревью-2: `migrating`, `forking`, `archive_debt` парсились нестрого | строгая схема вложенных записей → карантин | `test_a_malformed_migrating_record_is_quarantined`, `test_a_string_archive_debt_is_quarantined` |
| ревью-2: `close --archive` терял привязку при отказе сервера | отказ архивации — ошибка, привязка остаётся | `test_close_archive_keeps_the_binding_when_the_server_refuses` |
| ревью-2: бесблокировочный поиск целей миграции мог менять файлы | `_peek` вместо `load`: только чтение | `TestMigrationRedirect` |
| ревью-2: не было теста на исчерпанное ожидание | код 10, ход не тронут, `turn/start` не отправлен | `test_a_still_running_turn_exits_ten_and_keeps_the_request` |
| ревью-2: fake отдавал ISO-время и одну страницу | Unix-секунды и настоящий `nextCursor`, как в схеме 0.153.4 | `test_the_held_original_is_owed_an_archive_across_pages` |
| ревью-2: утечка pipe в тестах lock | `stdout` держателя закрывается | прогон под `-W error::ResourceWarning` |
| ревью-3: другой запрос поверх незавершённого создания терял тред | наследование долга не зависит от `--new` | `test_a_plain_round_after_a_failed_new_still_owes_the_old_thread` |
| ревью-3: отставленный ключ читался как свободный | durable-редирект `<key>.moved`, перенос идёт по цепочке | `test_a_retired_key_redirects_instead_of_being_recreated` |
| ревью-3: v2 читался с приведением типов | строгий разбор без coercion, v1 остаётся мягким | `test_a_numeric_thread_id_is_quarantined_not_read_as_absent` |
| ревью-3: `close --archive` не гасил долг | сначала долги, потом тред, иначе ничего | `test_close_archive_settles_the_debt_before_forgetting` |
| ревью-4: раунд под перенесённым ключом открывал второй тред | канонический ключ — конец маршрута | `test_a_plain_round_follows_the_redirect_instead_of_starting_over` |
| ревью-4: `recover` писал не туда, куда приземлился перенос | запись по `moved.state_key` | `test_recover_lands_the_binding_where_the_move_ended` |
| ревью-4: пустой `thread_id` читался как «треда нет» | пустая строка в v2 — карантин | `test_an_empty_thread_id_is_quarantined` |
| ревью-4: ошибка чтения редиректа маскировалась под отсутствие | только `ENOENT` — отсутствие | `test_an_unreadable_redirect_is_a_fault_not_an_absence` |
| ревью-5: помеченный перенос без назначения читался как новая сессия | под блокировкой завершаются все помеченные ключи | `test_a_move_marked_but_never_written_is_finished_not_ignored` |
| ревью-5: цепочка усекалась молча | цикл и предел — ошибка | `test_a_loop_of_redirects_is_an_error_not_a_truncated_answer` |
| ревью-5: испорченный редирект блокировал и команды восстановления | `duo repair-redirect` берёт только названный ключ | `test_a_broken_redirect_can_be_set_aside_and_work_resumes` |
| ревью-6: ошибка чтения источника под блокировкой давала «переносить нечего» | строгое чтение, карантин — единственное исключение | `test_an_unreadable_source_stops_the_command_instead_of_starting_over` |
| ревью-6: петля A→A маскировалась под отсутствие редиректа | назначение, равное ключу, — ошибка | `test_a_redirect_pointing_at_its_own_key_is_a_fault` |
| ревью-6: `close` и `ack` не доводили маршрут | оба зовут `settle_migrations` | `test_close_finishes_a_half_done_move_before_forgetting` |
| ревью-6: снятие редиректа у ключа без привязки давало дубликат | такой ключ блокируется, выход — `--to` или `--new` | `test_dropping_a_redirect_with_no_local_binding_blocks_instead` |
| ревью-7: заблокированный ключ маршрута пропускался, и пустое назначение читалось как новая сессия | `SettledRoute.blocked`, обычный раунд отказывается | `test_two_runs_after_a_quarantined_source_still_refuse` |
| ревью-7: маршрут перепроверялся от кандидата, а не от начала | `KeyRoute.start` хранится отдельно | `test_two_runs_after_a_quarantined_source_still_refuse` |
| ревью-7: починка прятала обязательства ключа | проверяются `creating.previous_binding` и `archive_debt` | `test_a_repair_cannot_hide_an_unconfirmed_creations_debt` |
| ревью-7: нечитаемое состояние читалось как «привязки нет» | пробрасывается, ловится только карантин | `test_an_unreadable_local_state_blocks_the_repair` |
| ревью-8: `recover` мог записать ключ вне захваченного набора | реестр держащихся ключей, `migrate` требует блокировку на источник, назначение и пересылку | `test_migrate_refuses_a_forward_target_it_does_not_hold` |
| ревью-8: legacy-файл оценивался против начального ключа | конец маршрута считается до подтягивания | `test_recover_holds_the_destination_it_ends_up_writing` |
| ревью-9: `recover` не завершал перенос, помеченный на переданном ключе | в завершение уходит полный захваченный набор обеих сторон | `test_recover_finishes_a_move_marked_on_a_key_it_was_given` |
| ревью-9: `migrate` мог карантинить источник до проверки блокировки | `require_held` перенесён в начало, до `load` | `test_migrate_checks_the_lock_before_it_can_quarantine` |
| раунд удержания: severity вне шкалы понижалась до необязательной | значение вне `p0..p3` считается блокирующим | `test_a_severity_outside_the_scale_still_blocks` |
| раунд удержания: бриф не в UTF-8 падал трейсбеком | понятная ошибка с байтом и путём | `test_a_brief_in_another_encoding_is_explained_not_a_traceback` |
| раунд удержания: BOM уезжал внутрь сообщения | чтение через `utf-8-sig` | `test_a_byte_order_mark_does_not_travel_into_the_message` |
| раунд удержания: кривой вердикт читался как чистое согласие | структура проверяется до нормализации, `stop` только булев | `test_a_string_stop_is_not_a_stop`, `test_findings_as_an_object_is_a_parse_error_not_an_empty_list` |
| раунд удержания: форк уходил в каталог запуска, а не треда | в `thread/fork` передаётся `record.cwd` | `test_a_fork_uses_the_threads_directory_not_the_current_one` |
| раунд 12: исчезнувший каталог треда давал голый errno | предупреждение и продолжение в текущем каталоге | `test_a_thread_whose_directory_vanished_continues_where_it_is` |
| раунд 12: `findings: null` и отсутствие поля читались как «возражений нет» | все поля контракта обязательны, `null` — ошибка | `test_null_findings_is_a_parse_error_not_an_empty_list`, `test_every_contract_field_must_be_present` |
| раунд 12: JSON-пример в середине ответа принимался за вердикт | после блока допустим только пробел | `test_a_json_block_in_the_middle_is_not_the_verdict` |
| раунд 12: `--stop-hold 0` давал `converged` на первом раунде | счётчики строго положительные | `test_stop_hold_below_one_is_refused_by_the_cli` |
| раунд 12: форк подменял каталог, если записанный исчез | отказ вместо подмены репозитория | `test_a_fork_refuses_rather_than_change_repository` |
| раунд 13: строгость отвергала настоящий вердикт без `summary` | обязательны только `stance`, `stop`, `findings` | `test_a_missing_prose_field_is_not_a_parse_error` |
| раунд 13: fence разбирался без границ строк | три кавычки в строке и пример впереди больше не ломают разбор | `test_backticks_inside_a_string_do_not_break_the_trailer` |
| раунд 13: тест отказа форка не доходил до проверяемой ветки | ветка вызывается напрямую, сервер падает при обращении | `test_a_fork_refuses_rather_than_change_repository` |
| раунд 14: выделение трейлера теряло корректные ответы | построчный проход по fence вместо регулярного выражения | `test_a_marked_example_before_the_verdict_is_ignored`, `test_two_blocks_with_windows_line_endings` |
| раунд 14: девять классов тестов были определены дважды, поздний затирал ранний | дубли удалены, прогон падает на затенении | проверка «no shadowed tests» в `run-tests.sh` |
| раунд 15: пример вердикта с отступом в четыре пробела читался как согласие | отступ значим: четыре пробела — это literal, а не fence | `test_an_indented_example_is_not_the_verdict` |
| раунд 15: метка `c#` делала блок невидимым | метка свободная, `json` проверяется при выборе вердикта | `test_a_marked_example_before_the_verdict_is_ignored` |
| раунд 15: проверка затенения обходилась пробелом перед скобкой | имена берутся из синтаксического дерева | шаг «no shadowed tests» в `run-tests.sh` |
| раунд 16: `parse` снимал отступ до выделения трейлера | в разбор передаётся исходный текст | `test_an_indent_on_the_opening_line_alone_is_still_a_literal` |
| раунд 16: приоритет метки `json` терял безымянный вердикт после примера | выбирается последний подходящий блок, метка не решает | `test_an_unmarked_verdict_after_a_json_example_is_found` |
| раунд 17: тест отступа не падал на своём дефекте | префикс убран, проверено откатом | `test_an_indent_on_the_opening_line_alone_is_still_a_literal` |
| раунд 18: повторный ключ JSON стирал возражение или понижал severity | декодер отвергает повтор ключа на любом уровне, во всех трёх путях | `test_a_repeated_key_cannot_erase_a_finding` |
| раунд 19: глубокая вложенность роняла процесс с принятым ходом в `pending` | превышение глубины — обычная ошибка разбора | `test_a_verdict_nested_too_deeply_is_a_parse_error_not_a_crash` |
| раунд 19: непарный суррогат ломал сохранение и вывод после принятия хода | строки проверяются до применения вердикта, пары остаются допустимы | `test_an_unpaired_surrogate_is_refused_before_it_is_stored` |
| раунд 19: числовой `id` схлопывался в `inf` и останавливал спор раньше времени | `id` обязан быть строкой | `test_a_numeric_finding_id_is_refused` |
| раунд 20: проверка строк была рекурсивной и падала на глубине уровнем позже декодера | обход итеративный | `test_a_moderately_deep_verdict_does_not_escape_as_a_crash` |
| раунд 20: разбор ограждения откатывался экспоненциально | один жадный захват метки вместо трёх перекрывающихся квантификаторов | `test_a_pathological_fence_line_is_cheap` |
| раунд 21: обход строк копировал полный путь в каждую ветку, память ~ ключ × ширина | путь собирается только при ошибке | `test_the_string_walk_does_not_amplify_memory` |
| раунд 22: управляющие символы из ответа доходили до терминала, `ESC[8m` прятал возражения ниже | на границе вывода C0/C1/DEL спеллятся как `\xNN`, LF и TAB остаются; конфликт с `--json` отдаёт JSON | `TestTerminalBoundary` |
| раунд 23: три служебных пути шли мимо санитайзера — причина ошибки разбора, имя треда в `verify`, предупреждение о каталоге | граница вынесена в `terminal.py`; весь человеческий вывод обоих модулей идёт через `out`/`say`, JSON и состояние — нет | `test_a_parse_error_message_is_sanitised_on_first_run_and_replay`, `test_verify_sanitises_the_thread_name`, `test_the_directory_warning_is_sanitised` |
| раунд 24: диагностика `argparse` печатала argv сырым | `_Parser._print_message` идёт через `visible` | `test_argparse_diagnostics_are_sanitised` |
| 07.09: тред попадал в общий список Desktop, а не в проект | сначала подмена `cwd` на корень проекта — лишняя, снята 08.09 | — |
| 08.09: тред уезжал в чужой проект, потому что `$PWD` в Claude уплывает после `cd` | якорь — каталог чата из транскрипта; фиксируется в намерении, другой `--cwd` на повторе — отказ | `TestChatDirectoryAnchor` |
| 08.09: имя треда не следовало за заголовком чата | заголовок отправляется каждый настоящий раунд; `--name` закрепляет, `--name-from-chat` снимает | `TestThreadNameFollowsChat` |
| 08.09 ревью: продолжение создания решалось после выбора каталога, и `--new --cwd B` на повторе уводил тред | признак продолжения считается до каталога и не зависит от того, дошло ли до `thread/start` | `test_a_retried_creation_keeps_its_directory_even_with_new`, `test_a_retry_after_the_open_request_stays_in_the_chats_directory` |
| 08.09 ревью: быстрый выход при создании скрывал отказ переименования | выхода нет, имя отправляется всегда | `test_a_retried_creation_sends_the_new_pinned_name` |
| 08.09 ревью: нечитаемый транскрипт считался отсутствующим, тред уезжал в каталог shell | только `ENOENT` — отсутствие; ошибка чтения и относительный первый `cwd` — отказ | `test_an_unreadable_transcript_refuses_instead_of_wandering`, `test_a_relative_first_directory_refuses` |
| 08.09 ревью: `name_pinned: null` снимал закрепление | `null` — ошибка схемы; legacy пишется без поля | `test_an_explicit_null_pin_is_a_schema_error` |

Критерий, принятый в раунде 3: ни одного **видимого** orphan-треда и ни одного
дубликата хода. Нематериализованная строка после SIGKILL между ответом
`thread/start` и локальной записью допускается: у неё нет роллаута и пустой
`preview`, и `thread/list` её не возвращает — это закреплено тестом против
журнала fake, который применяет то же правило видимости.

## Регрессионные тесты проверены откатом выборочно

Тест, написанный по воспроизведённому сценарию, не обязательно падает на самом
дефекте. Раунд 17 нашёл ровно такой: проза перед примером с отступом делала
проверяемый отступ внутренним, а ломался отступ первой строки ответа. Тест был
зелёным и до, и после исправления.

После этого проверены откатом четыре ключевых места: приведение severity,
структурная проверка полей вердикта, `require_held` в переносе и отступ первой
строки. Каждое — снять исправление, прогнать тест, убедиться, что падает,
вернуть. Остальные тесты цикла так не проверялись: тридцать откатов стоят
дороже, чем дают. Это известное ограничение, а не закрытый вопрос.

Побочный вывод: `stop` защищён двумя независимыми проверками — структурной и
сравнением `is True`. Снятие любой одной тест не замечает, снятие структурной
роняет три теста. Дублирование здесь намеренное.

## Серия согласий не переживает границу сегмента фазы

Когда фаза упирается в `--max-rounds` с `agreed_unheld`, она считается
завершённой, и следующий `ask` начинает новый сегмент с нулевым счётчиком
раундов и нулевой серией согласий. Согласный раунд по одну сторону границы и
согласный по другую в серию не складываются: на живом прогоне 07.09.2026
Codex согласился в раунде 4 из 4 и снова в раунде 1 из 4 следующего сегмента,
а правило потребовало ещё один.

Это следствие того, что завершённая фаза сбрасывает счётчик — сброс введён
раньше ради отчёта «раунд 4 из 3» и оправдан. Считать ли серию сквозь границу
или нет — вопрос к правилу, а не дефект кода; сейчас оставлено как есть, цена —
один лишний раунд в редком случае.

## Ответ хранится целиком, без ограничения размера

Сводка на три миллиона символов принимается и целиком уезжает в файл состояния:
3 001 677 байт на пробе. Codex в раунде 22 дефектом это не счёл и рекомендовал
хранить полностью: цена — линейные память и ввод-вывод до подтверждения, после
`ack` файл сжимается обратно, на его пробе с 3 МБ до 842 байт. Накопления по
раундам нет. Так и оставлено; порог не введён сознательно.

## Windows

`open codex://threads/<id>` там не работает (issue openai/codex #30916);
`fsync` каталога не поддерживается и уже подавляется; путей с обратным слэшем
в коде нет, но и проверки на Windows не было. Скилл macOS-first.

## `SessionEnd`-хук не написан

Тред живёт после закрытия чата Claude. Это осознанно: человек может захотеть
дочитать его в Desktop. Хук, который архивирует тред по `close --archive`,
добавить просто; нужен ли он по умолчанию — не решено.

## Форкнутый оригинал помечается сразу и удаляется, когда Desktop отпустит

Измерено 07.09.2026 на 0.153.4: Desktop держит writer-lock каждого открытого
треда часами и не отдаёт при переходе на другой; `thread/archive` и, судя по
всему, `thread/delete` на удерживаемом треде отказывают, `thread/name/set` —
работает. Пока форк без следа, человек видел в проекте два треда с одним
именем и справедливо считал это нарушением правила «один чат — один тред».

Теперь при форке оригинал тут же переименовывается в `[старый] <имя>`, а его
id уходит в `supersede_debt`: история целиком в форке, поэтому долг гасится
удалением, не архивацией. Пока лок держится, удаление отказывает и тред
остаётся помеченным; каждый раунд пробует снова. `--new` по-прежнему
архивирует: заменённый тред больше нигде не хранится. Удаление удерживаемого
треда живьём не измерено — на неудерживаемом `thread/delete` убирает тред и
из живых, и из архивных.

## Форк: окно между ответом `thread/fork` и записью id — закрыто измерением

Раунд 2 ревью требовал зачищать осиротевший форк, считая его видимым. Измерение
05.09.2026 на 0.153.4 показало обратное: свежий форк не появляется в
`thread/list` — ни среди живых, ни среди архивных, ни после `thread/name/set`, —
хотя `thread/resume` на нём проходит и вся история источника в нём есть. Строку
в sidebar форк занимает только после собственного хода.

Поэтому падение в этом окне не оставляет ничего видимого, и подметать нечего:
код зачистки, поле `forking` и базовый список чужих форков удалены. Искать свой
форк перебором листинга было бы и невозможно — `forkedFromId` в этом сторе пуст
у всех тредов, — и опасно: единственное, что находит такой перебор, это форк,
сделанный человеком. Тесты — `test_a_fork_is_invisible_until_it_takes_a_turn`,
`test_a_fork_the_human_made_is_never_touched`.

## Ревьюер запускает тесты с правами пользователя — решено 07.09.2026

До этого дня тред создавался с `sandbox: read-only` и `approvalPolicy: never`,
и `bash tests/run-tests.sh` у Codex падал на создании временного каталога:
каждый раунд ревью он писал «полный прогон не запускал». Пользователь попросил
запускать Codex с теми же правами, что в Desktop — «Approve for me», то есть
`workspace-write`, `on-request` и `approvals_reviewer = "auto_review"` из его
`config.toml`, — потому что сидеть и подтверждать руками он не будет.

Поэтому `duo` больше не задаёт ни песочницу, ни политику: тред наследует конфиг
пользователя, согласования уходят авто-ревьюеру Codex, а `duo` отклоняет только
то, что всё же доходит до клиента. Запрет менять файлы остался в инструкциях
роли как правило, не как гарантия песочницы. Модель угроз изменилась
сознательно: `--sandbox read-only` возвращает прежнюю на один тред.

## Deep link и writer-лок

Не пробел, а свойство, о котором надо помнить: как только Desktop загрузил
тред, его app-server держит `thread-writer-locks/<id>.lock` и не отдаёт даже
после переключения на другой тред. Открывать тред в Desktop — после последнего
раунда фазы. Подробности в `protocol.md`.
