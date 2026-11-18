//! Windows resource backend built on Win32 APIs.
//!
//! Differences from the Unix backends:
//! - `open_fds` reports the process handle count.
//! - Windows exposes a single page-fault counter, reported as `minor_faults`.
//! - Per-process context-switch counts have no cheap API and are left at 0.
//! - `list_fd_paths` is not implemented and returns an empty list.

use crate::error::ProctraceError;
use crate::resources::{now_ns, ExtendedResourceSnapshot, ResourceSnapshot};
use std::collections::HashMap;
use std::mem::{size_of, zeroed};

use windows_sys::Win32::Foundation::{CloseHandle, FILETIME, INVALID_HANDLE_VALUE, NO_ERROR};
use windows_sys::Win32::NetworkManagement::IpHelper::{
    GetExtendedTcpTable, MIB_TCP6ROW_OWNER_PID, MIB_TCPROW_OWNER_PID, TCP_TABLE_OWNER_PID_ALL,
};
use windows_sys::Win32::Networking::WinSock::{AF_INET, AF_INET6};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32First, Process32Next, Thread32First, Thread32Next,
    PROCESSENTRY32, TH32CS_SNAPPROCESS, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::ProcessStatus::{
    GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS, PROCESS_MEMORY_COUNTERS_EX,
};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetCurrentProcessId, GetProcessHandleCount, GetProcessIoCounters,
    GetProcessTimes, GetThreadTimes, OpenProcess, OpenThread, IO_COUNTERS,
    PROCESS_QUERY_LIMITED_INFORMATION, THREAD_QUERY_LIMITED_INFORMATION,
};

fn win_err(call: &'static str) -> ProctraceError {
    ProctraceError::WindowsApi {
        call,
        source: std::io::Error::last_os_error(),
    }
}

/// FILETIME holds 100-nanosecond ticks split across two u32 halves.
fn filetime_to_ns(ft: &FILETIME) -> u64 {
    let ticks = ((ft.dwHighDateTime as u64) << 32) | ft.dwLowDateTime as u64;
    ticks.saturating_mul(100)
}

fn memory_counters() -> Result<PROCESS_MEMORY_COUNTERS_EX, ProctraceError> {
    let mut pmc: PROCESS_MEMORY_COUNTERS_EX = unsafe { zeroed() };
    pmc.cb = size_of::<PROCESS_MEMORY_COUNTERS_EX>() as u32;
    let ok = unsafe {
        GetProcessMemoryInfo(
            GetCurrentProcess(),
            &mut pmc as *mut PROCESS_MEMORY_COUNTERS_EX as *mut PROCESS_MEMORY_COUNTERS,
            pmc.cb,
        )
    };
    if ok == 0 {
        return Err(win_err("GetProcessMemoryInfo"));
    }
    Ok(pmc)
}

fn handle_count() -> Result<u32, ProctraceError> {
    let mut count: u32 = 0;
    if unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) } == 0 {
        return Err(win_err("GetProcessHandleCount"));
    }
    Ok(count)
}

pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    let pmc = memory_counters()?;
    Ok(ResourceSnapshot {
        rss_bytes: pmc.WorkingSetSize as u64,
        // PrivateUsage is the committed private memory, the closest analogue of VmSize.
        vms_bytes: pmc.PrivateUsage as u64,
        open_fds: handle_count()?,
        timestamp_ns: now_ns(),
    })
}

