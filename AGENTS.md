# Working on Yale Shuttle

This guide is for everyone on the team and for our AI coding agents. Agents:
read all of it before you change anything, and follow it.

Yale Shuttle is a Django app for Yale shuttle riders: arrival predictions,
crowding, service alerts, and an AI that tells you when to leave. It's our
MGT 656 team project. The course's rules are in
[project-requirements](https://github.com/yale-mgt-656-fall-2026/project-requirements),
and issue #51 tracks every requirement.

## Starting an issue

1. On the issue's GitHub page, click **Create a branch** under "Development",
   so the branch and its pull request link to the issue. Move the card to
   In Progress on the [board](https://github.com/users/vchen017/projects/1).
2. Tell your agent: *"Read AGENTS.md, then do issue #N in
   vchen017/project-yale-shuttle. Meet every acceptance criterion, follow its
   'How it connects' notes, add tests that mock outside services, and open a
   pull request using the template."*
3. Read what it wrote before you open the pull request. It's your work, and the
   description asks what you changed or rejected.

## Run it

Python 3.12, with [uv](https://docs.astral.sh/uv/):

```sh
uv venv && uv pip install -r requirements.txt
export DJANGO_DEBUG=1   # local only: SQLite and a throwaway secret key
.venv/bin/python manage.py migrate
SEED_ADMIN_PASSWORD=pick-one SEED_RIDER_PASSWORD=pick-another .venv/bin/python manage.py seed
.venv/bin/python manage.py runserver
```

Tests: `DJANGO_DEBUG=1 .venv/bin/python manage.py test`. CI runs them against
PostgreSQL on every pull request and checks that the migrations match the
models. GitHub won't merge until both pass.

Your laptop runs the app on SQLite, but CI and the servers use PostgreSQL. When
tests pass locally and fail in CI, that difference is the usual cause, so avoid
database-specific tricks.

## Where code goes

| Folder | What's in it |
|---|---|
| `config/` | Settings, all read from environment variables, and the main URL list |
| `accounts/` | The user model (`accounts.User`), sign-up and log-in |
| `abtest/` | The graded A/B test page and its stats. Leave it alone unless an issue says otherwise |
| `core/` | The home page, the error pages and the `seed` command |
| `templates/` | All HTML. Every page extends `base.html` |
| `core/static/core/app.css` | Our Yale-blue styles, on top of Bootstrap 5 |
| `docs/mgt656/` | Course docs: `team.yaml`, sprint reports, `technical.md` |

Each new feature area gets its own Django app, so we don't edit the same files
or create clashing migrations:

| App | Owner | What goes in it |
|---|---|---|
| `transit` | Vivian, then Ishita | The live Downtowner feed (#36) and the next-buses page (#24) |
| `history` | Ayush | The imported shuttle history (#22) and our predictions (#23) |
| `alerts` | Ishita | Service alerts (#13, #20) |
| `assistant` | Austin | The AI feature (#37, #28) |

Later areas, such as trips and crowding, get new apps the same way. Only change
another app's code or migrations when your issue says to, and tell its owner.

## Rules

- **Check facts instead of guessing.** Look up names, IDs, URLs and course
  rules at their source. If you have to assume something, say so in the pull
  request. Review data files like `docs/mgt656/team.yaml` as closely as code:
  in Sprint 1 an agent matched names to GitHub accounts by list order and
  swapped two.
- **Time zones.** The site shows Eastern time (`TIME_ZONE` is
  `America/New_York`, with `USE_TZ` on), but the Downtowner feed and the
  recorded history are in UTC. Use `django.utils.timezone.now()`, never
  `datetime.now()`, and keep every datetime time-zone aware: stored in UTC,
  shown in Eastern.
- **Settings and secrets come from environment variables**, read in
  `config/settings.py`. Keys, passwords and connection strings never go in the
  repo, tests or fixtures.
- **Tests never reach the outside world.** Mock the Downtowner feed, OpenRouter,
  push services and anything else on the network. The course checks this.
- **The LLM is called from the server only**, through OpenRouter, with the key
  in `OPENROUTER_API_KEY`. Never from browser JavaScript.
- **Nothing lasting on local disk.** Data goes in the database; the servers'
  disks are wiped on every deploy.
- **Schema changes are migrations**, committed with the code that needs them.
- **New packages** go in `requirements.txt` with an exact version (`name==1.2.3`).
- **Pages** extend `templates/base.html`, use Bootstrap 5 and our Yale-blue
  styles, and work on a phone and a desktop. Reuse what's there so new pages
  match: forms use `BootstrapFormMixin` from `accounts/forms.py` and the
  `templates/registration/_fields.html` partial, which shows errors properly,
  and icons come from Bootstrap Icons (`<i class="bi bi-...">`), already loaded
  in `base.html`.
- **Users:** point to them with `settings.AUTH_USER_MODEL`. Admin-only pages
  check `is_staff`.
- **Demo data:** if graders need data to see your feature, add it to
  `core/management/commands/seed.py`, safe to run twice. Never add A/B test
  visits there: the graded A/B run counts every visit.
- **Don't break what graders use.** The seed command manages the two grader
  logins (`admin@example.com` and `rider@example.com`), so leave them alone.
  Never change A/B test data, except deleting our own visits right before the
  graded run (#47). Production is updated only through the promote step (#34),
  and nobody changes it during the A/B run.
- **Small pull requests:** one issue each.

## Done means

- Every acceptance criterion in the issue is met.
- Tests cover it, with outside services mocked, and they pass.
- Any migrations are included.
- It works in a phone-sized and a desktop-sized window.
- The pull request uses the template: `Closes #N`, what the AI produced, what
  you changed or rejected, and how you tested it.
- A teammate approved it. GitHub asks all of us automatically, and one approval
  is enough.
- After merging, it works on staging, https://yale-shuttle-staging.onrender.com,
  which redeploys from `main` within about a minute.
- The team update (below) is posted in our team chat.

## When you finish

Agents: after opening the pull request, end with a short message the person can
paste into our team WhatsApp, in a code block. Write it in their voice, casual
and lowercase, in at most five lines:

- what's done, with the pull request link
- who it unblocks, or what comes next
- anything a reviewer should look at
- "working with the AI": one thing that worked or didn't, and what you changed,
  if there was one. The sprint report's "working with your agents" section
  needs these

For example:

```
opened the PR for the live shuttle feed: https://github.com/vchen017/project-yale-shuttle/pull/NN
it caches for 30 seconds and says when the feed is down instead of crashing
ishita, once it's merged you can start the next-buses page. can someone review?
working with the AI: its first tests called the real feed, so i had it mock the feed instead
```

## Deadlines and sprint reports

- Every deadline is 11:59 PM ET, and graders check `main` and staging right
  after. Don't merge code in the last few hours before a deadline, and check
  staging after every merge.
- Each sprint report needs one concrete thing that worked or didn't with our AI
  agents, and what we changed. The "working with the AI" lines in our team
  updates are where whoever writes the report finds them.

## Data sources

- **The live Downtowner feed** at https://yale.downtownerapp.com:
  `routes_routes.php?inactive=true`, `routes_stops.php`, `routes_buses.php`,
  `routes_eta.php?stop=<id>` and `routes_announcements.php`. Go through the
  `transit` helper (#36) rather than calling it directly.
- **The recorded history:** a snapshot every 5 minutes since Oct 26, 2026, in
  [downtowner-recorder](https://github.com/vchen017/downtowner-recorder), under
  `data/<UTC date>/`. Its README describes each CSV's columns, and
  `data/gaps.csv` lists the polls that are missing.
- **Feed quirks we've found** in the recorded data:
  - Route short names aren't unique: "BW" is both Blue – Weekend Daytime
    (route 4) and Blue West (route 16). Always identify routes by their
    numeric `id`.
  - A bus's position can be minutes old. Compare its `lastUpdate` with the
    current time before trusting it (#58).
  - The official routes aren't always right. The evening Blue also drives the
    daytime Blue's northern loop, and moved stops appear only in notices, not in
    the stop list (both in #54).
  - Routes and stops change during the term, for example on Nov 13 (#57).
