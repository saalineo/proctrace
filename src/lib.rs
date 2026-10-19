use pyo3::prelude::*;

#[cfg(unix)]
mod signal;

mod error;
mod ipc_probe;
mod resources;
mod sampler;

use ipc_probe::{IpcStats, SocketStats};
use resources::ResourceSnapshot;
pub use sampler::{BackgroundSampler, Sample, DEFAULT_SAMPLE_CAPACITY};

#[pyfunction]
fn probe_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pyfunction]
fn snapshot_resources() -> PyResult<ResourceSnapshot> {
    resources::snapshot().map_err(Into::into)
}

#[pyfunction]
fn list_open_fds() -> PyResult<Vec<String>> {
    resources::list_fd_paths().map_err(Into::into)
}

#[cfg(unix)]
#[pyfunction]
fn register_signal_pipe(signal_num: i32) -> PyResult<(i32, i32)> {
    signal::register_signal_pipe(signal_num)
}

#[pymodule]
fn _proctrace_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(probe_version, m)?)?;
    m.add_function(wrap_pyfunction!(snapshot_resources, m)?)?;
    m.add_function(wrap_pyfunction!(list_open_fds, m)?)?;
    #[cfg(unix)]
    m.add_function(wrap_pyfunction!(register_signal_pipe, m)?)?;
    m.add_class::<ResourceSnapshot>()?;
    m.add_class::<BackgroundSampler>()?;
    m.add_class::<IpcStats>()?;
    m.add_class::<SocketStats>()?;
    m.add("DEFAULT_SAMPLE_CAPACITY", sampler::DEFAULT_SAMPLE_CAPACITY)?;
    Ok(())
}
