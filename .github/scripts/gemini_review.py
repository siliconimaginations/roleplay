#!/usr/bin/env python3
"""
Gemini AI code reviewer for pull requests.

Called by .github/workflows/gemini-review.yml on pull_request events.
Fetches the diff for reviewable files only, sends to Gemini,
and posts the result as a PR comment (replacing any previous Gemini review).

Environment variables (injected by the workflow):
  GITHUB_TOKEN       — for reading diff and posting comments
  GEMINI_API_KEY     — Google AI Studio key (free tier supported)
  GITHUB_REPOSITORY  — owner/repo
  PR_NUMBER          — pull request number
  BASE_SHA           — base commit SHA of the PR
  HEAD_SHA           — head commit SHA of the PR
"""

import os
import re
import subprocess
import sys
import time

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from github import Github

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Models tried in order. On ResourceExhausted (quota) OR any API error,
# the outer loop falls through to the next candidate.
# Order: lite/budget first (higher free-tier RPD), flagship last (better quality).
MODELS = [
    "gemini-2.5-flash-lite",  # fastest, most budget-friendly; highest free-tier RPD in 2.5 family
    "gemini-3.1-flash-lite",  # newest stable lite model
    "gemini-3.5-flash",       # best quality, stable; lower free-tier RPD
    "gemini-2.5-flash",       # proven baseline; 20 RPD on free tier
]

# Hard cap on diff characters sent to Gemini.
MAX_DIFF_CHARS = 60_000

# Retry settings for 429 / ResourceExhausted errors.
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 15  # doubles each retry: 15, 30, 60, 120

# File extensions sent to Gemini for review.
REVIEW_EXTENSIONS = {
    ".py",    # Python source
    ".toml",  # pyproject.toml, config files
    ".cfg",   # setup.cfg if present
    ".ini",   # config files
    ".yml",   # GitHub Actions workflows, compose files
    ".yaml",  # YAML variant
    ".sh",    # Shell scripts
    ".md",    # Documentation
    ".json",  # JSON config (not lockfiles — see SKIP_FILES)
    ".txt",   # requirements.txt etc.
}

# Exact filenames to skip even when their extension is in REVIEW_EXTENSIONS.
SKIP_FILES = {
    "uv.lock",           # lockfile
    "poetry.lock",       # lockfile
    "requirements.txt",  # skip if auto-generated; review hand-edited ones only
}

REVIEW_HEADER = "## Gemini Code Review 🤖"

_DIFF_HEADER_RE = re.compile(r"^diff --git a/.+ b/(.+)$")

PROMPT_TEMPLATE = """\
You are an expert code reviewer for a Python project called Roleplay —
a multi-party interaction simulator. The simulator uses LLM agents (Gemini, Claude, etc.)
to drive conversations, negotiations, and social dynamics between configurable parties
(people, organizations, environments). Key design concerns: agent robustness (rate-limit
fallback, model switching), memory management (retrieval, compaction, forgetting),
episode/time abstraction, and a clean developer API.

Review the following git diff and provide concise, actionable feedback.

Focus on:
- Bugs or logic errors
- Security issues (especially around API key handling, prompt injection, data persistence)
- Performance problems (especially in the simulation loop and memory retrieval paths)
- Code quality, naming, and maintainability
- Python idioms (type hints, dataclasses, async/await, generics)
- Mypy strict-mode compliance and ruff lint issues
- API design quality (clarity, extensibility, ergonomics for downstream developers)

For documentation files (.md):
- Technical accuracy — does the doc match the actual code/API?
- Completeness — are important edge cases or decisions missing?
- Consistency — does it align with ENGINEERING_PRINCIPLES.md and other design docs?
- Clarity — are examples concrete and types shown correctly?

Format your response exactly as follows:

## Gemini Code Review 🤖

### Summary
[1–2 sentences]

### Issues
[Each issue: `file.py:line — 🔴 Critical / 🟠 Major / 🟡 Minor — description and fix`]
[Write "None found." if there are no issues]

### Suggestions
[Optional improvements that are not bugs. Write "None." if nothing to add]

### Looks good ✅
[What is done well]

Be concise. No padding or filler.

---
{diff}
"""


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def _should_review(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    basename = os.path.basename(filename)
    return ext in REVIEW_EXTENSIONS and basename not in SKIP_FILES


def get_filtered_diff(base_sha: str, head_sha: str) -> tuple[str, bool]:
    """
    Two-step approach:
    1. Get names of changed files (cheap).
    2. Fetch the full diff for reviewable files only.
    Returns (diff, truncated).
    """
    names_result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"],
        capture_output=True,
        text=True,
        check=True,
    )
    all_files = [f.strip() for f in names_result.stdout.strip().splitlines() if f.strip()]
    reviewable = [f for f in all_files if _should_review(f)]

    if not reviewable:
        return "", False

    diff_result = subprocess.run(
        ["git", "diff", f"{base_sha}...{head_sha}", "--"] + reviewable,
        capture_output=True,
        text=True,
        check=True,
    )
    diff = diff_result.stdout

    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS], True
    return diff, False


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


