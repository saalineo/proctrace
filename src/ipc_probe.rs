use pyo3::prelude::*;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::Mutex;
#[pyclass(weakref)]
pub struct IpcStats {
    name: String,
    latency_ring: Mutex<VecDeque<u64>>,
    ring_capacity: usize,
    total_messages: AtomicU64,
    peak_depth: AtomicU32,
}

#[pymethods]
impl IpcStats {
    #[new]
    pub fn new(name: String, ring_capacity: usize) -> Self {
        IpcStats {
            name,
            latency_ring: Mutex::new(VecDeque::with_capacity(ring_capacity)),
            ring_capacity,
            total_messages: AtomicU64::new(0),
            peak_depth: AtomicU32::new(0),
        }
    }

    pub fn record_latency_us(&self, us: u64) {
        let mut ring = self.latency_ring.lock().unwrap();
        if ring.len() >= self.ring_capacity {
            ring.pop_front();
        }
        ring.push_back(us);
        self.total_messages.fetch_add(1, Ordering::Relaxed);
    }
    pub fn record_depth(&self, depth: u32) {
        let mut current = self.peak_depth.load(Ordering::Relaxed);
        while depth > current {
            match self.peak_depth.compare_exchange_weak(
                current,
                depth,
                Ordering::SeqCst,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(actual) => current = actual,
            }
        }
    }

    pub fn avg_latency_us(&self) -> f64 {
        let ring = self.latency_ring.lock().unwrap();
        if ring.is_empty() {
            return 0.0;
        }
        ring.iter().sum::<u64>() as f64 / ring.len() as f64
    }

    pub fn p99_latency_us(&self) -> f64 {
        let ring = self.latency_ring.lock().unwrap();
        if ring.is_empty() {
            return 0.0;
        }
        let mut sorted: Vec<u64> = ring.iter().copied().collect();
        sorted.sort_unstable();
        let idx = (sorted.len() as f64 * 0.99) as usize;
        sorted[idx.min(sorted.len() - 1)] as f64
    }

    pub fn total_messages(&self) -> u64 {
        self.total_messages.load(Ordering::Relaxed)
    }

    pub fn peak_depth(&self) -> u32 {
        self.peak_depth.load(Ordering::Relaxed)
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn report(&self) -> String {
        format!(
            "{}: {} msgs | avg {:.1}µs | p99 {:.1}µs | peak depth {}",
            self.name,
            self.total_messages(),
            self.avg_latency_us(),
            self.p99_latency_us(),
            self.peak_depth(),
        )
    }
}

/// Statistics for a traced socket channel.
#[pyclass(weakref)]
pub struct SocketStats {
    name: String,
    bytes_sent: AtomicU64,
    bytes_recv: AtomicU64,
    send_latency_ring: Mutex<VecDeque<u64>>, // send latency in microseconds
    ring_capacity: usize,
    peak_recv_buffer_pct: AtomicU32, // 0-100
}

#[pymethods]
impl SocketStats {
    #[new]
    pub fn new(name: String, ring_capacity: usize) -> Self {
        SocketStats {
            name,
            bytes_sent: AtomicU64::new(0),
            bytes_recv: AtomicU64::new(0),
            send_latency_ring: Mutex::new(VecDeque::with_capacity(ring_capacity)),
            ring_capacity,
            peak_recv_buffer_pct: AtomicU32::new(0),
        }
    }

    pub fn record_send(&self, bytes: u64, latency_us: u64) {
        self.bytes_sent.fetch_add(bytes, Ordering::Relaxed);
        let mut ring = self.send_latency_ring.lock().unwrap();
        if ring.len() >= self.ring_capacity {
            ring.pop_front();
        }
        ring.push_back(latency_us);
    }

    pub fn record_recv(&self, bytes: u64, buffer_used_pct: u32) {
        self.bytes_recv.fetch_add(bytes, Ordering::Relaxed);
        // Atomic max
        let mut current = self.peak_recv_buffer_pct.load(Ordering::Relaxed);
        while buffer_used_pct > current {
            match self.peak_recv_buffer_pct.compare_exchange_weak(
                current,
                buffer_used_pct,
                Ordering::SeqCst,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(actual) => current = actual,
            }
        }
    }

    pub fn bytes_sent(&self) -> u64 {
        self.bytes_sent.load(Ordering::Relaxed)
    }

    pub fn bytes_recv(&self) -> u64 {
        self.bytes_recv.load(Ordering::Relaxed)
    }

    pub fn peak_recv_buffer_pct(&self) -> u32 {
        self.peak_recv_buffer_pct.load(Ordering::Relaxed)
    }

    pub fn avg_send_latency_us(&self) -> f64 {
        let ring = self.send_latency_ring.lock().unwrap();
        if ring.is_empty() {
            return 0.0;
        }
        ring.iter().sum::<u64>() as f64 / ring.len() as f64
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn report(&self) -> String {
        format!(
            "{}: sent {}B recv {}B | send avg {:.1}µs | recv buf peak {}%",
            self.name,
            self.bytes_sent(),
            self.bytes_recv(),
            self.avg_send_latency_us(),
            self.peak_recv_buffer_pct(),
        )
    }
}
