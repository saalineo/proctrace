use crate::error::ProctraceError;
use crate::resources::now_ns;
use crate::resources::ResourceSnapshot;

fn count_open_fds() -> Result<u32, ProctraceError> {
    // proc_pidinfo(PROC_PIDLISTFDS) rejects a NULL buffer, so query with a
    // real buffer and grow it by doubling until the table fits.
    // Note: when the buffer is full (ret == capacity * entry_size), proc_pidinfo
    // truncates output without returning ENOMEM, so we must check if ret < capacity * entry_size.
    let entry_size = std::mem::size_of::<libc::proc_fdinfo>() as i32;
    let mut buf: Vec<libc::proc_fdinfo> = Vec::new();
    let mut capacity: i32 = 256;
    loop {
        buf.resize(capacity as usize, unsafe { std::mem::zeroed() });
        let ret = unsafe {
            // SAFETY: `buf` holds `capacity` zero-initialized proc_fdinfo
            // entries and `capacity * entry_size` bytes are handed to the
            // kernel, which fills the leading live entries.
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
                return Ok((ret / entry_size) as u32);
            }
        } else if ret == 0 {
            return Ok(0);
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

pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    let mut info: libc::proc_taskinfo = unsafe { std::mem::zeroed() };
    let ret = unsafe {
        // SAFETY: `info` is a writable buffer of the exact size proc_pidinfo
        // expects; the call fills it with the current process's task info.
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

pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    let mut paths = Vec::new();
    for fd in 0..4096 {
        let mut buf = vec![0u8; libc::PATH_MAX as usize];
        let ret = unsafe {
            // SAFETY: `buf` is a writable buffer of PATH_MAX bytes, which is
            // exactly what F_GETPATH writes; fcntl fails harmlessly on closed fds.
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
