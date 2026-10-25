"""Refresh only attachments bearing the previous seed generator's exact markers."""
import hashlib
import re
from django.db import migrations


def refresh_generated_documents(apps, schema_editor):
    from credit.pdf import make_pdf

    documents = apps.get_model("credit", "Document")
    for doc in documents.objects.using(schema_editor.connection.alias).select_related("application").filter(name="Выписка.pdf").iterator():
        content = bytes(doc.content)
        app = doc.application
        if b"(SYNTHETIC SCORECARD INPUT " in content and app.company_snapshot.get("source") == "scorecard":
            payload = app.scoring_input
            data = payload.get("документ", payload.get("document", {}))
            lines = [f"Requested amount: RUB {app.amount}; term: {app.months} months."]
            if app.scoring.get("branch") == "mature":
                lines += [f"Months with inflows: {data.get('месяцев_с_поступлениями', data.get('months_with_inflows', ''))}",
                          f"Average monthly inflow: RUB {data.get('средний_приход', data.get('avg_monthly_inflow', ''))}"]
            else:
                lines += [f"Annual guarantor income: RUB {data.get('доход_поручителя', data.get('guarantor_income', 0))}",
                          f"Collateral value: RUB {data.get('стоимость_залога', data.get('collateral_value', 0))}"]
        elif re.search(rb"\(SYNTHETIC STATEMENT [1-4]\)", content):
            snapshot = app.company_snapshot
            lines = [f"Monthly revenue: RUB {snapshot.get('revenue', '')}",
                     f"Monthly expenses: RUB {snapshot.get('expenses', '')}",
                     f"Existing monthly payments: RUB {snapshot.get('existing_payments', '')}"]
        else:
            continue
        updated = make_pdf("FINANCIAL DATA", lines)
        doc.content, doc.size, doc.sha256 = updated, len(updated), hashlib.sha256(updated).hexdigest()
        doc.save(update_fields=["content", "size", "sha256"], using=schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [("credit", "0004_refresh_interface_copy")]
    operations = [migrations.RunPython(refresh_generated_documents, migrations.RunPython.noop)]
