# Перезапуск consumer

В современном мире надёжная эксплуатация consumer играет ключевую роль.

Запускайте процедуру, если consumer lag растёт 10 минут и pod не отвечает на
health check. До начала сообщите в `#payments-oncall` и остановите rollout.

1. Выполните `kubectl rollout restart deployment/payments-consumer`.
2. Дождитесь состояния `Available=True`.
3. Проверьте, что consumer lag снижается в dashboard:
   https://example.com/dashboards/payments-consumer

Если новый pod не становится Ready за 5 минут, выполните rollback командой
`kubectl rollout undo deployment/payments-consumer` и передайте incident
дежурному payments. Процедуру нужно закончить до 18:00.

Таким образом, мы рассмотрели эффективный и бесшовный перезапуск consumer.
