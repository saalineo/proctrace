use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

#[derive(Debug, thiserror::Error)]
pub enum ProctraceError {
    #[error("cannot read {path}: {source}")]
    Procfs {
        path: &'static str,
        source: std::io::Error,
    },
    #[error("malformed {field} in /proc/self/status: {detail}")]
    StatusParse { field: &'static str, detail: String },
    #[error("proc_pidinfo({call}) failed: {source}")]
    MacosSyscall {
        call: &'static str,
        source: std::io::Error,
    },
    #[error("{call} failed: {source}")]
    WindowsApi {
        call: &'static str,
        source: std::io::Error,
    },
    #[error("unsupported platform")]
    UnsupportedPlatform,
}

impl From<ProctraceError> for PyErr {
    fn from(e: ProctraceError) -> PyErr {
        PyRuntimeError::new_err(e.to_string())
    }
}
