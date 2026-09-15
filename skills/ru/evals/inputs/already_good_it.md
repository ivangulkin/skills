# Импорт прайс-листа

XML parser держит весь document в memory, поэтому large files раздувают
process. Попробовал батчить INSERT — стало заметно лучше. На COPY пока только
смотрел; benchmark не запускал.

Streaming parser, скорее всего, снимет memory spike, но это hypothesis. До
measurements менять import pipeline не будем. Иначе получим не «ускорилось», а
«сломалось по-другому».

Terminology здесь намеренная: parser, streaming, memory spike, benchmark,
pipeline, INSERT и COPY — слова команды, а не placeholders для перевода.
