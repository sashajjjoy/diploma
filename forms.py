from django import forms
from .models import Reservation

class ReservationForm(forms.ModelForm):
    DURATION_CHOICES = [
        (15, '15 минут'),
        (30, '30 минут'),
        (45, '45 минут'),
    ]
    
    duration = forms.ChoiceField(
        choices=DURATION_CHOICES,
        label='Длительность бронирования',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    class Meta:
        model = Reservation
        fields = ['table', 'guests_count', 'start_time']
        widgets = {
            'table': forms.Select(attrs={'class': 'form-control'}),
            'guests_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'start_time': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
        }
        labels = {
            'table': 'Столик',
            'guests_count': 'Количество персон',
            'start_time': 'Дата и время начала',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # При редактировании вычисляем длительность из существующего бронирования
            if self.instance.start_time and self.instance.end_time:
                delta = self.instance.end_time - self.instance.start_time
                duration_minutes = int(delta.total_seconds() / 60)
                if duration_minutes in [15, 30, 45]:
                    self.initial['duration'] = duration_minutes
                else:
                    self.initial['duration'] = 30  # По умолчанию
        else:
            self.initial['duration'] = 30  # По умолчанию 30 минут
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        duration = cleaned_data.get('duration')
        
        if start_time and duration:
            from datetime import timedelta
            from django.utils import timezone
            cleaned_data['end_time'] = start_time + timedelta(minutes=int(duration))
        
        return cleaned_data

