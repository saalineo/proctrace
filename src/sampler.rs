use crate::resources::snapshot;
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// Default maximum number of samples held in memory before decimation triggers.
pub const DEFAULT_SAMPLE_CAPACITY: usize = 65_536;

/// A discrete resource snapshot taken during background sampling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sample {
    /// Monotonic elapsed time in nanoseconds since sampling started.
    pub t_ns: u64,
    /// Resident Set Size in bytes.
    pub rss: u64,
    /// Virtual Memory Size in bytes.
    pub vms: u64,
    /// Number of open file descriptors.
    pub fds: u32,
}

/// Decimate samples in-place by retaining every second sample (indices 0, 2, 4, ...).
///
/// Preserves vector capacity and bounds memory usage over long durations without reallocating.
fn decimate_in_place(samples: &mut Vec<Sample>) {
    let mut write_idx = 0;
    for read_idx in (0..samples.len()).step_by(2) {
        samples[write_idx] = samples[read_idx];
        write_idx += 1;
    }
    samples.truncate(write_idx);
}

/// Shared thread state between the controlling thread and the background sampling worker.
pub(crate) struct Shared {
    pub stop: AtomicBool,
    pub peak_rss: AtomicU64,
    pub peak_at_ns: AtomicU64,
    pub samples: Mutex<Vec<Sample>>,
    pub cap: usize,
}

/// Threaded background sampler that records high-frequency resource metrics into a bounded buffer.
#[pyclass]
pub struct BackgroundSampler {
    pub(crate) shared: Arc<Shared>,
    handle: Option<thread::JoinHandle<()>>,
}

#[pymethods]
impl BackgroundSampler {
    /// Create a new `BackgroundSampler` with an optional sample buffer capacity.
    #[new]
    #[pyo3(signature = (cap = None))]
    pub fn new(cap: Option<usize>) -> Self {
        let capacity = cap.unwrap_or(DEFAULT_SAMPLE_CAPACITY).max(2);
        BackgroundSampler {
            shared: Arc::new(Shared {
                stop: AtomicBool::new(false),
                peak_rss: AtomicU64::new(0),
                peak_at_ns: AtomicU64::new(0),
                samples: Mutex::new(Vec::with_capacity(capacity)),
                cap: capacity,
            }),
            handle: None,
        }
    }

    /// Start background sampling at the specified polling interval (in milliseconds).
    pub fn start(&mut self, interval_ms: u64) {
        if self.handle.is_some() {
            self.stop();
        }
        self.shared.stop.store(false, Ordering::SeqCst);
        self.shared.peak_rss.store(0, Ordering::SeqCst);
        self.shared.peak_at_ns.store(0, Ordering::SeqCst);
        {
            let mut guard = self.shared.samples.lock().unwrap_or_else(|e| e.into_inner());
            guard.clear();
        }

        let start_instant = Instant::now();

        // Capture initial baseline sample synchronously at t = 0
        if let Ok(snap) = snapshot() {
            let rss = snap.rss_bytes;
            self.shared.peak_rss.store(rss, Ordering::Relaxed);
            self.shared.peak_at_ns.store(0, Ordering::Relaxed);
            let mut guard = self.shared.samples.lock().unwrap_or_else(|e| e.into_inner());
            guard.push(Sample {
                t_ns: 0,
                rss,
                vms: snap.vms_bytes,
                fds: snap.open_fds,
            });
        }

        let shared = Arc::clone(&self.shared);
        let interval = Duration::from_millis(interval_ms.max(1));

        self.handle = Some(thread::spawn(move || {
            let mut stride: usize = 1;
            let mut ticks_since_sample: usize = 0;

            while !shared.stop.load(Ordering::Relaxed) {
                thread::park_timeout(interval);
                if shared.stop.load(Ordering::Relaxed) {
                    break;
                }

                if let Ok(snap) = snapshot() {
                    let elapsed_ns = start_instant.elapsed().as_nanos() as u64;
                    let rss = snap.rss_bytes;

                    let current_peak = shared.peak_rss.load(Ordering::Relaxed);
                    if rss > current_peak {
                        shared.peak_rss.store(rss, Ordering::Relaxed);
                        shared.peak_at_ns.store(elapsed_ns, Ordering::Relaxed);
                    }

                    ticks_since_sample += 1;
                    if ticks_since_sample >= stride {
                        ticks_since_sample = 0;
                        let mut guard = shared.samples.lock().unwrap_or_else(|e| e.into_inner());
                        let should_push = guard.last().is_none_or(|last| elapsed_ns > last.t_ns);
                        if should_push {
                            guard.push(Sample {
                                t_ns: elapsed_ns,
                                rss,
                                vms: snap.vms_bytes,
                                fds: snap.open_fds,
                            });
                            if guard.len() >= shared.cap {
                                decimate_in_place(&mut guard);
                                stride = stride.saturating_mul(2);
                            }
                        }
                    }
                }
            }

            // Capture final sample upon stop if elapsed time has advanced
            if let Ok(snap) = snapshot() {
                let elapsed_ns = start_instant.elapsed().as_nanos() as u64;
                let rss = snap.rss_bytes;

                let current_peak = shared.peak_rss.load(Ordering::Relaxed);
                if rss > current_peak {
                    shared.peak_rss.store(rss, Ordering::Relaxed);
                    shared.peak_at_ns.store(elapsed_ns, Ordering::Relaxed);
                }

                let mut guard = shared.samples.lock().unwrap_or_else(|e| e.into_inner());
                let should_push = guard.last().is_none_or(|last| elapsed_ns > last.t_ns);
                if should_push {
                    guard.push(Sample {
                        t_ns: elapsed_ns,
                        rss,
                        vms: snap.vms_bytes,
                        fds: snap.open_fds,
                    });
                    if guard.len() >= shared.cap {
                        decimate_in_place(&mut guard);
                    }
                }
            }
        }));
    }

