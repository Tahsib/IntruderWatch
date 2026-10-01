package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Prometheus Metrics
var (
	framesCaptured = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "frame_capturer_captured_total",
			Help: "Total frames captured from stream",
		},
		[]string{"camera_id"},
	)
	framesSent = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "frame_capturer_sent_total",
			Help: "Total frames sent to queue",
		},
		[]string{"camera_id"},
	)
	framesSkipped = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "frame_capturer_skipped_total",
			Help: "Total frames skipped (duplicate, rate-limit, lighting-shift, backpressure)",
		},
		[]string{"camera_id", "reason"},
	)
	captureErrors = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "frame_capturer_errors_total",
			Help: "Total capture or processing errors",
		},
		[]string{"camera_id", "error_type"},
	)
	activeMotionBlocks = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "frame_capturer_active_motion_blocks",
			Help: "Current number of active motion macroblocks in frame",
		},
		[]string{"camera_id"},
	)
	motionAnalysisDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "frame_capturer_motion_analysis_seconds",
			Help:    "Time spent analyzing full 1080p frame macroblocks",
			Buckets: []float64{0.0005, 0.001, 0.002, 0.005, 0.010, 0.025},
		},
		[]string{"camera_id"},
	)
)

func init() {
	prometheus.MustRegister(framesCaptured)
	prometheus.MustRegister(framesSent)
	prometheus.MustRegister(framesSkipped)
	prometheus.MustRegister(captureErrors)
	prometheus.MustRegister(activeMotionBlocks)
	prometheus.MustRegister(motionAnalysisDuration)
}

func setupLogger(levelStr string) {
	var level slog.Level
	switch levelStr {
	case "DEBUG":
		level = slog.LevelDebug
	case "WARN":
		level = slog.LevelWarn
	case "ERROR":
		level = slog.LevelError
	default:
		level = slog.LevelInfo
	}

	handler := slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: level,
	})
	slog.SetDefault(slog.New(handler))
}

