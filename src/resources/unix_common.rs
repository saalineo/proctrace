use crate::error::ProctraceError;

#[derive(Debug, Clone, Copy, Default)]
pub struct RusageMetrics {
    pub cpu_user_ns: u64,
    pub cpu_sys_ns: u64,
    pub vol_ctx: u64,
    pub invol_ctx: u64,
    pub minor_faults: u64,
    pub major_faults: u64,
}

pub fn query_rusage() -> Result<RusageMetrics, ProctraceError> {
    let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
    let ret = unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
    if ret != 0 {
        return Err(ProctraceError::Procfs {
            path: "getrusage",
            source: std::io::Error::last_os_error(),
        });
    }

    let cpu_user_ns = (ru.ru_utime.tv_sec.max(0) as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add((ru.ru_utime.tv_usec.max(0) as u64).saturating_mul(1_000));

    let cpu_sys_ns = (ru.ru_stime.tv_sec.max(0) as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add((ru.ru_stime.tv_usec.max(0) as u64).saturating_mul(1_000));

    let vol_ctx = ru.ru_nvcsw.max(0) as u64;
    let invol_ctx = ru.ru_nivcsw.max(0) as u64;
    let minor_faults = ru.ru_minflt.max(0) as u64;
    let major_faults = ru.ru_majflt.max(0) as u64;

    Ok(RusageMetrics {
        cpu_user_ns,
        cpu_sys_ns,
        vol_ctx,
        invol_ctx,
        minor_faults,
        major_faults,
    })
}
