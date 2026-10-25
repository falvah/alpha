from django.db import migrations

REPLACEMENTS = {'Подтвердите условия для демовыдачи.': 'Подтвердите условия предложения.', 'Создана заявка из синтетических данных': 'Заявка создана', 'Демонстрационный документ.pdf': 'Выписка.pdf', 'Проверка синтетического примера.': 'Проверка исходных данных.', 'Средства выданы · симуляция': 'Условия приняты', 'Условия приняты. Тестовое зачисление выполнено; реальные деньги не переводились.': 'Условия приняты и зафиксированы в кабинете. Платёжная интеграция не подключена; реальные деньги не переводились.'}

def update_copy(apps, schema_editor):
    application = apps.get_model("credit", "Application")
    for record in application.objects.using(schema_editor.connection.alias).filter(scoring__card_version="demo-1.0.0").iterator():
        record.scoring["card_version"] = "project-1.0.0"
        record.save(update_fields=["scoring"], using=schema_editor.connection.alias)
    for model_name, fields in [("Application", ["decision_note"]), ("Event", ["title", "note"]), ("Notification", ["title", "text"]), ("Document", ["name"])]:
        model = apps.get_model("credit", model_name)
        for record in model.objects.using(schema_editor.connection.alias).all().iterator():
            changed = []
            for field in fields:
                old = getattr(record, field)
                new = old
                for source, target in REPLACEMENTS.items():
                    new = new.replace(source, target)
                if new != old:
                    setattr(record, field, new)
                    changed.append(field)
            if changed:
                record.save(update_fields=changed, using=schema_editor.connection.alias)

class Migration(migrations.Migration):
    dependencies = [("credit", "0003_application_scoring_input")]
    operations = [migrations.RunPython(update_copy, migrations.RunPython.noop)]