    /// Stop sampling and immediately unpark the background worker. Returns the peak RSS observed.
    pub fn stop(&mut self) -> u64 {
        self.shared.stop.store(true, Ordering::SeqCst);
        if let Some(handle) = self.handle.take() {
            handle.thread().unpark();
            let _ = handle.join();
        }
        self.shared.peak_rss.load(Ordering::Acquire)
    }

    /// Default sample buffer capacity exposed to Python.
    #[classattr]
    pub const DEFAULT_CAPACITY: usize = DEFAULT_SAMPLE_CAPACITY;

    /// Return the timestamp (nanoseconds relative to start) at which peak RSS was first reached.
    pub fn peak_at_ns(&self) -> u64 {
        self.shared.peak_at_ns.load(Ordering::Acquire)
    }

    /// Return the peak RSS observed in bytes.
    pub fn peak_rss(&self) -> u64 {
        self.shared.peak_rss.load(Ordering::Acquire)
    }

    /// Return a copy of all recorded time-series samples as `(t_ns, rss, vms, fds)` tuples.
    ///
    /// Copies the sample buffer under lock with a minimal critical section duration,
    /// transforming into primitive Python tuples entirely outside the lock to eliminate GIL stalls.
    pub fn samples(&self) -> Vec<(u64, u64, u64, u32)> {
        let raw = {
            let guard = self.shared.samples.lock().unwrap_or_else(|e| e.into_inner());
            guard.clone()
        };
        raw.into_iter()
            .map(|s| (s.t_ns, s.rss, s.vms, s.fds))
            .collect()
    }
}

impl Default for BackgroundSampler {
    fn default() -> Self {
        Self::new(None)
    }
}

impl Drop for BackgroundSampler {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sampler_start_stop() {
        let mut sampler = BackgroundSampler::new(None);
        sampler.start(10);
        thread::sleep(Duration::from_millis(50));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_restart_without_leak() {
        let mut sampler = BackgroundSampler::new(None);
        sampler.start(10);
        sampler.start(10);
        thread::sleep(Duration::from_millis(20));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_zero_interval_does_not_panic() {
        let mut sampler = BackgroundSampler::new(None);
        sampler.start(0);
        thread::sleep(Duration::from_millis(20));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_drop_cleans_up_thread() {
        let mut sampler = BackgroundSampler::new(None);
        let shared = Arc::clone(&sampler.shared);
        sampler.start(10);
        assert!(!shared.stop.load(Ordering::SeqCst));
        drop(sampler);
        assert!(shared.stop.load(Ordering::SeqCst));
    }

    #[test]
    fn test_decimate_in_place_retains_even_indices() {
        let mut samples = vec![
            Sample { t_ns: 10, rss: 100, vms: 200, fds: 3 },
            Sample { t_ns: 20, rss: 110, vms: 210, fds: 3 },
            Sample { t_ns: 30, rss: 120, vms: 220, fds: 3 },
            Sample { t_ns: 40, rss: 130, vms: 230, fds: 3 },
        ];
        decimate_in_place(&mut samples);
        assert_eq!(samples.len(), 2);
        assert_eq!(samples[0].t_ns, 10);
        assert_eq!(samples[1].t_ns, 30);
    }

    #[test]
    fn test_sampler_samples_monotonic() {
        let mut sampler = BackgroundSampler::new(None);
        sampler.start(5);
        thread::sleep(Duration::from_millis(30));
        sampler.stop();
        let samples = sampler.samples();
        assert!(samples.len() >= 2, "Expected at least 2 samples, got {}", samples.len());
        for window in samples.windows(2) {
            assert!(window[0].0 <= window[1].0, "Timestamps must be monotonically increasing");
        }
    }

    #[test]
    fn test_sampler_decimation_bounds_capacity() {
        let cap = 4;
        let mut sampler = BackgroundSampler::new(Some(cap));
        sampler.start(2);
        thread::sleep(Duration::from_millis(60));
        sampler.stop();
        let samples = sampler.samples();
        assert!(
            samples.len() <= cap,
            "Samples count {} exceeded cap {}",
            samples.len(),
            cap
        );
        assert!(!samples.is_empty(), "Samples should not be empty");
        for window in samples.windows(2) {
            assert!(window[0].0 <= window[1].0, "Timestamps must be monotonically increasing");
        }
    }

    #[test]
    fn test_sampler_stop_promptness_via_unpark() {
        let mut sampler = BackgroundSampler::new(None);
        // Start with a large interval (1000ms)
        sampler.start(1000);
        thread::sleep(Duration::from_millis(10));
        let start = Instant::now();
        sampler.stop();
        let elapsed = start.elapsed();
        // stop() should wake immediately (< 50ms) via unpark
        assert!(
            elapsed < Duration::from_millis(50),
            "stop() took {:?}, expected < 50ms",
            elapsed
        );
    }
}
