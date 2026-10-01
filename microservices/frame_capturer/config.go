package main

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// Zone defines a rectangular region in pixel coordinates [x1, y1, x2, y2]
type Zone struct {
	Name           string
	X1, Y1, X2, Y2 int
}

// Config encapsulates all runtime configuration options
type Config struct {
	StreamIP           string
	StreamUsername     string
	StreamPassword     string
	Channel            int
	ChannelStr         string
	Subtype            int
	FrameWidth         int
	FrameHeight        int
	FPS                int
	FrameSleep         time.Duration
	StartTimeStr       string
	EndTimeStr         string
	StartSeconds       int
	EndSeconds         int
	JPEGQuality        int
	BlockSize          int
	MotionThreshold    float64
	MinActiveBlocks    int
	LightingShiftRatio float64
	ExcludeZones       []Zone
	PostRollDuration   time.Duration
	CustomRTSPURL      string
	RabbitMQHost       string
	RabbitMQPort       string
	RabbitMQUser       string
	RabbitMQPass       string
	QueueName          string
	LogLevel           string
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func getEnvInt(key string, defaultVal int) int {
	if val := os.Getenv(key); val != "" {
		if i, err := strconv.Atoi(val); err == nil {
			return i
		}
	}
	return defaultVal
}

func getEnvFloat(key string, defaultVal float64) float64 {
	if val := os.Getenv(key); val != "" {
		if f, err := strconv.ParseFloat(val, 64); err == nil {
			return f
		}
	}
	return defaultVal
}

func parseSeconds(timeStr string) (int, error) {
	parts := strings.Split(strings.TrimSpace(timeStr), ":")
	if len(parts) != 3 {
		return 0, fmt.Errorf("invalid time format (expected HH:MM:SS): %s", timeStr)
	}
	h, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, err
	}
	m, err := strconv.Atoi(parts[1])
	if err != nil {
		return 0, err
	}
	s, err := strconv.Atoi(parts[2])
	if err != nil {
		return 0, err
	}
	return h*3600 + m*60 + s, nil
}

// parseExcludeZones parses named presets or coordinate tuples: "x1,y1,x2,y2; top-left-clock"
func parseExcludeZones(raw string, frameW, frameH int) []Zone {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}

	var zones []Zone
	items := strings.Split(raw, ";")
	for _, item := range items {
		token := strings.TrimSpace(strings.ToLower(item))
		if token == "" {
			continue
		}

		// Named presets
		switch token {
		case "top-left-clock":
			zones = append(zones, Zone{Name: "top-left-clock", X1: 0, Y1: 0, X2: 480, Y2: 96})
			continue
		case "top-right-clock":
			zones = append(zones, Zone{Name: "top-right-clock", X1: frameW - 448, Y1: 0, X2: frameW, Y2: 96})
			continue
		case "bottom-left-clock":
			zones = append(zones, Zone{Name: "bottom-left-clock", X1: 0, Y1: frameH - 96, X2: 480, Y2: frameH})
			continue
		case "bottom-right-clock":
			zones = append(zones, Zone{Name: "bottom-right-clock", X1: frameW - 480, Y1: frameH - 96, X2: frameW, Y2: frameH})
			continue
		}

		// Numeric tuple: "x1,y1,x2,y2"
		parts := strings.Split(token, ",")
		if len(parts) == 4 {
			x1, err1 := strconv.Atoi(strings.TrimSpace(parts[0]))
			y1, err2 := strconv.Atoi(strings.TrimSpace(parts[1]))
			x2, err3 := strconv.Atoi(strings.TrimSpace(parts[2]))
			y2, err4 := strconv.Atoi(strings.TrimSpace(parts[3]))

			if err1 == nil && err2 == nil && err3 == nil && err4 == nil {
				// Bounds checking and ordering
				if x1 > x2 {
					x1, x2 = x2, x1
				}
				if y1 > y2 {
					y1, y2 = y2, y1
				}
				if x1 < 0 {
					x1 = 0
				}
				if y1 < 0 {
					y1 = 0
				}
				if x2 > frameW {
					x2 = frameW
				}
				if y2 > frameH {
					y2 = frameH
				}
				zones = append(zones, Zone{
					Name: fmt.Sprintf("[%d,%d,%d,%d]", x1, y1, x2, y2),
					X1:   x1,
					Y1:   y1,
					X2:   x2,
					Y2:   y2,
				})
			}
		}
	}

	return zones
}

