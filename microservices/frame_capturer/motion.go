package main

// MotionResult contains the outcome of macroblock analysis
type MotionResult struct {
	HasMotion       bool
	ActiveBlocks    int
	TotalBlocks     int
	EffectiveBlocks int
	IsLightingShift bool
	MaxBlockDelta   float64
	MotionBox       *[4]int // [x1, y1, x2, y2] pixel coordinates of active region
}

// MacroblockDetector performs full-resolution spatial block motion analysis with exclusion masking
type MacroblockDetector struct {
	width              int
	height             int
	blockSize          int
	gridW              int
	gridH              int
	totalBlocks        int
	effectiveBlocks    int
	threshold          float64
	minActiveBlocks    int
	lightingShiftRatio float64
	isMasked           []bool
	lastY              []byte
	hasLastFrame       bool
}

// NewMacroblockDetector initializes a new full-frame motion detector with pre-compiled zone masks
func NewMacroblockDetector(cfg *Config) *MacroblockDetector {
	bs := cfg.BlockSize
	if bs <= 0 {
		bs = 32
	}
	gridW := cfg.FrameWidth / bs
	gridH := cfg.FrameHeight / bs
	total := gridW * gridH

	isMasked := make([]bool, total)
	maskedCount := 0

	// Precompute macroblock exclusion masks
	for by := 0; by < gridH; by++ {
		bY1 := by * bs
		bY2 := bY1 + bs
		for bx := 0; bx < gridW; bx++ {
			bX1 := bx * bs
			bX2 := bX1 + bs

			idx := by*gridW + bx
			for _, zone := range cfg.ExcludeZones {
				// Check for AABB intersection
				if bX1 < zone.X2 && bX2 > zone.X1 && bY1 < zone.Y2 && bY2 > zone.Y1 {
					isMasked[idx] = true
					maskedCount++
					break
				}
			}
		}
	}

	effective := total - maskedCount
	if effective <= 0 {
		effective = 1
	}

	return &MacroblockDetector{
		width:              cfg.FrameWidth,
		height:             cfg.FrameHeight,
		blockSize:          bs,
		gridW:              gridW,
		gridH:              gridH,
		totalBlocks:        total,
		effectiveBlocks:    effective,
		threshold:          cfg.MotionThreshold,
		minActiveBlocks:    cfg.MinActiveBlocks,
		lightingShiftRatio: cfg.LightingShiftRatio,
		isMasked:           isMasked,
		lastY:              make([]byte, cfg.FrameWidth*cfg.FrameHeight),
		hasLastFrame:       false,
	}
}

// Analyze inspects the raw Y (luminance) plane across all macroblocks at full 1080p resolution.
// Masked macroblocks are skipped with zero computation.
func (d *MacroblockDetector) Analyze(currentY []byte) MotionResult {
	if !d.hasLastFrame {
		copy(d.lastY, currentY)
		d.hasLastFrame = true
		return MotionResult{
			HasMotion:       false,
			ActiveBlocks:    0,
			TotalBlocks:     d.totalBlocks,
			EffectiveBlocks: d.effectiveBlocks,
			IsLightingShift: false,
			MotionBox:       nil,
		}
	}

	activeBlocks := 0
	maxBlockDelta := 0.0
	bs := d.blockSize
	blockPixelCount := bs * bs

	minBX, minBY := d.gridW, d.gridH
	maxBX, maxBY := -1, -1

	for by := 0; by < d.gridH; by++ {
		yStart := by * bs
		for bx := 0; bx < d.gridW; bx++ {
			blockIdx := by*d.gridW + bx

			// Skip excluded zones (clock OSD, swaying branches)
			if d.isMasked[blockIdx] {
				continue
			}

			xStart := bx * bs

			// Compute SAD (Sum of Absolute Differences) for this macroblock
			blockDiffSum := 0
			for y := yStart; y < yStart+bs; y++ {
				rowOffset := y * d.width
				for x := xStart; x < xStart+bs; x++ {
					idx := rowOffset + x
					c := int(currentY[idx])
					l := int(d.lastY[idx])
					diff := c - l
					if diff < 0 {
						diff = -diff
					}
					blockDiffSum += diff
				}
			}

			blockMAD := float64(blockDiffSum) / float64(blockPixelCount)
			if blockMAD > maxBlockDelta {
				maxBlockDelta = blockMAD
			}

			if blockMAD >= d.threshold {
				activeBlocks++
				if bx < minBX {
					minBX = bx
				}
				if bx > maxBX {
					maxBX = bx
				}
				if by < minBY {
					minBY = by
				}
				if by > maxBY {
					maxBY = by
				}
			}
		}
	}

	// Always update reference frame for continuous temporal difference
	copy(d.lastY, currentY)

	// Lighting Shift Check: evaluated against effective (non-masked) blocks
	ratio := float64(activeBlocks) / float64(d.effectiveBlocks)
	isLightingShift := ratio >= d.lightingShiftRatio

	hasMotion := activeBlocks >= d.minActiveBlocks && !isLightingShift

	var motionBox *[4]int
	if hasMotion && maxBX >= 0 {
		x1 := minBX * bs
		y1 := minBY * bs
		x2 := (maxBX + 1) * bs
		y2 := (maxBY + 1) * bs
		if x2 > d.width {
			x2 = d.width
		}
		if y2 > d.height {
			y2 = d.height
		}
		box := [4]int{x1, y1, x2, y2}
		motionBox = &box
	}

	return MotionResult{
		HasMotion:       hasMotion,
		ActiveBlocks:    activeBlocks,
		TotalBlocks:     d.totalBlocks,
		EffectiveBlocks: d.effectiveBlocks,
		IsLightingShift: isLightingShift,
		MaxBlockDelta:   maxBlockDelta,
		MotionBox:       motionBox,
	}
}

// Reset clears the background reference (used on reconnection or schedule change)
func (d *MacroblockDetector) Reset() {
	d.hasLastFrame = false
}
