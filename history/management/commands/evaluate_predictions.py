"""Report our prediction error next to the official ETA's, on held-out days (#23).

Trains on all history except every Nth day, scores both predictors at
sampled polls on those held-out days, and prints the average error of each:

    python manage.py evaluate_predictions
    python manage.py evaluate_predictions --holdout-modulus 4 --stride 4
"""

from django.core.management.base import BaseCommand, CommandError

from history import predictions


class Command(BaseCommand):
    help = (
        "Evaluate our arrival predictions on days held out from training, "
        "and print the average error next to the official ETA's."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--holdout-modulus",
            type=int,
            default=3,
            help="Hold out every Nth day of history (default 3).",
        )
        parser.add_argument(
            "--stride",
            type=int,
            default=2,
            help="Score only every Nth poll of each held-out day (default 2).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=predictions.HISTORY_DAYS,
            help="How many days of history to learn from (default 28).",
        )

    def handle(self, *args, **options):
        modulus, stride, days = (
            options["holdout_modulus"],
            options["stride"],
            options["days"],
        )
        if modulus < 2:
            raise CommandError("--holdout-modulus must be at least 2.")
        if stride < 1:
            raise CommandError("--stride must be at least 1.")
        if days < 2:
            raise CommandError("--days must be at least 2.")

        result = predictions.evaluate(
            holdout_modulus=modulus, stride=stride, days=days
        )
        if not result["queries"]:
            raise CommandError(
                "Nothing to score yet: run import_history first, so there is "
                "recorded history to learn from and to be scored on."
            )

        held_out = ", ".join(day.isoformat() for day in result["test_days"])
        self.stdout.write(
            f"Held out {len(result['test_days'])} of {result['history_days']} days "
            f"(1 in every {modulus}): {held_out}"
        )
        self.stdout.write(
            f"Scored {result['queries']:,} route-stop-poll queries "
            f"({result['no_prediction']:,} had no prediction of ours)."
        )
        if result["not_served"]:
            self.stdout.write(
                f"Skipped {result['not_served']:,} queries at stops the route "
                f"no longer serves (#57), so the score matches what riders "
                f"would be told."
            )
        self.stdout.write("")
        self.stdout.write(f"  our prediction:  average error {result['ours_mae']} min")
        self.stdout.write(f"  official ETA:    average error {result['official_mae']} min")
