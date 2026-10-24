use crate::error::ProctraceError;
use crate::resources::unix_common;
use crate::resources::{now_ns, ExtendedResourceSnapshot, ResourceSnapshot};
use std::collections::{HashMap, HashSet};

pub fn snapshot() -> Result<ResourceSnapshot, ProctraceError> {
    let (rss_kb, vms_kb) = read_vm_stats()?;
    let open_fds = count_open_fds()?;

    Ok(ResourceSnapshot {
        rss_bytes: rss_kb * 1024,
        vms_bytes: vms_kb * 1024,
        open_fds,
        timestamp_ns: now_ns(),
    })
}

pub fn snapshot_extended(
    cpu: bool,
    io: bool,
    sockets: bool,
    threads_cpu: bool,
) -> Result<ExtendedResourceSnapshot, ProctraceError> {
    let (rss_kb, vms_kb) = read_vm_stats()?;
    let open_fds = count_open_fds()?;

    let mut ext = ExtendedResourceSnapshot {
        rss_bytes: rss_kb * 1024,
        vms_bytes: vms_kb * 1024,
        open_fds,
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
        let (read_b, write_b, rchar, wchar) = read_proc_io();
        ext.io_read_bytes = read_b;
        ext.io_write_bytes = write_b;
        ext.io_rchar_bytes = rchar;
        ext.io_wchar_bytes = wchar;
    }

    if sockets {
        ext.sockets_by_state = collect_sockets_by_state();
    }

    if threads_cpu {
        ext.thread_cpu = collect_thread_cpu();
    }

    Ok(ext)
}

pub fn list_fd_paths() -> Result<Vec<String>, ProctraceError> {
    let dir = std::fs::read_dir("/proc/self/fd").map_err(|e| ProctraceError::Procfs {
        path: "/proc/self/fd",
        source: e,
    })?;

    let mut paths = Vec::new();
    for entry in dir.flatten() {
        if let Ok(target) = std::fs::read_link(entry.path()) {
            paths.push(target.to_string_lossy().into_owned());
        }
    }
    paths.sort();
    Ok(paths)
}

fn read_vm_stats() -> Result<(u64, u64), ProctraceError> {
    let status =
        std::fs::read_to_string("/proc/self/status").map_err(|e| ProctraceError::Procfs {
            path: "/proc/self/status",
            source: e,
        })?;

    let mut rss_kb = 0u64;
    let mut vms_kb = 0u64;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmRSS:") {
            rss_kb = parse_kb("VmRSS", rest)?;
        } else if let Some(rest) = line.strip_prefix("VmSize:") {
            vms_kb = parse_kb("VmSize", rest)?;
        }
        if rss_kb > 0 && vms_kb > 0 {
            break;
        }
    }
    Ok((rss_kb, vms_kb))
}

fn count_open_fds() -> Result<u32, ProctraceError> {
    let count = std::fs::read_dir("/proc/self/fd")
        .map_err(|e| ProctraceError::Procfs {
            path: "/proc/self/fd",
            source: e,
        })?
        .flatten()
        .count() as u32;
    Ok(count)
}

fn read_proc_io() -> (u64, u64, u64, u64) {
    if let Ok(content) = std::fs::read_to_string("/proc/self/io") {
        parse_proc_io(&content)
    } else {
        (0, 0, 0, 0)
    }
}

pub(crate) fn parse_proc_io(content: &str) -> (u64, u64, u64, u64) {
    let mut read_bytes = 0u64;
    let mut write_bytes = 0u64;
    let mut rchar = 0u64;
    let mut wchar = 0u64;

    for line in content.lines() {
        if let Some(rest) = line.strip_prefix("read_bytes:") {
            if let Ok(n) = rest.trim().parse::<u64>() {
                read_bytes = n;
            }
        } else if let Some(rest) = line.strip_prefix("write_bytes:") {
            if let Ok(n) = rest.trim().parse::<u64>() {
                write_bytes = n;
            }
        } else if let Some(rest) = line.strip_prefix("rchar:") {
            if let Ok(n) = rest.trim().parse::<u64>() {
                rchar = n;
            }
        } else if let Some(rest) = line.strip_prefix("wchar:") {
            if let Ok(n) = rest.trim().parse::<u64>() {
                wchar = n;
            }
        }
    }
    (read_bytes, write_bytes, rchar, wchar)
}

fn collect_sockets_by_state() -> HashMap<String, u32> {
    let mut socket_inodes: HashSet<u64> = HashSet::new();

    if let Ok(entries) = std::fs::read_dir("/proc/self/fd") {
        for entry in entries.flatten() {
            if let Ok(target) = std::fs::read_link(entry.path()) {
                let s = target.to_string_lossy();
                if s.starts_with("socket:[") && s.ends_with(']') {
                    let inode_str = &s[8..s.len() - 1];
                    if let Ok(inode) = inode_str.parse::<u64>() {
                        socket_inodes.insert(inode);
                    }
                }
            }
        }
    }

    let mut state_counts: HashMap<String, u32> = HashMap::new();
    if socket_inodes.is_empty() {
        return state_counts;
    }

    parse_tcp_file("/proc/self/net/tcp", &socket_inodes, &mut state_counts);
    parse_tcp_file("/proc/self/net/tcp6", &socket_inodes, &mut state_counts);

    state_counts
}

pub(crate) fn parse_tcp_file(
    path: &str,
    target_inodes: &HashSet<u64>,
    state_counts: &mut HashMap<String, u32>,
) {
    if let Ok(content) = std::fs::read_to_string(path) {
        parse_tcp_content(&content, target_inodes, state_counts);
    }
}

