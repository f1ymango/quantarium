# Releasing Quantarium

Publishing is done by the maintainer. These are the steps used to build and check v1.0.3; repeat them for each release, under the rules in [versioning](versioning.md).

## Build

Build in a dedicated virtual environment, never the global Python, with setuptools pinned to a release at least seven days old (84.0.0 was used for v1.0.3):

```bash
py -m venv .build-env
```

```bash
.build-env/Scripts/python.exe -m pip install --only-binary=:all: --no-deps setuptools==84.0.0
```

Delete any old `build/` folder first, since setuptools copies its stale files into a new wheel. Then build the wheel and the source archive:

```bash
.build-env/Scripts/python.exe -m pip wheel . --no-deps --no-build-isolation -w dist
```

```bash
.build-env/Scripts/python.exe -c "import setuptools.build_meta as b; b.build_sdist('dist')"
```

## Check

- The wheel holds `quantarium/` (about 111 files), the 2-file alias package for imports written before 1.0, and the `dist-info` folder with `LICENSE.md` and `NOTICE`; nothing from `runs/`, `.quantarium/`, `private/`, or `__pycache__`.
- The source archive holds the package, the alias, the license files, the README, and `pyproject.toml`; no tests (`MANIFEST.in`).
- Install the wheel with `tzdata` into a new, empty virtual environment and run it from a folder outside the repository: `quantarium --version`, `quantarium run examples/experiment.json` (with a copy of `examples/`), `quantarium data check examples/data/fictional_index`, and `quantarium serve --fresh` with the app opened in a browser.
- Run the secret and personal-data scan on everything that goes out.

## Publish

Uploading to PyPI uses your PyPI account; Quantarium's tools never hold that token. PyPI's **Trusted Publishing** avoids storing a token at all, but needs a GitHub Actions workflow, which `AGENTS.md` allows only after review. Otherwise upload the two files in `dist/` with `twine`, installed in its own environment at a pinned version.

The README's images point at `https://raw.githubusercontent.com/f1ymango/quantarium/main/docs/images/`, so they appear on PyPI once the public repository exists.
