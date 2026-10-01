package main

import (
	"testing"
)

func TestParseExcludeZones(t *testing.T) {
	frameW, frameH := 1920, 1080
	input := "top-left-clock; 100,200,400,500; bottom-right-clock"
	zones := parseExcludeZones(input, frameW, frameH)

	if len(zones) != 3 {
		t.Fatalf("expected 3 zones, got %d", len(zones))
	}

	// Check top-left preset
	if zones[0].Name != "top-left-clock" || zones[0].X1 != 0 || zones[0].Y1 != 0 || zones[0].X2 != 480 || zones[0].Y2 != 96 {
		t.Errorf("unexpected top-left-clock zone: %+v", zones[0])
	}

	// Check custom zone
	if zones[1].X1 != 100 || zones[1].Y1 != 200 || zones[1].X2 != 400 || zones[1].Y2 != 500 {
		t.Errorf("unexpected custom zone: %+v", zones[1])
	}

	// Check bottom-right preset
	if zones[2].Name != "bottom-right-clock" || zones[2].X1 != 1440 || zones[2].Y1 != 984 || zones[2].X2 != 1920 || zones[2].Y2 != 1080 {
		t.Errorf("unexpected bottom-right-clock zone: %+v", zones[2])
	}
}

func TestMacroblockDetectorMasking(t *testing.T) {
	cfg := &Config{
		FrameWidth:         1920,
		FrameHeight:        1080,
		BlockSize:          32,
		MotionThreshold:    8.0,
		MinActiveBlocks:    2,
		LightingShiftRatio: 0.75,
		ExcludeZones: []Zone{
			{Name: "top-left", X1: 0, Y1: 0, X2: 480, Y2: 96},
		},
	}

	detector := NewMacroblockDetector(cfg)

	// Verify masked blocks count
	// Width 480 / 32 = 15 blocks, Height 96 / 32 = 3 blocks -> 15 * 3 = 45 blocks masked
	expectedMasked := (480 / 32) * (96 / 32)
	actualMasked := detector.totalBlocks - detector.effectiveBlocks
	if actualMasked != expectedMasked {
		t.Errorf("expected %d masked blocks, got %d", expectedMasked, actualMasked)
	}

	f1 := make([]byte, 1920*1080)
	f2 := make([]byte, 1920*1080)

	// Initial reference frame
	res1 := detector.Analyze(f1)
	if res1.HasMotion {
		t.Errorf("first frame should not trigger motion")
	}

	// Case 1: Motion ONLY in masked zone (e.g. clock digits changing)
	for y := 10; y < 50; y++ {
		for x := 10; x < 200; x++ {
			f2[y*1920+x] = 200
		}
	}
	res2 := detector.Analyze(f2)
	if res2.HasMotion || res2.ActiveBlocks > 0 {
		t.Errorf("motion in masked zone should be ignored, got active=%d, motion=%v", res2.ActiveBlocks, res2.HasMotion)
	}

	// Case 2: Motion in unmasked region (real intruder)
	copy(f2, f1)
	for y := 500; y < 600; y++ {
		for x := 800; x < 900; x++ {
			f2[y*1920+x] = 200
		}
	}
	res3 := detector.Analyze(f2)
	if !res3.HasMotion {
		t.Errorf("real motion outside mask should trigger, got active=%d, motion=%v", res3.ActiveBlocks, res3.HasMotion)
	}
	if res3.MotionBox == nil {
		t.Fatalf("expected non-nil MotionBox")
	}
	box := *res3.MotionBox
	if box[0] > 800 || box[2] < 900 || box[1] > 500 || box[3] < 600 {
		t.Errorf("motion box should enclose [800, 500, 900, 600], got %+v", box)
	}
}
