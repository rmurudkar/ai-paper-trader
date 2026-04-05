# .claude/hooks/post-edit.sh
#!/bin/bash

# Check if there are unstaged changes
if git diff-index --quiet HEAD --; then
    exit 0  # No changes
fi

# Stage all changes
git add -A

# Use GitHub Copilot CLI to generate commit message
if command -v gh &> /dev/null; then
    # Generate message using GitHub CLI
    COMMIT_MSG=$(gh copilot suggest "Generate a concise git commit message for the following changes:" --shell=false)
    git commit -m "$COMMIT_MSG"
    git push
fi
