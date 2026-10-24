use crate::error::ProctraceError;
use crate::resources::unix_common;
use crate::resources::{now_ns, ExtendedResourceSnapshot, ResourceSnapshot};
use std::collections::HashMap;

fn list_fd_info() -> Result<Vec<libc::proc_fdinfo>, ProctraceError> {
    // proc_pidinfo(PROC_PIDLISTFDS) rejects a NULL buffer, so query with a
    // real buffer and grow it by doubling until the table fits.
    let entry_size = std::mem::size_of::<libc::proc_fdinfo>() as i32;
    let mut buf: Vec<libc::proc_fdinfo> = Vec::new();
    let mut capacity: i32 = 256;
    loop {
        buf.resize(capacity as usize, unsafe { std::mem::zeroed() });
        let ret = unsafe {
            libc::proc_pidinfo(
                libc::getpid(),
                libc::PROC_PIDLISTFDS,
                0,
                buf.as_mut_ptr().cast::<libc::c_void>(),
                capacity * entry_size,
            )
        };
        if ret > 0 {
            let buffer_bytes = capacity * entry_size;
            if ret < buffer_bytes {
                buf.truncate((ret / entry_size) as usize);
                return Ok(buf);
            }
        } else if ret == 0 {
            return Ok(Vec::new());
        } else {
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ENOMEM) {
                return Err(ProctraceError::MacosSyscall {
                    call: "PROC_PIDLISTFDS",
                    source: err,
                });
            }
        }
        capacity = capacity.saturating_mul(2);
        if capacity > 65536 {
            return Err(ProctraceError::MacosSyscall {
                call: "PROC_PIDLISTFDS",
                source: std::io::Error::from_raw_os_error(libc::E2BIG),
            });
        }
    }
}

fn count_open_fds() -> Result<u32, ProctraceError> {
    Ok(list_fd_info()?.len() as u32)
}

pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    let mut info: libc::proc_taskinfo = unsafe { std::mem::zeroed() };
    let ret = unsafe {
        libc::proc_pidinfo(
            libc::getpid(),
            libc::PROC_PIDTASKINFO,
            0,
            (&mut info as *mut libc::proc_taskinfo).cast::<libc::c_void>(),
            std::mem::size_of::<libc::proc_taskinfo>() as i32,
        )
    };
    if ret <= 0 {
        return Err(ProctraceError::MacosSyscall {
            call: "PROC_PIDTASKINFO",
            source: std::io::Error::last_os_error(),
        });
    }

    let fd_count = count_open_fds()?;

    Ok(ResourceSnapshot {
        rss_bytes: info.pti_resident_size,
        vms_bytes: info.pti_virtual_size,
        open_fds: fd_count,
        timestamp_ns: now_ns(),
    })
}

pub fn snapshot_extended(
    cpu: bool,
    io: bool,
    _sockets: bool,
    _threads_cpu: bool,
) -> Result<ExtendedResourceSnapshot, ProctraceError> {
    let mut info: libc::proc_taskinfo = unsafe { std::mem::zeroed() };
    let ret = unsafe {
        libc::proc_pidinfo(
            libc::getpid(),
            libc::PROC_PIDTASKINFO,
            0,
            (&mut info as *mut libc::proc_taskinfo).cast::<libc::c_void>(),
            std::mem::size_of::<libc::proc_taskinfo>() as i32,
        )
    };
    if ret <= 0 {
        return Err(ProctraceError::MacosSyscall {
            call: "PROC_PIDTASKINFO",
            source: std::io::Error::last_os_error(),
        });
    }

    let fd_count = count_open_fds().unwrap_or(0);

    let mut ext = ExtendedResourceSnapshot {
        rss_bytes: info.pti_resident_size,
        vms_bytes: info.pti_virtual_size,
        open_fds: fd_count,
        timestamp_ns: now_ns(),
        ..Default::default()
    };

    if cpu {
        if let Ok(ru) = unix_common::query_rusage() {
            ext.cpu_user_ns = ru.cpu_user_ns;
            ext.cpu_sys_ns = ru.cpu_sys_ns;
            ext.vol_ctx = ru.vol_ctx;
            ext.invol_ctx = ru.invol_ctx;
            ext.minor_faults = ru.minor_faults;
            ext.major_faults = ru.major_faults;
        }
    }

    if io {
        #[repr(C)]
        struct RusageInfoV2 {
            _ri_uuid: [u8; 16],
            _ri_user_time: u64,
            _ri_system_time: u64,
            _ri_pkg_idle_wkups: u64,
            _ri_interrupt_wkups: u64,
            _ri_pageins: u64,
            _ri_wired_size: u64,
            _ri_resident_size: u64,
            _ri_phys_footprint: u64,
            _ri_proc_start_abstime: u64,
            _ri_proc_exit_abstime: u64,
            _ri_child_user_time: u64,
            _ri_child_system_time: u64,
            _ri_child_pkg_idle_wkups: u64,
            _ri_child_interrupt_wkups: u64,
            _ri_child_pageins: u64,
            _ri_child_elapsed_abstime: u64,
            ri_diskio_bytesread: u64,
            ri_diskio_byteswritten: u64,
        }

        extern "C" {
            fn proc_pid_rusage(
                pid: libc::pid_t,
                flavor: libc::c_int,
                buffer: *mut libc::c_void,
            ) -> libc::c_int;
        }

        let mut r_info: RusageInfoV2 = unsafe { std::mem::zeroed() };
        let rusage_ret = unsafe {
            proc_pid_rusage(
                libc::getpid(),
                2, // RUSAGE_INFO_V2
                (&mut r_info as *mut RusageInfoV2).cast::<libc::c_void>(),
            )
        };
        if rusage_ret == 0 {
            ext.io_read_bytes = r_info.ri_diskio_bytesread;
            ext.io_write_bytes = r_info.ri_diskio_byteswritten;
        }
    }

    ext.sockets_by_state = HashMap::new();
    ext.thread_cpu = Vec::new();

    Ok(ext)
}

pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    let mut paths = Vec::new();
    for fd_info in list_fd_info()? {
        let fd = fd_info.proc_fd;
        let mut buf = vec![0u8; libc::PATH_MAX as usize];
        let ret = unsafe {
            libc::fcntl(fd, libc::F_GETPATH, buf.as_mut_ptr())
        };
        if ret == 0 {
            let len = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
            buf.truncate(len);
            paths.push(String::from_utf8_lossy(&buf).into_owned());
        }
    }
    paths.sort();
    Ok(paths)
}
