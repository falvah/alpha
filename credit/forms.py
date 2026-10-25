import re
from decimal import Decimal
from django import forms
from .domain import PRODUCTS, validate_identity
from .models import Application, Company, Document

class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")

class FinancingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Application
        fields = ["product", "amount", "months", "purpose"]
        labels = {"product": "Кредитный продукт", "amount": "Сумма, ₽", "months": "Срок, месяцев", "purpose": "На что нужны деньги"}
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3, "placeholder": "Закупка товаров к новому сезону"}),
                   "amount": forms.NumberInput(attrs={"step": "0.01", "min": "100000"}), "months": forms.NumberInput(attrs={"min": 1, "max": 60})}
    def clean(self):
        data = super().clean()
        product = PRODUCTS.get(data.get("product"))
        if product and data.get("amount") is not None and not product["min"] <= data["amount"] <= product["max"]:
            self.add_error("amount", f"Для этого продукта: от {product['min']:,} до {product['max']:,} ₽.".replace(",", " "))
        if product and data.get("months") is not None and not 1 <= data["months"] <= product["months"]:
            self.add_error("months", f"Выберите срок от 1 до {product['months']} мес.")
        if data.get("purpose") and not re.fullmatch(r"[А-Яа-яЁё0-9\s.,:;!?()«»№/—–+%\-]+", data["purpose"]):
            self.add_error("purpose", "Используйте русский текст, цифры и знаки препинания.")
        return data

class CompanyForm(StyledFormMixin, forms.ModelForm):
    company_version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company_version"].initial = self.instance.version

    def save(self, commit=True):
        company = super().save(commit=False)
        company.version += 1
        if commit:
            company.save()
        return company

    class Meta:
        model = Company
        fields = ["kind", "name", "representative", "address", "inn", "ogrn", "business_months", "revenue", "expenses", "existing_payments"]
        labels = {"kind": "Форма бизнеса", "name": "Название без ООО / ИП", "representative": "Представитель, ФИО", "address": "Юридический адрес в России",
                  "inn": "ИНН", "ogrn": "ОГРН / ОГРНИП", "business_months": "Сколько месяцев работает бизнес", "revenue": "Средняя выручка в месяц, ₽", "expenses": "Средние расходы в месяц, ₽", "existing_payments": "Платежи по другим кредитам в месяц, ₽"}
        widgets = {"inn": forms.TextInput(attrs={"inputmode": "numeric"}), "ogrn": forms.TextInput(attrs={"inputmode": "numeric"})}
    def clean(self):
        data = super().clean()
        if self.instance.pk and data.get("company_version") != self.instance.version:
            self.add_error(None, "Данные компании изменились в другой вкладке. Обновите страницу и повторите изменения.")
        if all(data.get(k) for k in ["kind", "inn", "ogrn"]) and not validate_identity(data["kind"], data["inn"], data["ogrn"]):
            self.add_error("inn", "Для ИП: ИНН 12 цифр и ОГРНИП 15 цифр. Для остальных: ИНН 10 и ОГРН 13 цифр. Реквизиты не могут состоять из одной повторяющейся цифры.")
        for key, pattern, message in [
            ("name", r"[А-Яа-яЁё0-9\s«»\".\-]+", "Название: русские буквы, цифры, пробелы и кавычки."),
            ("representative", r"[А-Яа-яЁё\s\-]+", "Введите ФИО русскими буквами."),
        ]:
            meaningful = "[А-Яа-яЁё0-9]" if key == "name" else "[А-Яа-яЁё]"
            if data.get(key) and (not re.fullmatch(pattern, data[key]) or not re.search(meaningful, data[key])):
                self.add_error(key, message)
        if data.get("representative") and len(re.findall(r"[А-Яа-яЁё]+", data["representative"])) < 2:
            self.add_error("representative", "Укажите как минимум фамилию и имя.")
        if data.get("address") and (len(data["address"]) < 12 or len(re.findall("[А-Яа-яЁё]{2,}", data["address"])) < 2 or not re.search(r"[0-9]", data["address"])):
            self.add_error("address", "Укажите полный адрес в России: город, улица, дом.")
        for key in ["revenue", "expenses", "existing_payments"]:
            if data.get(key) is not None and not 0 <= data[key] <= Decimal("9999999999.99"):
                self.add_error(key, "Введите сумму от 0 до 9 999 999 999,99 ₽.")
        if data.get("business_months") is not None and not 1 <= data["business_months"] <= 1200:
            self.add_error("business_months", "Введите срок работы от 1 до 1200 месяцев.")
        return data

class UploadForm(StyledFormMixin, forms.Form):
    kind = forms.ChoiceField(label="Тип документа", choices=Document._meta.get_field("kind").choices)
    file = forms.FileField(label="PDF-документ", widget=forms.ClearableFileInput(attrs={"accept": ".pdf,application/pdf"}))

class ScorecardForm(StyledFormMixin, forms.Form):
    payload = forms.CharField(label="Данные заявки, проверок и документа (JSON)", max_length=65536, widget=forms.Textarea(attrs={"rows": 12}), help_text="Русские и английские ключи из формата скоринговой карты.")

    def clean_payload(self):
        from .scorecard import LoanRequest, InputDataError
        from .scorecard.payload import parse_payload
        try:
            payload = parse_payload(self.cleaned_data["payload"])
            LoanRequest.from_payload(payload)
        except InputDataError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return payload

class DecisionForm(StyledFormMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.data.get("decision") != "counteroffer":
            self.data = self.data.copy()
            for name in ["amount", "months", "rate"]:
                self.data.pop(name, None)

    decision = forms.ChoiceField(label="Решение", choices=[("approved", "Одобрить запрошенные условия"), ("counteroffer", "Предложить другие условия"), ("info", "Запросить уточнения"), ("rejected", "Отказать")])
    note = forms.CharField(label="Сообщение предпринимателю", min_length=10, max_length=2000, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Объясните решение и следующий шаг простыми словами"}))
    amount = forms.DecimalField(label="Предлагаемая сумма, ₽", required=False, min_value=100000, max_value=30000000, max_digits=12, decimal_places=2)
    months = forms.IntegerField(label="Предлагаемый срок, мес.", required=False, min_value=1, max_value=60)
    rate = forms.DecimalField(label="Предлагаемая ставка, %", required=False, min_value=1, max_value=60, decimal_places=2)
    def clean(self):
        data = super().clean()
        if data.get("decision") == "counteroffer":
            for field in ["amount", "months", "rate"]:
                if data.get(field) is None:
                    self.add_error(field, "Заполните условия встречного предложения.")
        return data
