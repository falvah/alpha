# API и веб-контракты

Машиночитаемая схема: `docs/openapi.json` (OpenAPI 3.1). Коллекция запросов: `docs/requests.http` для REST Client/HTTP-клиента. Внешних банковских API и настоящего скорингового провайдера нет.

## Скоринговая карта

`POST /api/v1/scorecard/`: JSON в русском или английском формате из [описания интеграции](scorecard.md). Только сессия сотрудника + `X-CSRFToken` из HTML-формы; до 64 КБ. Возвращает решение, доступную сумму, лимит, PD, покрытие, факторы, причины и флаги. Этот endpoint выполняет расчёт без создания заявки. Ошибочный JSON — 400, отсутствие сессии — 401, недостаточные права/CSRF — 403.

`GET/POST /applications/{id}/scorecard/`: форма сотрудника для привязки JSON к отправленной заявке. POST содержит `payload` (JSON-строка), `version`, CSRF. Сумма, срок и ИНН сверяются с заявкой; результат сохраняется и применяется транзакционно.

`GET/POST /bank/scorecard/`: рабочая форма сотрудника для расчёта без изменения заявки. POST содержит `payload` (JSON-строка) и CSRF. Клиентский импорт JSON с автоматическим одобрением удалён.

В `GET /api/v1/applications/{id}/` добавлено поле `scoring`: полный результат новой карты или null для старых заявок. `offer` и `payment` используют уменьшенную сумму после встречного предложения.

## Авторизация

Все данные кабинета доступны только в Django-сессии. `GET /login/` выдаёт страницу с CSRF-токеном и cookie. `POST /login/` принимает `application/x-www-form-urlencoded`: `username`, `password`, `csrfmiddlewaretoken`. После входа cookie сессии ротируется. `next`/произвольные redirect URL не используются.

Все изменения — POST с валидным CSRF-токеном и cookie. Токен берётся из HTML-формы; CSRF-cookie HttpOnly и не читается JavaScript. В тестах авторизация и CSRF проверяются отдельно. JSON endpoints возвращают 401 без сессии; веб-страницы перенаправляют на вход. Доступ к чужому объекту — 404, действие чужой роли — 403.

## JSON, только чтение

| Метод | URL | Результат |
|---|---|---|
| GET | `/health/` | `{ "status": "ok" }`, проверка доступности БД, без авторизации |
| GET | `/api/v1/products/` | Словарь продуктов: лимиты, срок, ставка, цель |
| GET | `/api/v1/calculator/?product=working&amount=1500000&months=24` | Строковые Decimal: платёж, сумма выплат, ставка; 400 при неверных параметрах |
| GET | `/api/v1/applications/?page=1` | `results`, `page`, `pages`; максимум 20 записей |
| GET | `/api/v1/applications/{uuid}/` | Своя заявка для клиента; отправленная заявка для сотрудника |

Формат объекта заявки:

```json
{
  "id": "UUID",
  "reference": "1234ABCD",
  "product": "working",
  "amount": "1500000.00",
  "months": 24,
  "status": "counteroffer",
  "status_label": "Новые условия",
  "version": 6,
  "payment": "52871.10",
  "offer": {"amount": "1000000.00", "months": 24, "rate": "24.00"},
  "decision_note": "Предлагаем меньшую сумму с учётом денежного потока.",
  "updated_at": "2026-10-22T12:00:00+00:00"
}
```

Число платежа в объекте соответствует указанным параметрам; фактическое значение возвращает сервер. Не используйте float для бухгалтерских расчётов.

## Изменения через веб-формы

Веб-интерфейс использует серверные формы, а не дублирующее CRUD JSON API. Это один контракт бизнес-действий с CSRF и проверками. Успех — `302 Location`; ошибки полей — HTML 400, отклонённый переход/старая версия — redirect с сообщением. У каждой сохранённой заявки поле `version` обязательно для изменения.

| URL | Поля / назначение |
|---|---|
| `POST /applications/new/` | `product`, `amount`, `months`, `purpose`, `creation_token`; создаёт черновик и переводит на шаг 2 |
| `POST /applications/{id}/edit/1/` | Те же поля + `version`; изменить финансирование |
| `POST /applications/{id}/edit/2/` | Поля компании из `CompanyForm` + `version`; сохранить профиль |
| `POST /applications/{id}/edit/3/` | `action=upload`, `kind`, `file`, `version`; multipart PDF |
| `POST /applications/{id}/edit/3/` | `action=submit`, `consent=on`, `version`, `company_version`; подача/повторная подача |
| `POST /applications/{id}/actions/review/` | `version`; сотрудник берёт заявку в работу |
| `POST /applications/{id}/actions/decide/` | `decision`, `note`, `version`; для `counteroffer` также `amount`, `months`, `rate` |
| `POST /applications/{id}/actions/accept/` | `version`, `confirm=on`; принять предложение и смоделировать выдачу |
| `POST /applications/{id}/actions/cancel/` | `version`; отмена владельцем |
| `POST /applications/{id}/documents/{doc_id}/delete/` | `version`; удалить файл только из черновика/уточнения |
| `POST /notifications/` | Отметить прочитанными только уведомления текущего пользователя |
| `POST /profile/` | Редактируемые поля собственной компании; не меняет снимки уже поданных заявок |
| `POST /logout/` | Завершить сессию |

`decision`: `approved`, `counteroffer`, `info`, `rejected`. Комментарий обязателен (10–2000 символов). Сотрудник не может сам принять предложение за клиента. `approved` использует исходные параметры, переданные клиентом поля `offer_*` игнорируются. Для заявки с JSON-скорингом дополнительно проверяются лимит и актуальность оценки. Изменение суммы, срока или ставки за пределы рассчитанных условий требует нового расчёта.

`GET /documents/{uuid}/download/` отдаёт attachment, MIME `application/octet-stream`, `nosniff`; никакой публичной media-директории нет. `GET /applications/{uuid}/schedule/` отдаёт CSV с UTF-8 BOM и разделителем `;`.

## Локальные интеграции

Вход симулятора — `Company.mock_registry` и `Company.mock_overdue`, установленные серверным сидером. Выход сохраняется в `Application.checks` с меткой `simulated` и временем. Поля симулятора отсутствуют в пользовательских формах. Для реального БКИ/ЕГРЮЛ потребуется новый адаптер с таймаутом, повторными попытками и контрактными тестами; никакие реальные запросы сейчас не отправляются.