pub(crate) fn parse_tcp_content(
    content: &str,
    target_inodes: &HashSet<u64>,
    state_counts: &mut HashMap<String, u32>,
) {
    for line in content.lines().skip(1) {
        let tokens: Vec<&str> = line.split_whitespace().collect();
        // Column 3 is st (state), Column 9 is inode
        if tokens.len() >= 10 {
            if let Ok(inode) = tokens[9].parse::<u64>() {
                if target_inodes.contains(&inode) {
                    let state_name = tcp_state_name(tokens[3]);
                    *state_counts.entry(state_name.to_string()).or_insert(0) += 1;
                }
            }
        }
    }
}

pub(crate) fn tcp_state_name(st_hex: &str) -> &'static str {
    match st_hex.to_ascii_uppercase().as_str() {
        "01" | "1" => "ESTABLISHED",
        "02" | "2" => "SYN_SENT",
        "03" | "3" => "SYN_RECV",
        "04" | "4" => "FIN_WAIT1",
        "05" | "5" => "FIN_WAIT2",
        "06" | "6" => "TIME_WAIT",
        "07" | "7" => "CLOSE",
        "08" | "8" => "CLOSE_WAIT",
        "09" | "9" => "LAST_ACK",
        "0A" | "10" => "LISTEN",
        "0B" | "11" => "CLOSING",
        "0C" | "12" => "NEW_SYN_RECV",
        _ => "UNKNOWN",
    }
}

fn collect_thread_cpu() -> Vec<(u32, String, u64, u64)> {
    let mut threads = Vec::new();
    let clk_tck = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };

    if let Ok(entries) = std::fs::read_dir("/proc/self/task") {
        for entry in entries.flatten() {
            let stat_path = entry.path().join("stat");
            if let Ok(content) = std::fs::read_to_string(&stat_path) {
                if let Some(t_info) = parse_thread_stat_line(&content, clk_tck) {
                    threads.push(t_info);
                }
            }
        }
    }
    threads
}

pub(crate) fn parse_thread_stat_line(
    content: &str,
    clk_tck: libc::c_long,
) -> Option<(u32, String, u64, u64)> {
    let open_idx = content.find('(')?;
    let close_idx = content.rfind(')')?;
    if close_idx <= open_idx {
        return None;
    }

    let tid = content[..open_idx].trim().parse::<u32>().ok()?;
    let comm = content[open_idx + 1..close_idx].to_string();

    let rest = &content[close_idx + 1..];
    let tokens: Vec<&str> = rest.split_whitespace().collect();
    // After ')', token 0 is state. Field 14 is utime -> index 11. Field 15 is stime -> index 12.
    if tokens.len() <= 12 {
        return None;
    }

    let utime_ticks = tokens[11].parse::<u64>().ok()?;
    let stime_ticks = tokens[12].parse::<u64>().ok()?;

    let tck = if clk_tck > 0 { clk_tck as u64 } else { 100 };
    let utime_ns = utime_ticks
        .saturating_mul(1_000_000_000)
        .checked_div(tck)
        .unwrap_or(0);
    let stime_ns = stime_ticks
        .saturating_mul(1_000_000_000)
        .checked_div(tck)
        .unwrap_or(0);

    Some((tid, comm, utime_ns, stime_ns))
}

fn parse_kb(field: &'static str, value: &str) -> Result<u64, ProctraceError> {
    value
        .split_whitespace()
        .next()
        .ok_or_else(|| ProctraceError::StatusParse {
            field,
            detail: "empty value".into(),
        })?
        .parse::<u64>()
        .map_err(|e| ProctraceError::StatusParse {
            field,
            detail: e.to_string(),
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_kb_values() {
        assert_eq!(parse_kb("VmRSS", "1234 kB").unwrap(), 1234);
    }

    #[test]
    fn parses_proc_io_content() {
        let sample = "rchar: 12345\nwchar: 67890\nsyscr: 10\nsyscw: 20\nread_bytes: 4096\nwrite_bytes: 8192\n";
        let (read_b, write_b, rchar, wchar) = parse_proc_io(sample);
        assert_eq!(read_b, 4096);
        assert_eq!(write_b, 8192);
        assert_eq!(rchar, 12345);
        assert_eq!(wchar, 67890);
    }

    #[test]
    fn parses_tcp_state_and_inodes() {
        let sample = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n   0: 0100007F:235A 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 11931 1 0000000000000000 100 0 0 10 0\n   1: 0100007F:AEC3 00000000:0000 01 00000000:00000000 00:00000000 00000000     0        0 22334 2 0000000000000000 100 0 0 10 0\n";
        let mut target_inodes = HashSet::new();
        target_inodes.insert(11931);
        target_inodes.insert(22334);
        target_inodes.insert(99999);

        let mut counts = HashMap::new();
        parse_tcp_content(sample, &target_inodes, &mut counts);

        assert_eq!(counts.get("LISTEN"), Some(&1));
        assert_eq!(counts.get("ESTABLISHED"), Some(&1));
        assert_eq!(counts.get("UNKNOWN"), None);
    }

    #[test]
    fn parses_thread_stat_with_embedded_parens() {
        let sample = "4567 (worker (pool) 1) S 100 100 100 0 -1 4194304 10 0 0 0 50 25 0 0 20 0 1 0 5442578 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0";
        let parsed = parse_thread_stat_line(sample, 100).expect("failed to parse thread stat");
        assert_eq!(parsed.0, 4567);
        assert_eq!(parsed.1, "worker (pool) 1");
        assert_eq!(parsed.2, 500_000_000); // 50 ticks * 1e9 / 100
        assert_eq!(parsed.3, 250_000_000); // 25 ticks * 1e9 / 100
    }
}
