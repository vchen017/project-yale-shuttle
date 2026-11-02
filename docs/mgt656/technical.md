# Technical notes

## Technical-challenge items

To be chosen in Sprint 2.

## Testing strategy

- Django `TestCase` tests for each app, run with `python manage.py test`. CI runs
  them against PostgreSQL on every pull request.
- The A/B test page is tested over HTTP the way a browser uses it: each visitor
  keeps their variant, one impression per visitor, at most one click, the
  CSRF-protected form, the 50/50 split, and `preferred` chosen by click rate.
- The seed command is tested for being safe to rerun and for refusing to run
  without passwords.
- No external services are called yet. When we add the LLM and the Downtowner
  feeds, tests will mock them.