pub fn snapshot_extended(
    cpu: bool,
    io: bool,
    sockets: bool,
    threads_cpu: bool,
) -> Result<ExtendedResourceSnapshot, ProctraceError> {
    let pmc = memory_counters()?;
    let mut ext = ExtendedResourceSnapshot {
        rss_bytes: pmc.WorkingSetSize as u64,
        vms_bytes: pmc.PrivateUsage as u64,
        open_fds: handle_count()?,
        timestamp_ns: now_ns(),
        ..Default::default()
    };

    if cpu {
        let (mut creation, mut exit, mut kernel, mut user): (FILETIME, FILETIME, FILETIME, FILETIME) =
            unsafe { zeroed() };
        let ok = unsafe {
            GetProcessTimes(
                GetCurrentProcess(),
                &mut creation,
                &mut exit,
                &mut kernel,
                &mut user,
            )
        };
        if ok != 0 {
            ext.cpu_user_ns = filetime_to_ns(&user);
            ext.cpu_sys_ns = filetime_to_ns(&kernel);
        }
        ext.minor_faults = pmc.PageFaultCount as u64;
    }

    if io {
        let mut counters: IO_COUNTERS = unsafe { zeroed() };
        if unsafe { GetProcessIoCounters(GetCurrentProcess(), &mut counters) } != 0 {
            ext.io_read_bytes = counters.ReadTransferCount;
            ext.io_write_bytes = counters.WriteTransferCount;
            ext.io_rchar_bytes = counters.ReadTransferCount;
            ext.io_wchar_bytes = counters.WriteTransferCount;
        }
    }

    if sockets {
        ext.sockets_by_state = collect_sockets_by_state();
    }

    if threads_cpu {
        ext.thread_cpu = collect_thread_cpu();
    }

    Ok(ext)
}

/// Memory and handle usage of an arbitrary process, as (rss_bytes, vms_bytes, handles).
/// Returns `None` when the process does not exist or cannot be queried.
pub fn snapshot_pid(pid: u32) -> Option<(u64, u64, u32)> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return None;
    }
    let mut pmc: PROCESS_MEMORY_COUNTERS_EX = unsafe { zeroed() };
    pmc.cb = size_of::<PROCESS_MEMORY_COUNTERS_EX>() as u32;
    let ok = unsafe {
        GetProcessMemoryInfo(
            handle,
            &mut pmc as *mut PROCESS_MEMORY_COUNTERS_EX as *mut PROCESS_MEMORY_COUNTERS,
            pmc.cb,
        )
    };
    let mut handles: u32 = 0;
    unsafe { GetProcessHandleCount(handle, &mut handles) };
    unsafe { CloseHandle(handle) };
    if ok == 0 {
        return None;
    }
    Some((pmc.WorkingSetSize as u64, pmc.PrivateUsage as u64, handles))
}

/// Number of direct child processes of the current process.
pub fn count_child_processes() -> u32 {
    let me = unsafe { GetCurrentProcessId() };
    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snap == INVALID_HANDLE_VALUE {
        return 0;
    }
    let mut entry: PROCESSENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32>() as u32;
    let mut count = 0;
    let mut more = unsafe { Process32First(snap, &mut entry) } != 0;
    while more {
        if entry.th32ParentProcessID == me && entry.th32ProcessID != me {
            count += 1;
        }
        more = unsafe { Process32Next(snap, &mut entry) } != 0;
    }
    unsafe { CloseHandle(snap) };
    count
}

/// Windows has no cheap, stable way to enumerate handle paths; see module docs.
pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    Ok(Vec::new())
}

fn collect_thread_cpu() -> Vec<(u64, String, u64, u64)> {
    let mut out = Vec::new();
    let pid = unsafe { GetCurrentProcessId() };

    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snap == INVALID_HANDLE_VALUE {
        return out;
    }

    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;

    let mut more = unsafe { Thread32First(snap, &mut entry) } != 0;
    while more {
        if entry.th32OwnerProcessID == pid {
            let tid = entry.th32ThreadID;
            let handle = unsafe { OpenThread(THREAD_QUERY_LIMITED_INFORMATION, 0, tid) };
            if !handle.is_null() {
                let (mut creation, mut exit, mut kernel, mut user): (
                    FILETIME,
                    FILETIME,
                    FILETIME,
                    FILETIME,
                ) = unsafe { zeroed() };
                let ok = unsafe {
                    GetThreadTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user)
                };
                if ok != 0 {
                    out.push((
                        tid as u64,
                        format!("thread-{tid}"),
                        filetime_to_ns(&user),
                        filetime_to_ns(&kernel),
                    ));
                }
                unsafe { CloseHandle(handle) };
            }
        }
        more = unsafe { Thread32Next(snap, &mut entry) } != 0;
    }

    unsafe { CloseHandle(snap) };
    out
}

