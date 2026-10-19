# Contributing

## Branches

- `main` is what the README and the live site describe.
- `dev` is the integration branch, if the owner has created it (see below). Feature branches
  target `dev`, and `dev` is merged to `main` once it is reviewed.
- Use one branch per change: `fix/...`, `feat/...`, `docs/...`, `test/...`.

To create `dev` once (owner only):

```bash
gh api repos/keniel13-ui/ask-the-record/git/refs -f ref=refs/heads/dev \
  -f sha=$(gh api repos/keniel13-ui/ask-the-record/commits/main -q .sha)
```

Until `dev` exists, open PRs against `main`.

## Workflow

```bash
git fetch upstream
git switch -c fix/<topic> upstream/dev   # or upstream/main if dev does not exist yet
# edit, then validate (commands in docs/DEVELOPMENT.md)
git push -u origin fix/<topic>
gh pr create --repo keniel13-ui/ask-the-record --base dev
```

## What a PR must not do

- Edit anything under `evidence/`, or edit `harness/CONTRACT.md`.
- Change a README claim about a graded run without a new graded run behind it.
- Add a runtime dependency to the Python code (it is standard library only).
- Call the live model or the live site from tests, or commit a credential. `.env` and `.env.*`
  are gitignored.

## What a PR should include

- For a fix: the failure mode it prevents, and a test that fails without the fix.
- The same change in `harness/ask.py` and `web/api-ask.py` when it touches shared logic.
- The exact validation commands you ran and their output.
