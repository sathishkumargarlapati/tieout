# Releasing TieOut

Releases go to PyPI from GitHub Actions through PyPI's trusted publishing. No password or API token is stored in the repository or in GitHub.

## One-time setup

1. Create an account on [PyPI](https://pypi.org/account/register/) and turn on two-factor authentication.
2. Open [your publishing settings](https://pypi.org/manage/account/publishing/) and, under "Create a new pending publisher", choose GitHub and enter:

   | Field | Value |
   |---|---|
   | PyPI project name | `tieout` |
   | Owner | `sathishkumargarlapati` |
   | Repository name | `tieout` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. In this repository on GitHub, open **Settings → Environments**, create an environment named `pypi`, and add yourself as a required reviewer. Every upload then waits for your approval.

## Each release

1. On `main`, check that `version` in `pyproject.toml` is the version to release, and that `CHANGELOG.md` describes it.
2. On GitHub, open **Releases → Draft a new release**. Create the tag `v` + the version (for example `v0.1.0`) on `main`, title it "TieOut 0.1.0", paste the changelog entry and publish.
3. The Release workflow checks that the tag matches the version, runs the tests, builds the package and, after your approval, uploads it to PyPI.

Anyone can then install it with `pip install tieout`.
