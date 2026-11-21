from django.core.management.base import BaseCommand

from abtest.models import Visit


class Command(BaseCommand):
    help = "Delete all A/B test visits, such as our own test visits before the graded run."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="really delete them")

    def handle(self, *args, yes=False, **options):
        if not yes:
            count = Visit.objects.count()
            self.stdout.write(f"Would delete {count} visits. Run again with --yes to delete them.")
            return
        # delete()'s own count, so a visit that lands mid-reset is counted too.
        deleted, _ = Visit.objects.all().delete()
        self.stdout.write(f"Deleted {deleted} visits.")
