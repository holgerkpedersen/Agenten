---
name: git_pr
keywords: git, github, pull request, commit, push, branch, PR, workflow
template: agenten
description: Automatiseret Git/GitHub PR workflow — opret branch, commit, push, opret pull request.
---
# PR Agenten — Git/GitHub Workflow

UDFOR DISSE TRIN I RÆKKEFOLGE:

1. **BRANCH**: Opret ny branch med git_create_branch(name='...')
2. **COMMIT**: Stage med git_add_all() og commit med git_commit(message='...')
3. **PUSH**: Push til remote med git_push(branch='...')
4. **PR**: Opret Pull Request med github_create_pr(owner='...', repo='...', title='...', branch='...')

**Checkpoint-validering:** Hvis et tidligere trin mangler, vil systemet afvise <<<DONE>>> og bede dig fuldfore de manglende trin forst.
