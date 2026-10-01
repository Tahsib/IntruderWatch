package main

import (
	"bytes"
	"fmt"
	"image"
	"image/jpeg"
	"sync"
	"time"
)

// SnapshotManager provides thread-safe access to live camera snapshots and visual debug overlays
type SnapshotManager struct {
	mu        sync.RWMutex
	jpegBytes []byte
	rawFrame  []byte
	updatedAt time.Time
	frameW    int
	frameH    int
	ySize     int
	cSize     int
	jpegOpts  *jpeg.Options
	zones     []Zone
}

// NewSnapshotManager initializes the snapshot cache
func NewSnapshotManager(cfg *Config) *SnapshotManager {
	ySize := cfg.FrameWidth * cfg.FrameHeight
	cSize := (cfg.FrameWidth / 2) * (cfg.FrameHeight / 2)
	frameSize := ySize + 2*cSize

	return &SnapshotManager{
		frameW:   cfg.FrameWidth,
		frameH:   cfg.FrameHeight,
		ySize:    ySize,
		cSize:    cSize,
		rawFrame: make([]byte, frameSize),
		jpegOpts: &jpeg.Options{Quality: cfg.JPEGQuality},
		zones:    cfg.ExcludeZones,
	}
}

// UpdateRaw updates the latest raw frame buffer (called on every captured frame)
func (s *SnapshotManager) UpdateRaw(raw []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	copy(s.rawFrame, raw)
	s.updatedAt = time.Now()
}

// UpdateJPEG updates the cached pre-encoded JPEG buffer (called when a motion frame is encoded)
func (s *SnapshotManager) UpdateJPEG(jpegBytes []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.jpegBytes = make([]byte, len(jpegBytes))
	copy(s.jpegBytes, jpegBytes)
	s.updatedAt = time.Now()
}

// GetSnapshot returns a live JPEG image, optionally with coordinate grid and zone overlays
func (s *SnapshotManager) GetSnapshot(withGrid bool) ([]byte, time.Time, error) {
	s.mu.RLock()
	hasRaw := s.updatedAt.After(time.Time{})
	cachedJPEG := s.jpegBytes
	cacheTime := s.updatedAt
	rawCopy := make([]byte, len(s.rawFrame))
	copy(rawCopy, s.rawFrame)
	s.mu.RUnlock()

	if !hasRaw {
		return nil, time.Time{}, fmt.Errorf("no frame captured yet")
	}

	// Hot path: Fast return of cached JPEG if not requesting grid overlay and cache is fresh (< 4s)
	if !withGrid && len(cachedJPEG) > 0 && time.Since(cacheTime) < 4*time.Second {
		return cachedJPEG, cacheTime, nil
	}

	// On-demand path: Render from raw frame
	if withGrid {
		s.drawVisualOverlay(rawCopy)
	}

	img := &image.YCbCr{
		Y:              rawCopy[:s.ySize],
		Cb:             rawCopy[s.ySize : s.ySize+s.cSize],
		Cr:             rawCopy[s.ySize+s.cSize:],
		YStride:        s.frameW,
		CStride:        s.frameW / 2,
		SubsampleRatio: image.YCbCrSubsampleRatio420,
		Rect:           image.Rect(0, 0, s.frameW, s.frameH),
	}

	var buf bytes.Buffer
	buf.Grow(256 * 1024)
	if err := jpeg.Encode(&buf, img, s.jpegOpts); err != nil {
		return nil, time.Time{}, fmt.Errorf("snapshot encode failed: %w", err)
	}

	return buf.Bytes(), cacheTime, nil
}

// drawVisualOverlay burns pixel coordinate lines and exclusion zones directly into the YUV buffer
func (s *SnapshotManager) drawVisualOverlay(raw []byte) {
	w, h := s.frameW, s.frameH

	// 1. Draw coordinate grid lines every 200px (high-contrast white: Y=255)
	for x := 200; x < w; x += 200 {
		for y := 0; y < h; y++ {
			raw[y*w+x] = 240
		}
	}
	for y := 200; y < h; y += 200 {
		for x := 0; x < w; x++ {
			raw[y*w+x] = 240
		}
	}

	// 2. Draw exclusion zones with 4px border and crosshatch pattern
	for _, zone := range s.zones {
		x1, y1, x2, y2 := zone.X1, zone.Y1, zone.X2, zone.Y2
		if x2 > w {
			x2 = w
		}
		if y2 > h {
			y2 = h
		}

		// Outer border (4px thick)
		for t := 0; t < 4; t++ {
			// Top & bottom edges
			for x := x1; x < x2; x++ {
				if y1+t < h {
					raw[(y1+t)*w+x] = 255
				}
				if y2-1-t >= 0 && y2-1-t < h {
					raw[(y2-1-t)*w+x] = 255
				}
			}
			// Left & right edges
			for y := y1; y < y2; y++ {
				if x1+t < w {
					raw[y*w+(x1+t)] = 255
				}
				if x2-1-t >= 0 && x2-1-t < w {
					raw[y*w+(x2-1-t)] = 255
				}
			}
		}

		// Interior diagonal crosshatch
		for y := y1 + 4; y < y2-4; y++ {
			for x := x1 + 4; x < x2-4; x++ {
				if (x+y)%24 == 0 || (x-y)%24 == 0 {
					raw[y*w+x] = 220
				}
			}
		}
	}
}
