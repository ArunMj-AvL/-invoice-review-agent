# How we work

## Golden rules
1. Edit **only your own files** (see the table in README.md).
2. **Never push directly to `main`.** Work on your branch, then open a Pull Request.
3. **Never commit `.env` or API keys.**
4. Only `app/rules.py` decides. Agents explain or advise, never change a decision.

## Branches
| Person | Branch |
|---|---|
| Niranjan | `feat/rules` |
| Aamir | `feat/langchain-crewai` |
| Anit | `feat/autogen-tests` |
| Lead | `feat/graph` |

## Daily workflow
```bash
git checkout main
git pull                          # get the latest main
git checkout feat/your-branch
git merge main                    # bring main's updates into your branch
# ... do your work ...
git add <your files>
git commit -m "rules: add P2 quantity check"
git push -u origin feat/your-branch
```
Then open a Pull Request on GitHub into `main`. The lead reviews and merges.

## Commit messages
Start with the module name: `rules: ...`, `crewai: ...`, `autogen: ...`, `graph: ...`, `docs: ...`.

## Stuck with a merge conflict?
Don't force-push. Message the lead.
