from django.core.management.base import BaseCommand

from abtest.models import Visit


class Command(BaseCommand):
    help = "Delete all A/B test visits, such as our own test visits before the graded run."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="really delete them")

    def handle(self, *args, yes=False, **options):
        count = Visit.objects.count()
        if not yes:
            self.stdout.write(f"Would delete {count} visits. Run again with --yes to delete them.")
            return
        Visit.objects.all().delete()
        self.stdout.write(f"Deleted {count} visits.")
