package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os/exec"
	"sync"
	"time"
)

// StreamCapturer manages the supervised FFmpeg subprocess and video stream ingestion
type StreamCapturer struct {
	cfg           *Config
	mu            sync.Mutex
	cmd           *exec.Cmd
	stdout        io.ReadCloser
	isRunning     bool
	lastFrameTime time.Time
}

// NewStreamCapturer initializes a stream capturer instance
func NewStreamCapturer(cfg *Config) *StreamCapturer {
	return &StreamCapturer{
		cfg: cfg,
	}
}

// Start launches the FFmpeg process with low-latency and low-memory parameters
func (c *StreamCapturer) Start(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.isRunning {
		return nil
	}

	// High-efficiency, memory-optimized FFmpeg arguments
	args := []string{
		"-hide_banner",
		"-loglevel", "error",
		"-nostats",
		"-rtsp_transport", "tcp",
		"-thread_queue_size", "128",
		"-probesize", "1M",
		"-analyzeduration", "1M",
		"-fflags", "nobuffer",
		"-flags", "low_delay",
		"-i", c.cfg.RTSPURL(),
		"-vf", fmt.Sprintf("fps=%d,scale=%d:%d", c.cfg.FPS, c.cfg.FrameWidth, c.cfg.FrameHeight),
		"-f", "image2pipe",
		"-pix_fmt", "yuv420p",
		"-vcodec", "rawvideo",
		"-",
	}

	cmd := exec.CommandContext(ctx, "ffmpeg", args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to open stdout pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		_ = stdout.Close()
		return fmt.Errorf("failed to spawn ffmpeg: %w", err)
	}

	c.cmd = cmd
	c.stdout = stdout
	c.isRunning = true
	c.lastFrameTime = time.Now()

	slog.Info("RTSP stream connected",
		"cam", c.cfg.Channel,
		"fps", c.cfg.FPS,
		"resolution", fmt.Sprintf("%dx%d", c.cfg.FrameWidth, c.cfg.FrameHeight),
		"format", "yuv420p",
	)

	return nil
}

// ReadFrame reads exactly one full YUV420p frame from the FFmpeg pipe
func (c *StreamCapturer) ReadFrame(buf []byte) error {
	c.mu.Lock()
	stdout := c.stdout
	c.mu.Unlock()

	if stdout == nil {
		return fmt.Errorf("capturer stdout is closed")
	}

	_, err := io.ReadFull(stdout, buf)
	if err == nil {
		c.mu.Lock()
		c.lastFrameTime = time.Now()
		c.mu.Unlock()
	}
	return err
}

// Stop cleanly terminates the FFmpeg process
func (c *StreamCapturer) Stop() {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.isRunning {
		return
	}

	if c.stdout != nil {
		_ = c.stdout.Close()
		c.stdout = nil
	}

	if c.cmd != nil && c.cmd.Process != nil {
		_ = c.cmd.Process.Kill()
		_ = c.cmd.Wait()
		c.cmd = nil
	}

	c.isRunning = false
	slog.Info("FFmpeg capture stopped", "cam", c.cfg.Channel)
}

// IsHealthy returns true if the stream has delivered a frame within the last 15 seconds
func (c *StreamCapturer) IsHealthy() bool {
	c.mu.Lock()
	defer c.mu.Unlock()

	if !c.isRunning {
		return false
	}
	return time.Since(c.lastFrameTime) < 15*time.Second
}

// LastFrameAge returns how long ago the last frame was received
func (c *StreamCapturer) LastFrameAge() time.Duration {
	c.mu.Lock()
	defer c.mu.Unlock()
	return time.Since(c.lastFrameTime)
}
