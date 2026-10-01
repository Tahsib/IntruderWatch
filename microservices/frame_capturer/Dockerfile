FROM golang:alpine AS builder

WORKDIR /app
ENV GOTOOLCHAIN=auto

COPY frame_capturer/go.mod frame_capturer/go.sum ./
RUN go mod download

COPY frame_capturer/*.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o frame_capturer .

# Stage 2: Final Minimal Runtime
FROM alpine:3.20

WORKDIR /app

# Install runtime dependencies: ffmpeg, tzdata, ca-certificates
RUN apk add --no-cache ffmpeg tzdata ca-certificates \
    && cp /usr/share/zoneinfo/Asia/Dhaka /etc/localtime \
    && echo "Asia/Dhaka" > /etc/timezone

# Copy compiled Go binary from builder
COPY --from=builder /app/frame_capturer .

# Non-root user for security
RUN adduser -D -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8001/healthz || exit 1

ENTRYPOINT ["./frame_capturer"]
