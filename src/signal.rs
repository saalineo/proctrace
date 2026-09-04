use pyo3::prelude::*;
use std::sync::atomic::{AtomicI32, Ordering};

const MAX_SIGNALS: usize = 128;

#[allow(clippy::declare_interior_mutable_const)]
const INIT_FD: AtomicI32 = AtomicI32::new(-1);
static PIPE_WRITE_FDS: [AtomicI32; MAX_SIGNALS] = [INIT_FD; MAX_SIGNALS];

extern "C" fn handle_signal(signum: libc::c_int) {
    if signum >= 0 && (signum as usize) < MAX_SIGNALS {
        let fd = PIPE_WRITE_FDS[signum as usize].load(Ordering::Relaxed);
        if fd >= 0 {
            unsafe {
                libc::write(fd, b"\x01".as_ptr() as *const libc::c_void, 1);
            }
        }
    }
}

fn create_nonblocking_pipe() -> std::io::Result<(i32, i32)> {
    let mut fds = [0i32; 2];

    #[cfg(target_os = "linux")]
    let ret = unsafe {
        libc::pipe2(fds.as_mut_ptr(), libc::O_NONBLOCK | libc::O_CLOEXEC)
    };

    #[cfg(not(target_os = "linux"))]
    let ret = {
        // macOS/BSD lack pipe2: create a plain pipe and set the flags with fcntl
        let ret = unsafe { libc::pipe(fds.as_mut_ptr()) };
        if ret == 0 {
            for fd in &fds {
                unsafe {
                    libc::fcntl(*fd, libc::F_SETFL, libc::O_NONBLOCK);
                    libc::fcntl(*fd, libc::F_SETFD, libc::FD_CLOEXEC);
                }
            }
        }
        ret
    };

    if ret != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok((fds[0], fds[1]))
}

#[pyfunction]
pub fn register_signal_pipe(signal_num: i32) -> PyResult<(i32, i32)> {
    if signal_num <= 0 || (signal_num as usize) >= MAX_SIGNALS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Invalid signal number: {signal_num} (must be 1..{MAX_SIGNALS})"
        )));
    }

    let (read_fd, write_fd) = create_nonblocking_pipe().map_err(|e| {
        pyo3::exceptions::PyOSError::new_err(format!("pipe failed: {e}"))
    })?;

    let sig_idx = signal_num as usize;
    let old_fd = PIPE_WRITE_FDS[sig_idx].swap(write_fd, Ordering::SeqCst);
    if old_fd >= 0 {
        unsafe {
            libc::close(old_fd);
        }
    }

    let action = libc::sigaction {
        sa_sigaction: handle_signal as *const () as libc::sighandler_t,
        sa_mask: unsafe { std::mem::zeroed() },
        sa_flags: libc::SA_RESTART,
        #[cfg(target_os = "linux")]
        sa_restorer: None,
    };

    let ret = unsafe {
        libc::sigaction(signal_num, &action, std::ptr::null_mut())
    };

    if ret != 0 {
        let err = std::io::Error::last_os_error();
        let stored = PIPE_WRITE_FDS[sig_idx].swap(-1, Ordering::SeqCst);
        if stored >= 0 {
            unsafe {
                libc::close(stored);
            }
        }
        unsafe {
            libc::close(read_fd);
        }
        return Err(pyo3::exceptions::PyOSError::new_err(
            format!("sigaction failed: {err}")
        ));
    }

    Ok((read_fd, write_fd))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_nonblocking_pipe() {
        let (r, w) = create_nonblocking_pipe().unwrap();
        assert!(r >= 0);
        assert!(w >= 0);
        unsafe {
            libc::close(r);
            libc::close(w);
        }
    }

    #[test]
    fn test_invalid_signal_num() {
        assert!(register_signal_pipe(0).is_err());
        assert!(register_signal_pipe(-1).is_err());
        assert!(register_signal_pipe(200).is_err());
    }
}