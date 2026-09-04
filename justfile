# **Justfile**

# **Description: Task runner configuration for the Riemann project.**

# **Run the Python application.**

# **Prerequisite: Builds the Rust extension first.**

run: build
	@if [ -x "./riemann/bin/python" ]; then \
		env -u LD_LIBRARY_PATH PYTHONPATH=python-app ./riemann/bin/python -m riemann; \
	elif [ -x "./.venv/bin/python" ]; then \
		env -u LD_LIBRARY_PATH PYTHONPATH=python-app ./.venv/bin/python -m riemann; \
	else \
		env -u LD_LIBRARY_PATH PYTHONPATH=python-app python3 -m riemann; \
	fi

# **Build and install the Rust extension into the current virtual environment.**

# **Uses maturin in development mode.**

build:
 maturin develop

# **Run the Rust unit test suite.**

# **Targets the 'riemann\_core' package.**

test-rust:
 cargo test \-p riemann\_core

# **Clean build artifacts.**

# **Removes Cargo target directory, compiled shared objects, and Python bytecode caches.**

clean:
 cargo clean
 find . \-name "\*.so" \-delete
 find . \-name "**pycache**" \-type d \-exec rm \-rf {} \+
