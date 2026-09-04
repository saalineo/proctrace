use crate::resources::snapshot;
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

#[pyclass]
pub struct BackgroundSampler {
    stop_flag: Arc<AtomicBool>, // Main thread sets  true so sampler thread exits
    peak_rss: Arc<AtomicU64>,   // Peak RSS seen since start() Read by main thread after stop()
    handle: Option<thread::JoinHandle<()>>, // Handle to the spawned thread None if not started yet
}

#[pymethods]
impl BackgroundSampler {
    #[new]
    pub fn new() -> Self {
        BackgroundSampler {
            stop_flag: Arc::new(AtomicBool::new(false)),
            peak_rss: Arc::new(AtomicU64::new(0)),
            handle: None,
        }
    }

    pub fn start(&mut self, interval_ms: u64) {
        if self.handle.is_some() {
            self.stop();
        }
        self.stop_flag.store(false, Ordering::SeqCst);
        self.peak_rss.store(0, Ordering::SeqCst);

        let stop = Arc::clone(&self.stop_flag);
        let peak = Arc::clone(&self.peak_rss);
        let interval = Duration::from_millis(interval_ms.max(1));

        self.handle = Some(thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                if let Ok(snap) = snapshot() {
                    peak.fetch_max(snap.rss_bytes, Ordering::Relaxed);
                }
                thread::sleep(interval);
            }
        }));
    }

    pub fn stop(&mut self) -> u64 {
        self.stop_flag.store(true, Ordering::SeqCst);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
        self.peak_rss.load(Ordering::SeqCst)
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
        let mut sampler = BackgroundSampler::new();
        sampler.start(10);
        thread::sleep(Duration::from_millis(50));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_restart_without_leak() {
        let mut sampler = BackgroundSampler::new();
        sampler.start(10);
        sampler.start(10);
        thread::sleep(Duration::from_millis(20));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_zero_interval_does_not_panic() {
        let mut sampler = BackgroundSampler::new();
        sampler.start(0);
        thread::sleep(Duration::from_millis(20));
        let peak = sampler.stop();
        assert!(peak > 0);
    }

    #[test]
    fn test_sampler_drop_cleans_up_thread() {
        let stop_flag = Arc::new(AtomicBool::new(false));
        {
            let mut sampler = BackgroundSampler::new();
            sampler.stop_flag = Arc::clone(&stop_flag);
            sampler.start(10);
            assert!(!stop_flag.load(Ordering::SeqCst));
        }
        assert!(stop_flag.load(Ordering::SeqCst));
    }
}
