from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("credit", "0002_application_creation_token_company_version_and_more")]
    operations = [migrations.AddField(model_name="application", name="scoring_input", field=models.JSONField(default=dict, blank=True))]