def call_gemini(model_name: str, diff: str, truncated: bool) -> str:
    """Call one specific model. Raises on failure so the caller can try the next."""
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(model_name)

    notice = (
        f"\n\n> ⚠️ Diff was truncated to {MAX_DIFF_CHARS:,} chars. "
        "Some files may not have been reviewed.\n"
        if truncated
        else ""
    )

    prompt = PROMPT_TEMPLATE.format(diff=diff) + notice

    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = model.generate_content(prompt)
            return response.text
        except ResourceExhausted as exc:
            if attempt < MAX_RETRIES:
                print(
                    f"[{model_name}] Rate limit hit (attempt {attempt}/{MAX_RETRIES}). "
                    f"Retrying in {backoff}s…",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            raise


# ---------------------------------------------------------------------------
# GitHub comment
# ---------------------------------------------------------------------------


def post_or_update_comment(review: str) -> None:
    gh = Github(os.environ["GITHUB_TOKEN"])
    repo = gh.get_repo(os.environ["GITHUB_REPOSITORY"])
    pr = repo.get_pull(int(os.environ["PR_NUMBER"]))

    for comment in pr.get_issue_comments():
        if comment.body.startswith(REVIEW_HEADER):
            comment.delete()

    pr.create_issue_comment(review)
    print(f"Review posted on PR #{os.environ['PR_NUMBER']}.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]

    print(f"Diffing {base_sha[:8]}...{head_sha[:8]}")
    diff, truncated = get_filtered_diff(base_sha, head_sha)

    if not diff.strip():
        print("No reviewable changes in this diff.")
        return

    print(f"Sending {len(diff):,} chars to Gemini (trying {len(MODELS)} model(s))…")

    used_model: str | None = None
    for model_name in MODELS:
        print(f"Trying {model_name}…", file=sys.stderr)
        try:
            review = call_gemini(model_name, diff, truncated)
            used_model = model_name
            break
        except ResourceExhausted:
            print(
                f"[{model_name}] Quota exhausted after all retries — trying next model.",
                file=sys.stderr,
            )
            continue
        except Exception as exc:
            print(
                f"[{model_name}] API error ({exc.__class__.__name__}: {exc}) — trying next model.",
                file=sys.stderr,
            )
            continue

    if used_model is None:
        gh = Github(os.environ["GITHUB_TOKEN"])
        repo = gh.get_repo(os.environ["GITHUB_REPOSITORY"])
        pr = repo.get_pull(int(os.environ["PR_NUMBER"]))
        pr.create_issue_comment(
            f"{REVIEW_HEADER}\n\n"
            "> ⏸️ Gemini review skipped — free-tier quota exhausted on all models "
            f"({', '.join(MODELS)}). "
            "No action required; this does not block merge."
        )
        print("All models quota-exhausted — soft notice posted, job exits 0.")
        return

    review_with_attribution = review.rstrip() + f"\n\n---\n*Review by `{used_model}`*"
    post_or_update_comment(review_with_attribution)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
