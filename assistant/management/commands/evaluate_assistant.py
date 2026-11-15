from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from assistant import evals, openrouter
from assistant.decide import decide_notification


class Command(BaseCommand):
    help = (
        "Run the notification evaluation set against the real model through OpenRouter. "
        "Needs OPENROUTER_API_KEY. CI runs the same cases with the LLM mocked."
    )

    def handle(self, *args, **options):
        if not settings.OPENROUTER_API_KEY:
            raise CommandError("Set OPENROUTER_API_KEY to run the evaluation against the real model.")
        self.stdout.write(f"Model: {settings.OPENROUTER_MODEL}")
        try:
            results = evals.run(lambda trip: decide_notification(trip, strict=True))
        except openrouter.OpenRouterError as error:
            # An outage isn't a "don't notify" answer, so don't score it as one.
            raise CommandError(f"Couldn't reach the model, so nothing was evaluated: {error.__cause__ or error}")
        for case_, decision, ok, why in results:
            self.stdout.write(f"{'PASS' if ok else 'FAIL'}  {case_['name']}")
            if not ok:
                self.stdout.write(f"      {why}\n      got: {decision}")
        passed = sum(ok for _, _, ok, _ in results)
        self.stdout.write(f"{passed}/{len(results)} passed")
        if passed < len(results):
            raise CommandError("Some evaluation cases failed.")
