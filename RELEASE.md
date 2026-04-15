# Release Process

This document is a maintainer checklist for releasing a new version of `magscope`.

PyPI publication is handled by GitHub Actions after a GitHub Release is published. The normal release flow is:

1. Prepare the version and changelog.
2. Run validation locally.
3. Optionally publish to TestPyPI and smoke test the package.
4. Publish a GitHub Release tagged as `vX.Y.Z`.
5. Verify the PyPI release and related project pages.

## Checklist

### 1. Decide the release version

- Choose the next version number `X.Y.Z`.
- Confirm the release only includes changes that are ready to ship.

### 2. Update versioned files

- Update `pyproject.toml`:
  - Set `[project].version = "X.Y.Z"`.
- Update `docs/source/conf.py`:
  - Set `release = 'X.Y.Z'`.

### 3. Finalize the changelog

- Review `CHANGELOG.md` and make sure the release notes are accurate and user-facing.
- Change the pending release heading from:
  - `## [X.Y.Z] - Unreleased`
  to:
  - `## [X.Y.Z] - YYYY-MM-DD`
- Keep a fresh empty `## [Unreleased]` section at the top for future work.
- Update the comparison links at the bottom of `CHANGELOG.md`:
  - Point `[X.Y.Z]` to the compare view from the previous tag to `vX.Y.Z`.
  - Point `[Unreleased]` to the compare view from `vX.Y.Z` to `HEAD`.

### 4. Validate locally

- Run the test suite:

```bash
python -m pytest -q
```

- Build the docs locally:

```bash
python -m pip install -e .[docs]
python -m sphinx -b html docs/source docs/build
```

- Confirm the docs build completes without warnings or import errors.

- Build the release distributions:

```bash
python -m pip install --upgrade build
python -m build
```

- Confirm `dist/` contains both a wheel and source distribution.

### 5. Optional: publish to TestPyPI first

- Trigger `.github/workflows/publish-to-testpypi.yml` with GitHub Actions.
- After it completes, verify that the package can be installed from TestPyPI in a clean environment.
- Smoke test a basic import:

```bash
python -c "import magscope; print(magscope.__file__)"
```

### 6. Publish the real release

- Commit the release-prep changes.
- Create and push the release tag as `vX.Y.Z` if needed.
- Create a GitHub Release for tag `vX.Y.Z`.
- Use the finalized changelog entry as the GitHub Release notes.
- Publish the GitHub Release.
- Confirm `.github/workflows/publish-to-pypi.yml` starts and completes successfully.

### 7. Post-release verification

- Verify the new version appears on PyPI:
  - `https://pypi.org/project/magscope/`
- Verify the GitHub Release page is correct.
- Verify the install command works for the released version in a clean environment.
- Verify Read the Docs pages render correctly:
  - `https://magscope.readthedocs.io/en/latest/`
  - `https://magscope.readthedocs.io/en/stable/`
- Confirm the `stable` docs version has advanced to the new release and is not still pointing at an older build.
- Confirm docs and README links still point to the correct package and release pages.
- Move any remaining items that missed the release back into `## [Unreleased]`.

## Notes For This Repository

- The package version currently lives in `pyproject.toml`.
- The docs release version currently lives in `docs/source/conf.py`.
- Release summaries live in `CHANGELOG.md` and follow Keep a Changelog style.
- GitHub Actions workflows relevant to releases:
  - `.github/workflows/tests.yml`
  - `.github/workflows/publish-to-testpypi.yml`
  - `.github/workflows/publish-to-pypi.yml`
- PyPI publishing is triggered by publishing a GitHub Release.
