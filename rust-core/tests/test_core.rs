use pyo3::prelude::*;

#[test]
fn test_python_gil_acquisition() {
    pyo3::prepare_freethreaded_python();
    Python::with_gil(|py| {
        let sys = py.import_bound("sys").unwrap();
        let version: String = sys.getattr("version").unwrap().extract().unwrap();
        assert!(!version.is_empty());
    });
}