/// Map an MIB_TCP_STATE_* value to the same names the Linux backend reports.
fn tcp_state_name(state: u32) -> &'static str {
    match state {
        1 => "CLOSE",
        2 => "LISTEN",
        3 => "SYN_SENT",
        4 => "SYN_RECV",
        5 => "ESTABLISHED",
        6 => "FIN_WAIT1",
        7 => "FIN_WAIT2",
        8 => "CLOSE_WAIT",
        9 => "CLOSING",
        10 => "LAST_ACK",
        11 => "TIME_WAIT",
        12 => "DELETE_TCB",
        _ => "UNKNOWN",
    }
}

/// Fetch the system TCP table for one address family as a raw buffer.
fn tcp_table(family: u32) -> Option<Vec<u8>> {
    let mut size: u32 = 0;
    unsafe {
        GetExtendedTcpTable(
            std::ptr::null_mut(),
            &mut size,
            0,
            family,
            TCP_TABLE_OWNER_PID_ALL,
            0,
        );
    }
    // The table can grow between calls, so retry a few times.
    for _ in 0..4 {
        let mut buf = vec![0u8; size as usize];
        let ret = unsafe {
            GetExtendedTcpTable(
                buf.as_mut_ptr() as *mut _,
                &mut size,
                0,
                family,
                TCP_TABLE_OWNER_PID_ALL,
                0,
            )
        };
        if ret == NO_ERROR {
            return Some(buf);
        }
    }
    None
}

fn count_rows<R: Copy>(
    buf: &[u8],
    pid: u32,
    row_state_pid: impl Fn(&R) -> (u32, u32),
    counts: &mut HashMap<String, u32>,
) {
    // Layout: u32 dwNumEntries followed by an array of rows.
    if buf.len() < size_of::<u32>() {
        return;
    }
    let n = u32::from_ne_bytes(buf[..4].try_into().unwrap()) as usize;
    let base = size_of::<u32>();
    let row_size = size_of::<R>();
    for i in 0..n {
        let start = base + i * row_size;
        if start + row_size > buf.len() {
            break;
        }
        // Rows are not guaranteed to be aligned inside the byte buffer.
        let row: R = unsafe { std::ptr::read_unaligned(buf.as_ptr().add(start) as *const R) };
        let (state, owner) = row_state_pid(&row);
        if owner == pid {
            *counts.entry(tcp_state_name(state).to_string()).or_insert(0) += 1;
        }
    }
}

fn collect_sockets_by_state() -> HashMap<String, u32> {
    let pid = unsafe { GetCurrentProcessId() };
    let mut counts = HashMap::new();

    if let Some(buf) = tcp_table(AF_INET as u32) {
        count_rows::<MIB_TCPROW_OWNER_PID>(&buf, pid, |r| (r.dwState, r.dwOwningPid), &mut counts);
    }
    if let Some(buf) = tcp_table(AF_INET6 as u32) {
        count_rows::<MIB_TCP6ROW_OWNER_PID>(&buf, pid, |r| (r.dwState, r.dwOwningPid), &mut counts);
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_reports_nonzero_memory() {
        let snap = snapshot().unwrap();
        assert!(snap.rss_bytes > 0);
        assert!(snap.open_fds > 0);
    }

    #[test]
    fn extended_snapshot_collects_threads() {
        let ext = snapshot_extended(true, true, true, true).unwrap();
        assert!(!ext.thread_cpu.is_empty());
    }

    #[test]
    fn snapshot_pid_of_self() {
        let (rss, _, handles) = snapshot_pid(std::process::id()).unwrap();
        assert!(rss > 0);
        assert!(handles > 0);
        assert!(snapshot_pid(0xFFFF_FFF0).is_none());
    }

    #[test]
    fn filetime_conversion() {
        let ft = FILETIME {
            dwLowDateTime: 10,
            dwHighDateTime: 0,
        };
        assert_eq!(filetime_to_ns(&ft), 1000);
    }

    #[test]
    fn state_names() {
        assert_eq!(tcp_state_name(5), "ESTABLISHED");
        assert_eq!(tcp_state_name(2), "LISTEN");
        assert_eq!(tcp_state_name(99), "UNKNOWN");
    }
}
