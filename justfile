dev:
    maturin develop -m pyproject.toml --release
    python python-app/riemann_app/main.py
