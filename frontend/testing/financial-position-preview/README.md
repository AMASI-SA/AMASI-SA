# Synthetic report preview

This harness renders the real financial-position component using data generated
by the real read-only adapter. All names, order references and amounts are
invented test data. It has no production backend, authentication or write routes.
Never publish this harness as the Mezan application.

From the repository root, with backend dependencies and frontend packages installed:

```bash
PYTHONPATH=backend .venv/bin/python -B frontend/testing/financial-position-preview/generate_fixture.py
node frontend/testing/financial-position-preview/build.mjs
sites-preview start "$PWD/.evidence/financial-preview-site"
```

The compiled output is ignored and separate from `frontend/build`. Its small
static server exposes only the generated fixture and bundled page assets.
Refresh the fixture and rebuild after adapter or component changes.

`/compact.html` renders the same real page in a 390px-wide frame to inspect the
small-viewport CSS. This is a responsive-layout check, not a physical-device test.
