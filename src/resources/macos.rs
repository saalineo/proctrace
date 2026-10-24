use crate::error::ProctraceError;
use crate::resources::unix_common;
use crate::resources::{now_ns, ExtendedResourceSnapshot, ResourceSnapshot};
use std::collections::HashMap;

const PROC_PIDLISTTHREADS: libc::c_int = 6;
const PROC_PIDTHREADINFO: libc::c_int = 5;
const PROC_PIDFDSOCKETINFO: libc::c_int = 3;
const SOCKINFO_TCP: libc::c_int = 2;

#[repr(C)]
struct ProcThreadInfo {
    pub pth_user_time: u64,
    pub pth_system_time: u64,
    pub pth_cpu_usage: i32,
    pub pth_policy: i32,
    pub pth_run_state: i32,
    pub pth_flags: i32,
    pub pth_sleep_time: i32,
    pub pth_curpri: i32,
    pub pth_priority: i32,
    pub pth_maxpriority: i32,
    pub pth_name: [libc::c_char; 64],
}

#[repr(C)]
#[derive(Default)]
struct ProcFileInfo {
    pub fi_openflags: u32,
    pub fi_status: u32,
    pub fi_offset: libc::off_t,
    pub fi_type: i32,
    pub rfu_1: i32,
}

#[repr(C)]
#[derive(Default)]
struct VInfoStat {
    pub vst_dev: u32,
    pub vst_mode: u16,
    pub vst_nlink: u16,
    pub vst_ino: u64,
    pub vst_uid: libc::uid_t,
    pub vst_gid: libc::gid_t,
    pub vst_atime: i64,
    pub vst_atimensec: i64,
    pub vst_mtime: i64,
    pub vst_mtimensec: i64,
    pub vst_ctime: i64,
    pub vst_ctimensec: i64,
    pub vst_birthtime: i64,
    pub vst_birthtimensec: i64,
    pub vst_size: libc::off_t,
    pub vst_blocks: i64,
    pub vst_blksize: i32,
    pub vst_flags: u32,
    pub vst_gen: u32,
    pub vst_rdev: u32,
    pub vst_qspare: [i64; 2],
}

#[repr(C)]
#[derive(Default)]
struct SockBufInfo {
    pub sbi_cc: u32,
    pub sbi_hiwat: u32,
    pub sbi_mbcnt: u32,
    pub sbi_mbmax: u32,
    pub sbi_lowat: u32,
    pub sbi_flags: libc::c_short,
    pub sbi_timeo: libc::c_short,
}

#[repr(C)]
#[derive(Copy, Clone)]
struct In4In6Addr {
    pub i46a_pad32: [u32; 3],
    pub i46a_addr4: libc::in_addr,
}

#[repr(C)]
#[derive(Copy, Clone)]
union InSIAddr {
    pub ina_46: In4In6Addr,
    pub ina_6: libc::in6_addr,
}

impl Default for InSIAddr {
    fn default() -> Self {
        InSIAddr {
            ina_46: In4In6Addr {
                i46a_pad32: [0; 3],
                i46a_addr4: libc::in_addr { s_addr: 0 },
            },
        }
    }
}

#[repr(C)]
#[derive(Copy, Clone, Default)]
struct InSIV4 {
    pub in4_tos: libc::c_uchar,
}

#[repr(C)]
#[derive(Copy, Clone, Default)]
struct InSIV6 {
    pub in6_hlim: u8,
    pub in6_cksum: libc::c_int,
    pub in6_ifindex: libc::c_ushort,
    pub in6_hops: libc::c_short,
}

#[repr(C)]
#[derive(Copy, Clone, Default)]
struct InSockInfo {
    pub insi_fport: libc::c_int,
    pub insi_lport: libc::c_int,
    pub insi_gencnt: u64,
    pub insi_flags: u32,
    pub insi_flow: u32,
    pub insi_vflag: u8,
    pub insi_ip_ttl: u8,
    pub rfu_1: u32,
    pub insi_faddr: InSIAddr,
    pub insi_laddr: InSIAddr,
    pub insi_v4: InSIV4,
    pub insi_v6: InSIV6,
}

#[repr(C)]
#[derive(Copy, Clone, Default)]
struct TcpSockInfo {
    pub tcpsi_ini: InSockInfo,
    pub tcpsi_state: libc::c_int,
    pub tcpsi_timer: [libc::c_int; 4],
    pub tcpsi_mss: libc::c_int,
    pub tcpsi_flags: u32,
    pub rfu_1: u32,
    pub tcpsi_tp: u64,
}

#[repr(C)]
#[derive(Copy, Clone)]
union SocketInfoProto {
    pub pri_in: InSockInfo,
    pub pri_tcp: TcpSockInfo,
    pub raw: [u8; 1024],
}

impl Default for SocketInfoProto {
    fn default() -> Self {
        SocketInfoProto {
            pri_in: InSockInfo::default(),
        }
    }
}

