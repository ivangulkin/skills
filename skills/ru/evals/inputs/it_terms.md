# Consumer pipeline

В современном мире потоковой обработки стоит отметить, что данный pipeline
является мощным и гибким решением. Пайплайн читает события из topic
`orders.v2`. Producer пишет их в три broker, consumer group `billing`
обрабатывает partition параллельно.

Consumer lag растёт после 20k msg/s: handler упирается в seq scan по таблице
`invoices`. Retry не скрывает проблему. После трёх попыток message уходит в
DLQ, а backpressure ограничивает requests к PostgreSQL. Rate limiter здесь не
нужен: он режет входящий поток, но не разгружает медленный query plan.

В Kubernetes один pod держит 8k msg/s. При rollout одновременно работают две
версии consumer, поэтому rebalance занимает до 12 секунд. Эти данные сняты на
staging; production пока не измеряли.

Таким образом, этот комплексный подход открывает новые горизонты для
масштабируемой обработки сообщений.