// IsWithinTimeFrame checks if the current time falls inside the configured schedule
func (c *Config) IsWithinTimeFrame(now time.Time) bool {
	nowSec := now.Hour()*3600 + now.Minute()*60 + now.Second()
	if c.StartSeconds <= c.EndSeconds {
		return nowSec >= c.StartSeconds && nowSec <= c.EndSeconds
	}
	// Overnight schedule, e.g. 22:00:00 to 06:00:00
	return nowSec >= c.StartSeconds || nowSec <= c.EndSeconds
}

// RTSPURL returns the sanitized stream address
func (c *Config) RTSPURL() string {
	if c.CustomRTSPURL != "" {
		return c.CustomRTSPURL
	}
	return fmt.Sprintf("rtsp://%s:%s@%s:554/cam/realmonitor?channel=%d&subtype=%d",
		c.StreamUsername, c.StreamPassword, c.StreamIP, c.Channel, c.Subtype)
}

// RabbitMQURL returns a safe AMQP connection URI with URL-encoded credentials
func (c *Config) RabbitMQURL() string {
	u := url.URL{
		Scheme: "amqp",
		User:   url.UserPassword(c.RabbitMQUser, c.RabbitMQPass),
		Host:   net.JoinHostPort(c.RabbitMQHost, c.RabbitMQPort),
		Path:   "/",
	}
	return u.String()
}

// LoadConfig initializes configuration from environment variables with sensible defaults
func LoadConfig() (*Config, error) {
	channel := getEnvInt("CHANNEL", 1)
	startStr := getEnv("START_TIME", "00:00:00")
	endStr := getEnv("END_TIME", "23:59:59")

	startSec, err := parseSeconds(startStr)
	if err != nil {
		return nil, fmt.Errorf("START_TIME error: %w", err)
	}
	endSec, err := parseSeconds(endStr)
	if err != nil {
		return nil, fmt.Errorf("END_TIME error: %w", err)
	}

	frameSleepSec := getEnvFloat("FRAME_SLEEP", 0.05)
	postRollSec := getEnvFloat("POST_ROLL_SECONDS", 1.5)
	frameW := getEnvInt("FRAME_WIDTH", 1920)
	frameH := getEnvInt("FRAME_HEIGHT", 1080)

	excludeZonesRaw := getEnv("EXCLUDE_ZONES", "")
	excludeZones := parseExcludeZones(excludeZonesRaw, frameW, frameH)

	cfg := &Config{
		StreamIP:           getEnv("STREAM_IP", "127.0.0.1"),
		StreamUsername:     getEnv("STREAM_USERNAME", "admin"),
		StreamPassword:     getEnv("STREAM_PASSWORD", "password"),
		Channel:            channel,
		ChannelStr:         strconv.Itoa(channel),
		Subtype:            getEnvInt("SUBTYPE", 0),
		FrameWidth:         frameW,
		FrameHeight:        frameH,
		FPS:                getEnvInt("FPS", 3),
		FrameSleep:         time.Duration(frameSleepSec * float64(time.Second)),
		StartTimeStr:       startStr,
		EndTimeStr:         endStr,
		StartSeconds:       startSec,
		EndSeconds:         endSec,
		JPEGQuality:        getEnvInt("JPEG_QUALITY", 85),
		BlockSize:          getEnvInt("BLOCK_SIZE", 32),
		MotionThreshold:    getEnvFloat("MOTION_THRESHOLD", 8.0),
		MinActiveBlocks:    getEnvInt("MIN_ACTIVE_BLOCKS", 3),
		LightingShiftRatio: getEnvFloat("LIGHTING_SHIFT_RATIO", 0.75),
		ExcludeZones:       excludeZones,
		PostRollDuration:   time.Duration(postRollSec * float64(time.Second)),
		CustomRTSPURL:      os.Getenv("CUSTOM_RTSP_URL"),
		RabbitMQHost:       getEnv("RABBITMQ_HOST", "rabbitmq"),
		RabbitMQPort:       getEnv("RABBITMQ_PORT", "5672"),
		RabbitMQUser:       getEnv("RABBITMQ_USER", "guest"),
		RabbitMQPass:       getEnv("RABBITMQ_PASS", "guest"),
		QueueName:          getEnv("QUEUE_NAME", "frame_queue"),
		LogLevel:           strings.ToUpper(getEnv("LOG_LEVEL", "INFO")),
	}

	return cfg, nil
}
