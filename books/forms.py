from django import forms
from .models import Thread, Comment

class ThreadForm(forms.ModelForm):
    reading_date = forms.DateField(
        label='독서일',
        required=True,
        widget=forms.DateInput(attrs={
            'type': 'date'
        })
    )
    class Meta:
        model = Thread
        exclude = ["cover_img", "likes", "user", "book", "created_at", "updated_at"]

class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        exclude = ('user', 'thread', "created_at", "updated_at")
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'maxlength': 100,
                'placeholder': '책을 읽으며 나눈 생각을 적어 주세요.',
            }),
        }
