from django.core.management.base import BaseCommand, CommandError

from bookings.seed_data import MIN_ROWS, run_reseed


class Command(BaseCommand):
    help = (
        "Удаляет все данные кроме Table и Dish, затем заполняет демо-данными "
        f"(не меньше {MIN_ROWS} записей в основных контентных таблицах)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Не спрашивать подтверждение",
        )

    def handle(self, *args, **options):
        if not options["no_input"]:
            confirm = input("Удалить все данные кроме столиков и блюд и заполнить демо? [y/N]: ")
            if confirm.lower() not in ("y", "yes", "д", "да"):
                self.stdout.write(self.style.WARNING("Отменено."))
                return
        try:
            stats = run_reseed()
        except RuntimeError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS("Готово. Счётчики:"))
        for name, count in sorted(stats.items()):
            self.stdout.write(f"  {name}: {count}")
