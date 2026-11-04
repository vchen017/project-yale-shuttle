# Sprint 1: walking skeleton

**Goal:** get the whole pipeline working end to end before building features:
a Django app with accounts on PostgreSQL, deployed to staging from `main`, with
the A/B test page, the grader accounts, and a backlog for the rest of the term.

## What shipped

- [#30](https://github.com/vchen017/project-yale-shuttle/pull/30), the walking
  skeleton: Django 5.2 with Django's built-in accounts, settings from
  environment variables, the A/B test page and its stats, the `seed` command,
  and the Render and CI setup. It closed
  [#1](https://github.com/vchen017/project-yale-shuttle/issues/1) (accounts),
  [#2](https://github.com/vchen017/project-yale-shuttle/issues/2) (A/B page and
  stats) and [#3](https://github.com/vchen017/project-yale-shuttle/issues/3)
  (grader accounts).
- Staging on Render with its own Neon PostgreSQL database
  ([#4](https://github.com/vchen017/project-yale-shuttle/issues/4)).
- [#31](https://github.com/vchen017/project-yale-shuttle/pull/31): Neon's agent
  skills and MCP setup for Claude Code.
- The backlog: the rest of our user stories
  ([#5](https://github.com/vchen017/project-yale-shuttle/issues/5) to
  [#21](https://github.com/vchen017/project-yale-shuttle/issues/21)), sub-issues
  for the bigger ones, and a [project board](https://github.com/users/vchen017/projects/1)
  with one iteration per sprint.

## What works on staging

https://yale-shuttle-staging.onrender.com, which deploys automatically from
`main` (merging #31 redeployed it within seconds):

- signing up, logging in and out, and `/admin/` for admins
- the grader accounts, which `seed` creates on every deploy
- `/ecc1032`: public, lists our four nicknames, gives each new visitor `kudos`
  or `thanks` at random and keeps showing it, and counts one impression and at
  most one click per visitor
- `/ecc1032/stats`: live counts and the `preferred` variant, from the database

We checked all of it with a script that uses staging like a browser: a new
visitor, a reload, two clicks, both grader logins and a wrong password.

## Working with our agents

What didn't work: we gave Claude Code our teammates' course nicknames and GitHub
usernames as two separate lists, and it paired them up in `team.yaml` by
position. Two of the pairs came out swapped. Our tests didn't catch it, because
the A/B page lists only nicknames; the agent flagged the pairing as a guess and
we corrected it. What we changed: we give the agent exact pairs instead of lists
to match up, and we review data files like `team.yaml` as closely as code.

What worked: before building the A/B page, the agent checked our team nickname
on the course site instead of copying it from our proposal. The proposal used
our team-selection secret key, australian-zoo, which would have put the page at
the wrong address.

## Backlog changes for Sprint 2

- Add a production environment story: Sprint 2 needs production, with its own
  database.
- Proposed commitment: [#5](https://github.com/vchen017/project-yale-shuttle/issues/5)
  (next buses with predicted arrival times) through its sub-issues
  [#22](https://github.com/vchen017/project-yale-shuttle/issues/22) (import the
  recorded shuttle history), [#23](https://github.com/vchen017/project-yale-shuttle/issues/23)
  (predictions) and [#24](https://github.com/vchen017/project-yale-shuttle/issues/24)
  (the page), plus [#13](https://github.com/vchen017/project-yale-shuttle/issues/13)
  (service alerts on the home screen).
- Choose our technical-challenge items. CI on every pull request and automatic
  staging deploys are already in place.
