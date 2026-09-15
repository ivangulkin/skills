# Проверка retry

Стоит отметить, что данный runbook является достаточно простым. Operator
проверяет consumer lag и при необходимости запускает retry.

> «Стоит отметить, что данный фрагмент процитирован из incident report и не
> принадлежит автору runbook».

```go
// Данный код — verbatim; строку ошибки менять нельзя.
if err := consumer.Retry(ctx, msg); err != nil {
    return fmt.Errorf("retry failed for topic=%s: %w", topic, err)
}
```

Log sample:

    2026-08-21T10:12:44Z ERROR retry failed for topic=orders.v2: timeout

Таким образом, мы рассмотрели порядок проверки.
