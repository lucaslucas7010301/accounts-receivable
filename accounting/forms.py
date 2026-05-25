from django import forms
from .models import Customer, Account, JournalEntry, Transaction


class SaleForm(forms.ModelForm):
    class Meta:
        model  = Transaction
        fields = ['date', 'customer', 'amount', 'description']
        widgets = {
            'date':        forms.DateInput(attrs={'type': 'date'}),
            'customer':    forms.Select(),
            'amount':      forms.NumberInput(attrs={'min': 1}),
            'description': forms.TextInput(attrs={'placeholder': '摘要（任意）'}),
        }
        labels = {
            'date':        '売上日付',
            'customer':    '得意先',
            'amount':      '金額（税込）',
            'description': '摘要',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = Customer.objects.filter(is_active=True)


class ReceiptForm(forms.ModelForm):
    class Meta:
        model  = Transaction
        fields = ['date', 'customer', 'amount', 'description']
        widgets = {
            'date':        forms.DateInput(attrs={'type': 'date'}),
            'customer':    forms.Select(),
            'amount':      forms.NumberInput(attrs={'min': 1}),
            'description': forms.TextInput(attrs={'placeholder': '摘要（任意）'}),
        }
        labels = {
            'date':        '入金日付',
            'customer':    '得意先',
            'amount':      '金額',
            'description': '摘要',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = Customer.objects.filter(is_active=True)


class JournalEntryForm(forms.Form):
    """手動仕訳：借方・貸方を1画面で入力する"""
    date            = forms.DateField(label='仕訳日付',  widget=forms.DateInput(attrs={'type': 'date'}))
    debit_account   = forms.ModelChoiceField(label='借方科目',  queryset=Account.objects.all())
    debit_customer  = forms.ModelChoiceField(label='借方得意先', queryset=Customer.objects.filter(is_active=True),
                                             required=False)
    credit_account  = forms.ModelChoiceField(label='貸方科目',  queryset=Account.objects.all())
    credit_customer = forms.ModelChoiceField(label='貸方得意先', queryset=Customer.objects.filter(is_active=True),
                                             required=False)
    amount          = forms.DecimalField(label='金額', max_digits=12, decimal_places=0,
                                         widget=forms.NumberInput(attrs={'min': 1}))
    description     = forms.CharField(label='摘要', max_length=200, required=False,
                                      widget=forms.TextInput(attrs={'placeholder': '摘要（任意）'}))

    def clean(self):
        cleaned = super().clean()
        d = cleaned.get('debit_account')
        c = cleaned.get('credit_account')
        if d and c and d == c:
            raise forms.ValidationError('借方と貸方に同じ科目は指定できません。')
        return cleaned


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(label='CSVファイル', help_text='文字コード: UTF-8')
