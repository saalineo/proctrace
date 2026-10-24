use crate::error::ProctraceError;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
pub mod unix_common;

#[cfg(target_os = "linux")]
mod linux;

#[cfg(target_os = "macos")]
mod macos;

#[cfg(target_os = "linux")]
use linux as backend;

#[cfg(target_os = "macos")]
use macos as backend;

/// Byte-level resource usage of this process at a single instant.
/// Timestamps are Unix nanoseconds so callers can compute deltas.
#[pyclass(skip_from_py_object)]
#[derive(Debug, Clone)]
pub struct ResourceSnapshot {
    #[pyo3(get)]
    pub rss_bytes: u64,

    #[pyo3(get)]
    pub vms_bytes: u64,

    #[pyo3(get)]
    pub open_fds: u32,

    #[pyo3(get)]
    pub timestamp_ns: u64,
}

#[pymethods]
impl ResourceSnapshot {
    pub fn rss_mb(&self) -> f64 {
        self.rss_bytes as f64 / (1024.0 * 1024.0)
    }

    pub fn vms_mb(&self) -> f64 {
        self.vms_bytes as f64 / (1024.0 * 1024.0)
    }

    pub fn __repr__(&self) -> String {
        format!(
            "ResourceSnapshot(rss={:.1}MB, vms={:.1}MB, fds={})",
            self.rss_mb(),
            self.vms_mb(),
            self.open_fds
        )
    }
}

/// Extended resource snapshot containing CPU times, context switches,
/// page faults, disk I/O, open socket states, and per-thread CPU counters.
#[pyclass(skip_from_py_object)]
#[derive(Debug, Clone, Default)]
pub struct ExtendedResourceSnapshot {
    #[pyo3(get)]
    pub rss_bytes: u64,

    #[pyo3(get)]
    pub vms_bytes: u64,

    #[pyo3(get)]
    pub open_fds: u32,

    #[pyo3(get)]
    pub timestamp_ns: u64,

    // CPU & Scheduler
    #[pyo3(get)]
    pub cpu_user_ns: u64,

    #[pyo3(get)]
    pub cpu_sys_ns: u64,

    #[pyo3(get)]
    pub vol_ctx: u64,

    #[pyo3(get)]
    pub invol_ctx: u64,

    // Page Faults
    #[pyo3(get)]
    pub minor_faults: u64,

    #[pyo3(get)]
    pub major_faults: u64,

    // Disk I/O
    #[pyo3(get)]
    pub io_read_bytes: u64,

    #[pyo3(get)]
    pub io_write_bytes: u64,

    #[pyo3(get)]
    pub io_rchar_bytes: u64,

    #[pyo3(get)]
    pub io_wchar_bytes: u64,

    // Socket state counts
    #[pyo3(get)]
    pub sockets_by_state: HashMap<String, u32>,

    // Per-thread CPU tuples: (tid, comm, user_ns, sys_ns)
    #[pyo3(get)]
    pub thread_cpu: Vec<(u64, String, u64, u64)>,
}

#[pymethods]
impl ExtendedResourceSnapshot {
    pub fn rss_mb(&self) -> f64 {
        self.rss_bytes as f64 / (1024.0 * 1024.0)
    }

    pub fn vms_mb(&self) -> f64 {
        self.vms_bytes as f64 / (1024.0 * 1024.0)
    }

    pub fn cpu_user_s(&self) -> f64 {
        self.cpu_user_ns as f64 / 1_000_000_000.0
    }

    pub fn cpu_sys_s(&self) -> f64 {
        self.cpu_sys_ns as f64 / 1_000_000_000.0
    }

    pub fn __repr__(&self) -> String {
        format!(
            "ExtendedResourceSnapshot(rss={:.1}MB, cpu_user={:.2}s, cpu_sys={:.2}s, fds={})",
            self.rss_mb(),
            self.cpu_user_s(),
            self.cpu_sys_s(),
            self.open_fds
        )
    }
}

pub(crate) fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    backend::snapshot()
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    Err(ProctraceError::UnsupportedPlatform)
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
pub fn snapshot_extended(
    cpu: bool,
    io: bool,
    sockets: bool,
    threads_cpu: bool,
) -> Result<ExtendedResourceSnapshot, ProctraceError> {
    backend::snapshot_extended(cpu, io, sockets, threads_cpu)
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn snapshot_extended(
    _cpu: bool,
    _io: bool,
    _sockets: bool,
    _threads_cpu: bool,
) -> Result<ExtendedResourceSnapshot, ProctraceError> {
    Err(ProctraceError::UnsupportedPlatform)
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    backend::list_fd_paths()
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    Err(ProctraceError::UnsupportedPlatform)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_bytes_to_mb() {
        let snap = ResourceSnapshot {
            rss_bytes: 2 * 1024 * 1024,
            vms_bytes: 1024 * 1024,
            open_fds: 0,
            timestamp_ns: 0,
        };
        assert_eq!(snap.rss_mb(), 2.0);
        assert_eq!(snap.vms_mb(), 1.0);
    }

    #[test]
    fn extended_snapshot_methods() {
        let ext = ExtendedResourceSnapshot {
            rss_bytes: 4 * 1024 * 1024,
            cpu_user_ns: 1_500_000_000,
            cpu_sys_ns: 500_000_000,
            ..Default::default()
        };
        assert_eq!(ext.rss_mb(), 4.0);
        assert_eq!(ext.cpu_user_s(), 1.5);
        assert_eq!(ext.cpu_sys_s(), 0.5);
    }
}
