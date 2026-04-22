# Generated manually for takeout removal and WeeklyMenu -> WeeklyMenuDaySettings

import django.db.models.deletion
from django.db import migrations, models


def copy_weekly_menu_to_day_settings(apps, schema_editor):
    WeeklyMenu = apps.get_model('bookings', 'WeeklyMenu')
    WeeklyMenuDaySettings = apps.get_model('bookings', 'WeeklyMenuDaySettings')
    WeeklyMenuItem = apps.get_model('bookings', 'WeeklyMenuItem')
    mapping = {}
    for wm in WeeklyMenu.objects.all():
        s = WeeklyMenuDaySettings.objects.create(
            day_of_week=wm.day_of_week,
            is_active=wm.is_active,
        )
        mapping[wm.pk] = s.pk
    for item in WeeklyMenuItem.objects.all():
        if item.menu_id in mapping:
            item.day_settings_id = mapping[item.menu_id]
            item.save(update_fields=['day_settings_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0010_menuoverride_weeklymenu_weeklymenuitem_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='TakeoutOrderItem',
        ),
        migrations.DeleteModel(
            name='TakeoutOrder',
        ),
        migrations.CreateModel(
            name='WeeklyMenuDaySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day_of_week', models.IntegerField(choices=[(0, 'Понедельник'), (1, 'Вторник'), (2, 'Среда'), (3, 'Четверг'), (4, 'Пятница'), (5, 'Суббота'), (6, 'Воскресенье')], unique=True, verbose_name='День недели')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
            ],
            options={
                'verbose_name': 'Настройки меню на день недели',
                'verbose_name_plural': 'Настройки меню по дням недели',
                'ordering': ['day_of_week'],
            },
        ),
        migrations.AddField(
            model_name='weeklymenuitem',
            name='day_settings',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='items',
                to='bookings.weeklymenudaysettings',
                verbose_name='День недели (настройки)',
            ),
        ),
        migrations.RunPython(copy_weekly_menu_to_day_settings, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='weeklymenuitem',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='weeklymenuitem',
            name='menu',
        ),
        migrations.AlterField(
            model_name='weeklymenuitem',
            name='day_settings',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='items',
                to='bookings.weeklymenudaysettings',
                verbose_name='День недели (настройки)',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='weeklymenuitem',
            unique_together={('day_settings', 'dish')},
        ),
        migrations.DeleteModel(
            name='WeeklyMenu',
        ),
    ]
