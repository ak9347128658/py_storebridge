# How to deploy py_storebridge to PyPI

This guide publishes **py_storebridge** as a public Python package so anyone can install it with:

```bash
pip install py_storebridge
pip install py_storebridge[s3]
pip install py_storebridge[all]
```

---

## 1. One-time setup

### PyPI account

1. Create an account at [https://pypi.org/account/register/](https://pypi.org/account/register/).
2. Enable 2FA.
3. Create a **Trusted Publisher** (recommended) or an API token.

### API token (manual upload)

1. Open [https://pypi.org/manage/account/token/](https://pypi.org/manage/account/token/).
2. Token name: `py_storebridge-publish`.
3. Scope: entire account (first upload) or project `py_storebridge` (later uploads).
4. Copy the token. It is shown once. It starts with `pypi-`.

Store it as an environment variable, never in git:

```powershell
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-…"
```

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-…
```

### TestPyPI (optional but recommended)

Register at [https://test.pypi.org](https://test.pypi.org) and create a separate token. Use it to rehearse a release without affecting the public index.

---

## 2. Version bump

Edit **one** place: `pyproject.toml`

```toml
[project]
name = "py_storebridge"
version = "1.0.1"
```

Keep `src/py_storebridge/__init__.py` `__version__` in sync, and add a `CHANGELOG.md` entry.

Use semantic versioning:

| Change | Version |
| --- | --- |
| Bug fix | patch (`1.0.1`) |
| New compatible feature | minor (`1.1.0`) |
| Breaking API change | major (`2.0.0`) |

---

## 3. Local checks

From the repository root, with the project venv activated:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev,build]"

pytest
python -m build
python -m twine check dist/*
```

`twine check` must report `PASSED` for both the wheel and the sdist.

Confirm the artifacts:

```
dist/py_storebridge-1.0.0.tar.gz
dist/py_storebridge-1.0.0-py3-none-any.whl
```

Delete `dist/` and `src/py_storebridge.egg-info/` before a clean rebuild if you already built an older version.

```powershell
Remove-Item -Recurse -Force dist, src\py_storebridge.egg-info -ErrorAction SilentlyContinue
python -m build
```

---

## 4. Publish to TestPyPI

```bash
python -m twine upload --repository testpypi dist/*
```

Install from TestPyPI to verify extras and metadata:

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple py_storebridge==1.0.0
python -c "import py_storebridge; print(py_storebridge.__version__)"
```

`--extra-index-url` is required because TestPyPI does not host Pydantic.

---

## 5. Publish to PyPI (public)

```bash
python -m twine upload dist/*
```

First upload of a new project name:

```bash
python -m twine upload --repository pypi dist/*
```

After a few minutes:

```bash
pip index versions py_storebridge
pip install py_storebridge
```

Project page: [https://pypi.org/project/py_storebridge/](https://pypi.org/project/py_storebridge/)

---

## 6. GitHub Actions trusted publishing (recommended)

Trusted publishing avoids long-lived tokens. PyPI issues a short-lived OIDC token to GitHub Actions.

### On PyPI

1. Open the project → **Publishing**.
2. Add a trusted publisher:
   - Owner: your GitHub user or org
   - Repository: `py_storebridge`
   - Workflow name: `publish.yml`
   - Environment: `pypi` (optional but safer)

### Workflow

The repository includes `.github/workflows/publish.yml`. It runs on a version tag:

```bash
git add pyproject.toml src/py_storebridge/__init__.py CHANGELOG.md
git commit -m "release: 1.0.1"
git tag v1.0.1
git push origin main --tags
```

The workflow builds the package, runs tests, and uploads to PyPI.

For CI-only (no publish), `.github/workflows/ci.yml` runs pytest on pull requests.

---

## 7. What gets uploaded

`setuptools` packages the `src/py_storebridge` tree. These files ship in the wheel:

- Python modules under `src/py_storebridge/`
- `py.typed` (PEP 561)

These files ship in the sdist (source distribution):

- `src/`, `tests/`, `docs/`, `README.md`, `LICENSE`, `pyproject.toml`, `CHANGELOG.md`

`.venv/`, `__pycache__/`, and secrets listed in `.gitignore` are excluded.

---

## 8. Optional extras after publish

Users install only the provider they need:

```bash
pip install py_storebridge[s3]
pip install py_storebridge[gcs]
pip install py_storebridge[azure]
pip install py_storebridge[all]
```

If you add a new extra, bump the **minor** version and document it in `README.md` and `CHANGELOG.md`.

---

## 9. Yank / delete a bad release

You cannot delete a published version on PyPI. You can **yank** it so `pip install py_storebridge` skips it while the file remains for pins:

1. Open the project on PyPI → **Options** / **Yank**.
2. Publish a new patch immediately (`1.0.2`).

---

## 10. Checklist

- [ ] Tests pass (`pytest`)
- [ ] Version bumped in `pyproject.toml` and `__init__.py`
- [ ] `CHANGELOG.md` updated
- [ ] `python -m build` produces sdist + wheel
- [ ] `twine check dist/*` passes
- [ ] TestPyPI install works (optional)
- [ ] Uploaded to PyPI
- [ ] `pip install py_storebridge` from a clean venv works
- [ ] Git tag `vX.Y.Z` pushed