#[repr(C)]
struct SocketInfo {
    pub soi_stat: VInfoStat,
    pub soi_so: u64,
    pub soi_pcb: u64,
    pub soi_type: libc::c_int,
    pub soi_protocol: libc::c_int,
    pub soi_family: libc::c_int,
    pub soi_options: libc::c_short,
    pub soi_linger: libc::c_short,
    pub soi_state: libc::c_short,
    pub soi_qlen: libc::c_short,
    pub soi_incqlen: libc::c_short,
    pub soi_qlimit: libc::c_short,
    pub soi_timeo: libc::c_short,
    pub soi_error: libc::c_ushort,
    pub soi_oobmark: u32,
    pub soi_rcv: SockBufInfo,
    pub soi_snd: SockBufInfo,
    pub soi_kind: libc::c_int,
    pub rfu_1: u32,
    pub soi_proto: SocketInfoProto,
}

#[repr(C)]
struct SocketFDInfo {
    pub pfi: ProcFileInfo,
    pub psi: SocketInfo,
}

fn macos_tcp_state_name(state: libc::c_int) -> &'static str {
    match state {
        0 => "CLOSED",
        1 => "LISTEN",
        2 => "SYN_SENT",
        3 => "SYN_RECV",
        4 => "ESTABLISHED",
        5 => "CLOSE_WAIT",
        6 => "FIN_WAIT1",
        7 => "CLOSING",
        8 => "LAST_ACK",
        9 => "FIN_WAIT2",
        10 => "TIME_WAIT",
        _ => "UNKNOWN",
    }
}

fn collect_macos_sockets() -> HashMap<String, u32> {
    let mut state_counts: HashMap<String, u32> = HashMap::new();
    let fds = match list_fd_info() {
        Ok(fds) => fds,
        Err(_) => return state_counts,
    };

    let pid = unsafe { libc::getpid() };
    for fd_info in fds {
        if fd_info.proc_fdtype == libc::PROX_FDTYPE_SOCKET as u32 {
            let mut sock_info: SocketFDInfo = unsafe { std::mem::zeroed() };
            let ret = unsafe {
                libc::proc_pidfdinfo(
                    pid,
                    fd_info.proc_fd,
                    PROC_PIDFDSOCKETINFO,
                    (&mut sock_info as *mut SocketFDInfo).cast::<libc::c_void>(),
                    std::mem::size_of::<SocketFDInfo>() as libc::c_int,
                )
            };
            if ret > 0 && sock_info.psi.soi_kind == SOCKINFO_TCP {
                let tcp_state = unsafe { sock_info.psi.soi_proto.pri_tcp.tcpsi_state };
                let state_str = macos_tcp_state_name(tcp_state);
                *state_counts.entry(state_str.to_string()).or_insert(0) += 1;
            }
        }
    }
    state_counts
}

fn collect_macos_threads() -> Vec<(u64, String, u64, u64)> {
    let mut count = 64;
    let mut tids: Vec<u64> = Vec::new();
    loop {
        tids.resize(count, 0);
        let buf_bytes = (count * std::mem::size_of::<u64>()) as libc::c_int;
        let ret = unsafe {
            libc::proc_pidinfo(
                libc::getpid(),
                PROC_PIDLISTTHREADS,
                0,
                tids.as_mut_ptr().cast::<libc::c_void>(),
                buf_bytes,
            )
        };
        if ret <= 0 {
            return Vec::new();
        }
        let actual_tids = (ret as usize) / std::mem::size_of::<u64>();
        if (ret as usize) < (buf_bytes as usize) {
            tids.truncate(actual_tids);
            break;
        }
        count *= 2;
        if count > 8192 {
            tids.truncate(actual_tids);
            break;
        }
    }

    let mut threads = Vec::with_capacity(tids.len());
    for tid in tids {
        let mut tinfo: ProcThreadInfo = unsafe { std::mem::zeroed() };
        let ret = unsafe {
            libc::proc_pidinfo(
                libc::getpid(),
                PROC_PIDTHREADINFO,
                tid,
                (&mut tinfo as *mut ProcThreadInfo).cast::<libc::c_void>(),
                std::mem::size_of::<ProcThreadInfo>() as libc::c_int,
            )
        };
        if ret == std::mem::size_of::<ProcThreadInfo>() as libc::c_int {
            let name = unsafe { std::ffi::CStr::from_ptr(tinfo.pth_name.as_ptr()) }
                .to_str()
                .unwrap_or("")
                .to_string();
            threads.push((tid, name, tinfo.pth_user_time, tinfo.pth_system_time));
        }
    }
    threads
}

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
    sockets: bool,
    threads_cpu: bool,
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

    if sockets {
        ext.sockets_by_state = collect_macos_sockets();
    }

    if threads_cpu {
        ext.thread_cpu = collect_macos_threads();
    }

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
