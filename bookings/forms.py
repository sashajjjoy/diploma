from django import forms
from .models import Booking

class ReservationForm(forms.ModelForm):
    DURATION_CHOICES = [
        (25, '25 минут'),
        (55, '55 минут'),
    ]
    
    duration = forms.ChoiceField(
        choices=DURATION_CHOICES,
        label='Длительность бронирования',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    class Meta:
        model = Booking
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
                if duration_minutes in [25, 55]:
                    self.initial['duration'] = duration_minutes
                else:
                    self.initial['duration'] = 25  # По умолчанию
        else:
            self.initial['duration'] = 25  # По умолчанию 25 минут
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        duration = cleaned_data.get('duration')
        
        if start_time and duration:
            from datetime import timedelta
            from django.utils import timezone
            cleaned_data['end_time'] = start_time + timedelta(minutes=int(duration))
        
        return cleaned_data
