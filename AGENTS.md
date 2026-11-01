# Instructions for coding agents

Read `CONTEXT.md` first, every session. It holds current state, who is working on what, and the
handoff log between the two people on this project.

When you finish a meaningful change or end a session, update `CONTEXT.md` (current state, in progress,
next up, and a new log entry at the top of the Log) and push it directly on `main` (see
`CONTEXT.md` rule 8); code goes on a task branch and a PR.

Also read `docs/DECISIONS.md` before changing architecture. Do not reverse a decision there without
adding a new entry that explains why.

House rules:
- Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy, Alembic. MCP Python SDK pinned `<2`.
- Zero spend: free tiers and hackathon credits only. Never add paid services.
- Secrets only in `.env` (git-ignored). New env var → add its name to `.env.example` + log it.
- Fail safe: any check error → BLOCK or STEP_UP, never ALLOW.
- Small commits with clear messages. Write tests with the code.

---

# Coding agent protocol

## Hard limits (never break these)
- Do NOT push, open/close PRs or issues, comment on GitHub, or change any remote. Do NOT add, rename or delete remotes.
- Stop after ONE local commit (or the number of commits I specify) and report back.
- Stage files by explicit path only. Never use `git add -A` or `git add .`
- Leave untracked files you didn't create untouched.
- Do NOT build Docker images, download large toolchains, or install global packages without asking. My disk space is limited.
- If something blocks you (missing remote, unclear requirement, failing setup), stop and tell me. Don't work around it.

## Before writing code
- Read the repo's contributor docs first (AGENTS.md, CLAUDE.md, CONTRIBUTING, PR template, any agent skills) and follow them over these rules where they conflict.
- Check for existing issues and PRs covering the same change. Stop if one exists.
- Branch fresh from the upstream default branch, not from another feature branch.
- Confirm the bug still exists on upstream before changing anything.
- Read the surrounding code and any earlier fixes in the same area (`git log` / `git blame`) and follow their pattern.

## Code
- Write like a professional already working in this codebase: match its style, naming, structure and test patterns exactly.
- Smallest possible diff that fully fixes the problem. No refactors, reformatting, renames, new helpers or unrelated changes.
- Comments only for genuinely non-obvious constraints. No narrating comments.
- No emojis anywhere: code, commits, PR text.
- Follow the project's spelling and terminology conventions.
- Before finalizing, reason through every call site and edge case the change touches, and list that reasoning in the report.

## Tests
- Add tests to the existing test module for that code, in its style. Don't create new test files unless the project's layout requires it.
- Every new test case must fail without the fix and pass with it. Prove both: show the failing run with the fix reverted and the passing run with it.
- Cover exactly what the change affects. Don't add cases that pass with or without the fix.
- Run the whole affected test module afterwards. If unrelated tests fail, check whether they also fail without your change (for example, stale local DB state) and report it.
- Run the project's lint/static checks on the changed files. If a hook needs heavy infrastructure, skip only that hook and say so.

## Commit
- Plain imperative subject, under 70 characters, describing the user-facing impact. No "fix:"/"feat:" prefixes, no issue numbers in the subject.
- Body: 1-3 lines explaining WHY.
- NO Co-Authored-By, "Generated with", or any AI attribution trailer. Verify with `git log -1 --format=%B`.
- Use my existing git identity. If none is set, stop and ask.

## PR description (draft only, never post)
- Write it to a gitignored file in the repo (or the scratch location I name), body only, no title line.
- Follow the project's PR template.
- Structure: closes/related line at the top, then Problem / Cause / Fix / Testing.
- Testing section: the new tests, that they fail without the fix and pass with it, the full module result, and any checks skipped and why.
- Include the project's required AI-disclosure section exactly as its guidelines ask.
- Concise, plain, factual. No filler, no hype, no AI-sounding phrasing. Don't tag or mention individuals.
- Suggest a PR title focused on user impact.

## Writing style (code, commits, PR text, replies)
- Short sentences, plain words, no buzzwords.
- State only what you verified. Label anything inferred or not run.

## Final report (then STOP)
1. Full diff against upstream
2. Edge-case / call-site reasoning
3. Failing test output (fix reverted) and passing output
4. Full module test result and lint/static-check output
5. `git log -1 --format=fuller` and `git status` (confirm nothing pushed, untracked files untouched)
6. The PR body file contents and the suggested title

## Leash adaptations (these override the protocol above where they conflict)
The protocol was written for bug fixes in upstream open-source repos. In this repo:
- "Existing issues and PRs" means `CONTEXT.md` claims and the other person's branch. Stop if the task is already claimed.
- Work on your own branch (`sahil` or `anurag`, or `<name>/<task>`). Merge `origin/main` into it before starting a task. "Upstream default branch" means `origin/main`.
- Bug fixes: the protocol applies as written (confirm the bug, smallest diff, tests fail without the fix and pass with it).
- New features: the smallest design that meets the task. New modules, helpers and test files are allowed. Tests cover the new behaviour and its edge cases; the fail-without-fix proof does not apply.
- One commit per `CONTEXT.md` task, then stop and report.
- PR descriptions are only needed when merging a branch into `main` (sync points: Nov 4, 6, 8).
- After each task, draft the `CONTEXT.md` update (your status section + a log entry). `CONTEXT.md` changes go to `main` only.
- AI disclosure for the hackathon is made once in the README and the Devpost form, not in commits.
