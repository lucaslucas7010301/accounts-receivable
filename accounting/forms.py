from django import forms

from .models import CollectionAction


class CollectionActionForm(forms.ModelForm):
    """督促アクションの記録フォーム（S3 顧問先詳細から使用）"""
    class Meta:
        model = CollectionAction
        fields = ['method', 'status', 'content', 'result', 'next_action_date']
        widgets = {
            'content':          forms.Textarea(attrs={'rows': 2}),
            'result':           forms.Textarea(attrs={'rows': 2}),
            'next_action_date': forms.DateInput(attrs={'type': 'date'}),
        }
