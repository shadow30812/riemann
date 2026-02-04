# **Justfile**

# **Description: Task runner configuration for the Riemann project.**

# **Run the Python application.**

# **Prerequisite: Builds the Rust extension first.**

run: build
 PYTHONPATH=python-app python3 -m riemann

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