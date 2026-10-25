# Проверки проекта

После упорядочивания структуры: **116 / 116 тестов Django и 5 / 5 тестов JavaScript**. Проверки зависимостей, конфигурации Django и отсутствия незарегистрированных миграций проходят. Дополнительно прошли **102 / 102 HTTP-проверки** реального Gunicorn на отдельной временной базе; свежий отчёт — `.artifacts/http-checks.json`.

Исторические отчёты до подключения полной модели находятся в `reports/2026-10-25-before-mlcard/`; они не описывают текущий расчёт. Покрытие строк, нагрузка и формальный аудит интерфейса не измерялись.

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

Прогон изменяет только отдельную базу: создаёт один черновик при двух параллельных POST и принимает одобренное предложение. Для повторного полного прогона нужна свежая временная база. Пароли, cookies и CSRF-токены в отчёт не записываются. Свежий JSON-отчёт записывается в путь, переданный через `--output`; его не нужно хранить среди исходников.

## Состав проверок

- `tests/test_cabinet.py`: кабинеты, заявки, документы, роли, CSRF и полный цикл.
- `tests/test_domain.py`: денежные расчёты, аннуитетный график и переходы статусов.
- `tests/test_audit.py`, `tests/test_hardening.py`: чужие объекты, устаревшие формы, подмена роли, лимиты и повторная отправка.
- `tests/test_scorecard.py`, `tests/test_scorecard_workflow.py`: контракт JSON, обе финансовые ветки, стоп-факторы, лимит, решение и принятие условий.
- `tests/test_security_inputs.py`: дублирующиеся ключи, неоднозначные алиасы, дробные копейки, повреждённый JSON, ошибочные типы, XSS, SQL-строки и актуальность проверок.
- `tests/test_configuration.py`: обратная совместимость настроек без отключения HTTPS.
- `tests/frontend.test.cjs`: устаревшие ответы калькулятора, потеря сети, неверные поля и переключение решения.
- `tests/live_http.py`: реальный HTTP, сессии и CSRF, изоляция клиентов, конкурирующие POST, документы и статические ресурсы.

- `tests/test_automatic_decisions.py`: полная модель при обычной отправке, серая зона, актуальность источников и история.
- `tests/test_mlcard_reference.py`: побайтовая сохранность оригинала в `docs/reference/mlcard`, совпадение результатов обеих веток и границы PD.
