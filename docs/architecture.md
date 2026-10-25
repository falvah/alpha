# Архитектура

Модульный монолит с серверной отрисовкой. Для проверяемого MVP он уменьшает число компонентов, поверхность API и риск рассогласования состояния. В браузере нет JWT, секретов и бизнес-решений: JavaScript обслуживает калькулятор, меню и вспомогательные элементы.

```mermaid
flowchart LR
  C[Браузер предпринимателя] --> W[Gunicorn + Django]
  E[Браузер сотрудника] --> W
  W --> A[Сессионная авторизация / CSRF / роли]
  A --> V[Forms + Views]
  V --> S[Транзакционные services]
  S --> R[domain: график платежей]
  S --> I[Проверенные входные данные компании]
  I --> M[Полная карта mlcard: факторы, PD, лимит и ставка]
  M --> S
  S --> D[(SQLite: данные, PDF, события)]
  W --> T[Шаблоны + локальная статика]
  D --> N[Уведомления / персональная лента]
  N --> V
```

- `Company` принадлежит одному пользователю. В MVP нет делегирования доступа нескольким представителям одной компании.
- `Application` содержит параметры кредита, отдельные предложенные условия, версию, ответственного, согласие, оценку и снимок реквизитов/финансов.
- Отправка фиксирует снимок компании, версию правил, результаты проверок и момент согласия. Изменение профиля не меняет отправленные заявки.
- Уточнение даёт возможность исправить данные и снова отправить заявку. Новый снимок фиксируется при повторной отправке. Журнал хранит события и комментарии, но не является полным архивом всех старых финансовых снимков.
- `Event` записывается в той же транзакции, что и статус. Пользовательских операций изменения/удаления журнала нет. Администратор БД технически может его изменить: это не WORM-хранилище.
- PDF хранится как бинарный объект в БД, не как доступный через URL файл. На списках `defer('content')` исключает чтение содержимого. Максимум 25 МБ файлов на заявку; для промышленной нагрузки нужен объектный storage.
- `Notification` — долговечное сообщение внутри кабинета. Новости на главной — локальные материалы, выбранные по возрасту компании, статусам и последнему продукту, а не внешняя новостная интеграция.

## Состояния

```mermaid
stateDiagram-v2
  [*] --> draft
  draft --> submitted: согласие + валидные поля + документы
  submitted --> review: полный расчёт карты
  review --> info: запрос уточнений
  info --> submitted: повторная подача
  review --> approved: автоматическое одобрение или решение эксперта
  review --> counteroffer: другие условия
  review --> rejected: автоматический отказ или решение эксперта
  approved --> funded: клиент принимает предложение
  counteroffer --> funded: клиент принимает предложение
  draft --> cancelled: отмена клиентом
  submitted --> cancelled: отмена клиентом
  review --> cancelled: отмена клиентом
  info --> cancelled: отмена клиентом
  approved --> cancelled: отказ от предложения
  counteroffer --> cancelled: отказ от предложения
  rejected --> [*]
  cancelled --> [*]
  funded --> [*]
```

Обычная отправка, JSON API и пересчёт экспертом вызывают полный движок `credit/mlcard` из `docs/reference/mlcard`. Серверный источник `Company.trusted_scoring_data` передаёт проверки и показатели документа; клиентская анкета задаёт сумму, срок и цель. Вход сохраняется в `scoring_input`, полный результат — в `scoring`, ревизия/дата/отпечаток источника — в `checks`. `scorecard_workflow` применяет одобрение, встречное предложение, отказ или ручную проверку. Старый условный расчёт удалён. [Правила и происхождение карты](scorecard.md). Принятие и отмена доступны только владельцу. Повторное принятие уже выданной заявки возвращает тот же результат без новой записи о выдаче.

## Модель данных

```mermaid
erDiagram
  User ||--o| Company : owns
  Company ||--o{ Application : creates
  User o|--o{ Application : assigned
  Application ||--o{ Document : contains
  Application ||--o{ Event : records
  Application ||--o{ Notification : generates
  User ||--o{ Event : acts
  User ||--o{ Notification : receives
  Company {
    int id PK
    int owner_id FK
    string kind
    string name
    string inn
    string ogrn
    decimal revenue
    decimal expenses
    decimal existing_payments
    int business_months
    json trusted_scoring_data
  }
  Application {
    uuid id PK
    int company_id FK
    string product
    decimal amount
    int months
    decimal rate
    string status
    json company_snapshot
    json scoring
    json scoring_input
    json checks
    decimal offer_amount
    int offer_months
    decimal offer_rate
    int version
    datetime consent_at
    datetime accepted_at
  }
  Document {
    uuid id PK
    uuid application_id FK
    string kind
    string name
    binary content
    int size
    string sha256
  }
  Event {
    int id PK
    uuid application_id FK
    int actor_id FK
    string from_status
    string to_status
    string title
    string note
    datetime created_at
  }
```

## Надёжность и границы масштаба

Сервисные изменения обёрнуты в `transaction.atomic()`. В SQLite задан `transaction_mode=IMMEDIATE`: запись резервируется в начале транзакции. Клиент передаёт `version`; устаревшая форма отклоняется. При переходе на PostgreSQL `select_for_update()` обеспечивает блокировку строки, но смену БД и миграцию документов следует проверить нагрузочными и конкурентными тестами.

Синхронные локальные проверки детерминированы и быстры; очереди задач нет. Удалённые БКИ/реестр потребуют timeouts, retry/backoff, idempotency keys и outbox. Горизонтальное масштабирование текущего SQLite-развёртывания не заявляется.

Health endpoint проверяет соединение с БД. Docker содержит healthcheck, сохраняемый volume, непривилегированного пользователя и ограничение ресурсов. Логи Gunicorn доступны в stdout; пароли и тела запросов приложение не пишет.
