use pyo3::prelude::*;

#[test]
fn test_python_gil_acquisition() {
    Python::initialize();
    Python::attach(|py| {
        let sys = py.import("sys").unwrap();
        let version: String = sys.getattr("version").unwrap().extract().unwrap();
        assert!(!version.is_empty());
    });
}
