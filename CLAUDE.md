# CLAUDE.md

All project rules live in AGENTS.md so every tool reads the same instructions:

@AGENTS.md

Claude-specific notes:
- Start by running `git status`, `git log --oneline -5` and `gh issue view <n>` for the task.
- Reply to the human in English. Code, comments, commits and docs in English.
- Explain new concepts briefly (what and why) before the code — the team is learning.
- After a change run `uv run pytest`. For a manual check: `uv run dso --help`.
- Never read files in `data/` into the conversation unless the human asks; they may be confidential.
