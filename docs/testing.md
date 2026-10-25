# Проверки проекта — 25 октября 2026

| Проверка | Результат |
|---|---|
| Серверные тесты Django | **107 / 107** |
| Проверки JavaScript | **5 / 5** |
| Проверки запущенного Gunicorn | **102 / 102**, 104 HTTP-запроса, ни одного ответа 5xx |
| Подстановка неверных значений | **336 вариантов в 28 полях**, без необработанных исключений |
| `pip check` | Конфликтов зависимостей нет |
| `manage.py check` и `check --deploy` с HTTPS-настройками | Замечаний нет |
| `makemigrations --check --dry-run` | Незарегистрированных изменений моделей нет |
| Python AST, JavaScript и shell-синтаксис | Проверки прошли |
| `docker compose config --quiet` | Конфигурация корректна |
| Миграции и сборка статики | Применены к локальной базе и проверены на отдельной базе |

Процент успешных тестов относится только к указанному набору. Покрытие строк не измерялось. Реальные пользователи не участвовали; визуальный аудит браузера, WCAG, нагрузка и сканирование CVE-зависимостей не выполнялись. Контейнерный образ не собирался; приложение проверено под Gunicorn с тем же поточным worker.

## Повторный запуск

```sh
sh scripts/check.sh
```

Скрипт использует установленное окружение `.venv`. Node.js нужен только для пяти проверок JavaScript; npm-пакеты не устанавливаются. Django создаёт отдельную тестовую базу, рабочие записи не используются.

## Проверка настоящего HTTP-сервера

Создайте отдельный каталог через `mktemp -d`, сохраните путь в `AUDIT_DATA_DIR` и выполните:

```sh
AUDIT_DATA_DIR=$(mktemp -d /tmp/cabinet-audit.XXXXXX)
DATA_DIR="$AUDIT_DATA_DIR" .venv/bin/python manage.py migrate --noinput
DATA_DIR="$AUDIT_DATA_DIR" .venv/bin/python manage.py seed_workspace
.venv/bin/python manage.py collectstatic --noinput
DATA_DIR="$AUDIT_DATA_DIR" .venv/bin/gunicorn config.wsgi:application \
  --bind 127.0.0.1:8765 --workers 1 --threads 4 --no-control-socket
```

В другом терминале передайте тот же путь в `--access`:

```sh
.venv/bin/python -m tests.live_http \
  --base http://127.0.0.1:8765 \
  --access /tmp/cabinet-audit.XXXXXX/access.txt \
  --output /tmp/cabinet-http-results.json
```

Прогон изменяет только отдельную базу: создаёт один черновик при двух параллельных POST и принимает одобренное предложение. Для повторного полного прогона нужна свежая временная база. Пароли, cookies и CSRF-токены в отчёт не записываются. Результаты выполненного прогона: [http-checks.json](http-checks.json).

## Состав проверок

- `credit/tests.py`: кабинеты, заявки, документы, роли, CSRF и полный цикл.
- `tests/test_domain.py`: денежные расчёты, аннуитетный график и переходы статусов.
- `tests/test_audit.py`, `tests/test_hardening.py`: чужие объекты, устаревшие формы, подмена роли, лимиты и повторная отправка.
- `tests/test_scorecard.py`, `tests/test_scorecard_workflow.py`: контракт JSON, обе финансовые ветки, стоп-факторы, лимит, решение и принятие условий.
- `tests/test_security_inputs.py`: дублирующиеся ключи, неоднозначные алиасы, дробные копейки, повреждённый JSON, ошибочные типы, XSS, SQL-строки и актуальность проверок.
- `tests/test_configuration.py`: обратная совместимость настроек без отключения HTTPS.
- `tests/frontend.test.cjs`: устаревшие ответы калькулятора, потеря сети, неверные поля и переключение решения.
- `tests/live_http.py`: реальный HTTP, сессии и CSRF, изоляция клиентов, конкурирующие POST, документы и статические ресурсы.

Подробный разбор исправлений и очистки: [quality-audit.md](quality-audit.md). Числовые показатели: [quality-statistics.json](quality-statistics.json).
