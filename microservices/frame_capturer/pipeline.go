package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"image"
	"image/jpeg"
	"log/slog"
	"sync"
	"time"
)

// FrameItem carries frame data across pipeline stages
type FrameItem struct {
	Data       []byte
	Timestamp  time.Time
	Sequence   int64
	MotionBox  *[4]int
	IsPostRoll bool
}

// FramePayload is the JSON contract expected by downstream human_detector
type FramePayload struct {
	Camera     int     `json:"camera"`
	Hash       string  `json:"hash"`
	Image      string  `json:"image"`
	MotionBox  *[4]int `json:"motion_box,omitempty"`
	IsPostRoll bool    `json:"is_post_roll,omitempty"`
}

// Pipeline coordinates zero-allocation frame processing and background publishing
type Pipeline struct {
	cfg         *Config
	broker      *Broker
	snapshotMgr *SnapshotManager
	frameChan   chan *FrameItem
	framePool   sync.Pool
	bufPool     sync.Pool
	jpegOpts    *jpeg.Options
	ycbcrImg    *image.YCbCr
	ySize       int
	cSize       int
}

// NewPipeline initializes object pools and worker channels
func NewPipeline(cfg *Config, broker *Broker, snapshotMgr *SnapshotManager) *Pipeline {
	frameSize := cfg.FrameWidth*cfg.FrameHeight + 2*(cfg.FrameWidth/2)*(cfg.FrameHeight/2)
	ySize := cfg.FrameWidth * cfg.FrameHeight
	cSize := (cfg.FrameWidth / 2) * (cfg.FrameHeight / 2)

	p := &Pipeline{
		cfg:         cfg,
		broker:      broker,
		snapshotMgr: snapshotMgr,
		frameChan:   make(chan *FrameItem, 2), // Bounded buffer to prevent latency drift
		ySize:       ySize,
		cSize:       cSize,
		jpegOpts:    &jpeg.Options{Quality: cfg.JPEGQuality},
		ycbcrImg: &image.YCbCr{
			YStride:        cfg.FrameWidth,
			CStride:        cfg.FrameWidth / 2,
			SubsampleRatio: image.YCbCrSubsampleRatio420,
			Rect:           image.Rect(0, 0, cfg.FrameWidth, cfg.FrameHeight),
		},
		framePool: sync.Pool{
			New: func() any {
				return &FrameItem{
					Data: make([]byte, frameSize),
				}
			},
		},
		bufPool: sync.Pool{
			New: func() any {
				buf := new(bytes.Buffer)
				buf.Grow(512 * 1024)
				return buf
			},
		},
	}

	return p
}

// AcquireFrame borrows a pre-allocated FrameItem from the pool
func (p *Pipeline) AcquireFrame() *FrameItem {
	item := p.framePool.Get().(*FrameItem)
	item.MotionBox = nil
	item.IsPostRoll = false
	return item
}

// ReleaseFrame returns a FrameItem back to the pool
func (p *Pipeline) ReleaseFrame(item *FrameItem) {
	item.MotionBox = nil
	p.framePool.Put(item)
}

// Submit enqueues a frame for async JPEG compression and AMQP publishing.
// If the worker is saturated, drops the frame gracefully to maintain live lockstep.
func (p *Pipeline) Submit(item *FrameItem) bool {
	select {
	case p.frameChan <- item:
		return true
	default:
		// Worker is busy: drop frame to prevent stream buffering latency
		framesSkipped.WithLabelValues(p.cfg.ChannelStr, "backpressure").Inc()
		p.ReleaseFrame(item)
		return false
	}
}

// StartWorker runs the background compression and dispatch loop
func (p *Pipeline) StartWorker(ctx context.Context) {
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case item, ok := <-p.frameChan:
				if !ok {
					return
				}
				p.processFrame(ctx, item)
			}
		}
	}()
}

func (p *Pipeline) processFrame(ctx context.Context, item *FrameItem) {
	defer p.ReleaseFrame(item)

	startEncode := time.Now()

	// Assign Y, Cb, Cr planes directly to image without reallocation
	p.ycbcrImg.Y = item.Data[:p.ySize]
	p.ycbcrImg.Cb = item.Data[p.ySize : p.ySize+p.cSize]
	p.ycbcrImg.Cr = item.Data[p.ySize+p.cSize:]

	buf := p.bufPool.Get().(*bytes.Buffer)
	buf.Reset()
	defer p.bufPool.Put(buf)

	if err := jpeg.Encode(buf, p.ycbcrImg, p.jpegOpts); err != nil {
		slog.Error("JPEG encoding error", "error", err, "cam", p.cfg.Channel)
		captureErrors.WithLabelValues(p.cfg.ChannelStr, "encoding_failed").Inc()
		return
	}

	jpegBytes := buf.Bytes()

	// Update live snapshot cache with freshly encoded JPEG
	if p.snapshotMgr != nil {
		p.snapshotMgr.UpdateJPEG(jpegBytes)
	}

	hashSum := sha256.Sum256(jpegBytes)
	hashHex := hex.EncodeToString(hashSum[:])
	base64Img := base64.StdEncoding.EncodeToString(jpegBytes)

	payload := FramePayload{
		Camera:     p.cfg.Channel,
		Hash:       hashHex,
		Image:      base64Img,
		MotionBox:  item.MotionBox,
		IsPostRoll: item.IsPostRoll,
	}

	payloadBytes, jsonErr := json.Marshal(payload)
	if jsonErr != nil {
		slog.Error("JSON marshal failed", "error", jsonErr, "cam", p.cfg.Channel)
		return
	}

	if err := p.broker.Publish(ctx, payloadBytes); err != nil {
		slog.Error("AMQP publish failed", "error", err, "cam", p.cfg.Channel)
		captureErrors.WithLabelValues(p.cfg.ChannelStr, "publish_failed").Inc()
		return
	}

	encodeDur := time.Since(startEncode)
	framesSent.WithLabelValues(p.cfg.ChannelStr).Inc()

	slog.Debug("Frame dispatched",
		"cam", p.cfg.Channel,
		"seq", item.Sequence,
		"post_roll", item.IsPostRoll,
		"encode_ms", encodeDur.Milliseconds(),
		"size_kb", len(jpegBytes)/1024,
	)
}
