---
name: git-pr-workflow
description: Automates a structured Git workflow (Local Test -> Verify -> Branch -> Diff -> Confirm -> Commit -> PR). Use whenever the user wants to push changes to GitHub.
---

# Git PR Workflow

This skill ensures a high-quality, verified, and user-confirmed Git workflow for the IntruderWatch project.

## The Golden Rule
**NEVER** create a branch, commit, or push until you have performed a local verification and received explicit approval for each step from the user.

## Workflow Steps

### 1. Local Deployment & Verification (MANDATORY)
Before proposing any Git actions, you must ensure the code works:
- **Deploy:** Run `docker compose up -d --build <affected_services>`.
- **Logs:** Run `docker logs <service> --tail 50` and check for errors or successful "Heartbeat" messages.
- **Metrics:** If metrics were changed, verify they are exposed correctly via `curl`.
- **Report:** Show the user a brief summary of the verification results (e.g., "Logs are clean," "Metrics verified").

### 2. Branch Proposal
- Suggest a branch name based on [conventions.md](references/conventions.md).
- **Stop and ask:** "Would you like me to create the branch `<suggested-branch-name>`?"

### 3. Commit Review (MANDATORY)
Once the branch is created:
- Show the user the full `git diff` of the changes.
- Propose a specific commit message in Conventional Commit style.
- **Stop and ask:** "Are you okay with these changes and the commit message? Ready to commit?"

### 4. Pull Request Review
Once committed and pushed:
- Propose a PR title and a detailed body/description.
- Mention the verification steps you performed in Step 1.
- **Stop and ask:** "Should I open the Pull Request with this description?"

### 5. Execution & Cleanup
- Create the PR using `gh pr create`.
- Offer to merge and delete the branch if the user has administrative privileges.
- Return to `main` and pull the latest changes.