func main() {
	cfg, err := LoadConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Configuration error: %v\n", err)
		os.Exit(1)
	}

	setupLogger(cfg.LogLevel)

	slog.Info("Starting IntruderWatch Go Ingestion Engine",
		"camera_id", cfg.Channel,
		"fps", cfg.FPS,
		"resolution", fmt.Sprintf("%dx%d", cfg.FrameWidth, cfg.FrameHeight),
		"block_size", cfg.BlockSize,
		"motion_threshold", cfg.MotionThreshold,
		"min_active_blocks", cfg.MinActiveBlocks,
		"post_roll_s", cfg.PostRollDuration.Seconds(),
		"exclude_zones", len(cfg.ExcludeZones),
		"schedule", fmt.Sprintf("%s to %s", cfg.StartTimeStr, cfg.EndTimeStr),
	)

	capturer := NewStreamCapturer(cfg)
	broker := NewBroker(cfg)
	detector := NewMacroblockDetector(cfg)
	snapshotMgr := NewSnapshotManager(cfg)
	pipeline := NewPipeline(cfg, broker, snapshotMgr)

	// 1. Setup HTTP Endpoints (:8001)
	http.Handle("/metrics", promhttp.Handler())

	// Healthcheck endpoint
	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if !cfg.IsWithinTimeFrame(time.Now()) {
			w.WriteHeader(http.StatusOK)
			_, _ = fmt.Fprintln(w, "OK (Sleeping outside scheduled hours)")
			return
		}

		if capturer.IsHealthy() {
			w.WriteHeader(http.StatusOK)
			_, _ = fmt.Fprintln(w, "OK (Stream healthy)")
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = fmt.Fprintf(w, "UNHEALTHY (No frame received in last %.1fs)\n", capturer.LastFrameAge().Seconds())
		}
	})

	// Live Snapshot & Visual Debug Grid endpoint
	http.HandleFunc("/snapshot", func(w http.ResponseWriter, r *http.Request) {
		withGrid := r.URL.Query().Get("grid") == "true" || r.URL.Query().Get("debug") == "true"
		jpegBytes, snapTime, snapErr := snapshotMgr.GetSnapshot(withGrid)
		if snapErr != nil {
			http.Error(w, fmt.Sprintf("Snapshot unavailable: %v", snapErr), http.StatusServiceUnavailable)
			return
		}

		w.Header().Set("Content-Type", "image/jpeg")
		w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
		w.Header().Set("X-Camera-ID", cfg.ChannelStr)
		w.Header().Set("X-Snapshot-Time", snapTime.Format(time.RFC3339))
		_, _ = w.Write(jpegBytes)
	})

	go func() {
		slog.Info("Prometheus metrics, healthcheck, and snapshot listening on :8001")
		if err := http.ListenAndServe(":8001", nil); err != nil {
			slog.Error("HTTP server failed", "error", err)
		}
	}()

	// 2. Setup Context and Graceful Shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		slog.Info("Received termination signal, shutting down...", "signal", sig.String())
		cancel()
		capturer.Stop()
		broker.Close()
	}()

	// 3. Start Background Encoder/Publisher Pipeline Worker
	pipeline.StartWorker(ctx)

	minInterval := time.Duration(float64(time.Second) / float64(cfg.FPS) * 0.9)
	var (
		lastSentTime   time.Time
		lastMotionTime time.Time
		lastMotionBox  *[4]int
		seqCounter     int64
		captured       int64
		lastLogTime    time.Time
		appStart       = time.Now()
	)

	// Main Supervision & Ingestion Loop
	for ctx.Err() == nil {
		if err := broker.Connect(); err != nil {
			slog.Error("Broker connection failure. Retrying in 10s...", "error", err)
			time.Sleep(10 * time.Second)
			continue
		}

		notifyClose := broker.NotifyClose()

	InnerLoop:
		for ctx.Err() == nil {
			// Check broker connectivity
			select {
			case amqpErr := <-notifyClose:
				slog.Warn("Broker disconnected. Reconnecting...", "error", amqpErr)
				capturer.Stop()
				detector.Reset()
				break InnerLoop
			default:
			}

			now := time.Now()
			if cfg.IsWithinTimeFrame(now) {
				if err := capturer.Start(ctx); err != nil {
					slog.Error("Stream capture error. Retrying in 3s...", "error", err)
					captureErrors.WithLabelValues(cfg.ChannelStr, "start_error").Inc()
					time.Sleep(3 * time.Second)
					continue
				}

				// Acquire frame buffer from sync.Pool
				frameItem := pipeline.AcquireFrame()
				if err := capturer.ReadFrame(frameItem.Data); err != nil {
					pipeline.ReleaseFrame(frameItem)
					slog.Error("Network sync lost reading frame. Reconnecting...", "error", err)
					captureErrors.WithLabelValues(cfg.ChannelStr, "network_sync_lost").Inc()
					capturer.Stop()
					detector.Reset()
					time.Sleep(2 * time.Second)
					continue
				}

				// Update live snapshot manager with latest raw frame
				snapshotMgr.UpdateRaw(frameItem.Data)

				captured++
				framesCaptured.WithLabelValues(cfg.ChannelStr).Inc()

				if captured%100 == 0 {
					slog.Info("Ingestion Heartbeat",
						"cam", cfg.Channel,
						"captured", captured,
						"uptime_s", int(time.Since(appStart).Seconds()),
					)
				}

				// Rate limiting check
				if !lastSentTime.IsZero() && now.Sub(lastSentTime) < minInterval {
					framesSkipped.WithLabelValues(cfg.ChannelStr, "rate_limit").Inc()
					pipeline.ReleaseFrame(frameItem)
					continue
				}

				// --- 1080P MACROBLOCK MOTION DETECTION ---
				yPlane := frameItem.Data[:pipeline.ySize]

				startAnalysis := time.Now()
				motion := detector.Analyze(yPlane)
				analysisDur := time.Since(startAnalysis).Seconds()

				motionAnalysisDuration.WithLabelValues(cfg.ChannelStr).Observe(analysisDur)
				activeMotionBlocks.WithLabelValues(cfg.ChannelStr).Set(float64(motion.ActiveBlocks))

				// Reject ambient lighting shifts (e.g. sudden sun emerging from cloud)
				if motion.IsLightingShift {
					framesSkipped.WithLabelValues(cfg.ChannelStr, "lighting_shift").Inc()
					pipeline.ReleaseFrame(frameItem)
					lastSentTime = now
					slog.Debug("Ambient lighting shift suppressed",
						"cam", cfg.Channel,
						"active_blocks", motion.ActiveBlocks,
					)
					continue
				}

				// Evaluate Motion Post-Roll (Hysteresis)
				if motion.HasMotion {
					lastMotionTime = now
					lastMotionBox = motion.MotionBox
				}

				inPostRoll := !motion.HasMotion && !lastMotionTime.IsZero() && now.Sub(lastMotionTime) <= cfg.PostRollDuration
				shouldDispatch := motion.HasMotion || inPostRoll

				if !shouldDispatch {
					framesSkipped.WithLabelValues(cfg.ChannelStr, "duplicate").Inc()
					pipeline.ReleaseFrame(frameItem)
					lastSentTime = now
					continue
				}

				// Motion confirmed or inside post-roll window!
				seqCounter++
				frameItem.Timestamp = now
				frameItem.Sequence = seqCounter
				frameItem.IsPostRoll = inPostRoll

				if motion.MotionBox != nil {
					frameItem.MotionBox = motion.MotionBox
				} else {
					frameItem.MotionBox = lastMotionBox
				}

				if pipeline.Submit(frameItem) {
					lastSentTime = now
					if motion.HasMotion {
						slog.Info("*** MOTION DETECTED ***",
							"cam", cfg.Channel,
							"active_blocks", motion.ActiveBlocks,
							"max_delta", fmt.Sprintf("%.2f", motion.MaxBlockDelta),
							"seq", seqCounter,
						)
					} else {
						slog.Debug("Post-roll frame dispatched", "cam", cfg.Channel, "seq", seqCounter)
					}
				}

				if cfg.FrameSleep > 0 {
					time.Sleep(cfg.FrameSleep)
				}

			} else {
				// Outside scheduled monitoring window
				capturer.Stop()
				detector.Reset()

				if time.Since(lastLogTime) >= time.Hour {
					slog.Info("Service sleeping outside scheduled hours",
						"cam", cfg.Channel,
						"resumes_at", cfg.StartTimeStr,
					)
					lastLogTime = time.Now()
				}
				time.Sleep(60 * time.Second)
			}
		}

		capturer.Stop()
		detector.Reset()
	}

	capturer.Stop()
	broker.Close()
	slog.Info("Ingestion engine terminated cleanly", "cam", cfg.Channel)
}
