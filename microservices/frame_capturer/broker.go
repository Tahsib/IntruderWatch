package main

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Broker manages RabbitMQ connections and message publishing
type Broker struct {
	cfg         *Config
	mu          sync.RWMutex
	conn        *amqp.Connection
	ch          *amqp.Channel
	notifyClose chan *amqp.Error
}

// NewBroker creates a new RabbitMQ broker client
func NewBroker(cfg *Config) *Broker {
	return &Broker{
		cfg: cfg,
	}
}

// Connect establishes a connection to RabbitMQ with retry backoff
func (b *Broker) Connect() error {
	b.mu.Lock()
	defer b.mu.Unlock()

	amqpURL := b.cfg.RabbitMQURL()
	retries := 15
	var lastErr error

	for attempt := 1; attempt <= retries; attempt++ {
		conn, err := amqp.DialConfig(amqpURL, amqp.Config{
			Heartbeat: 60 * time.Second,
		})
		if err == nil {
			ch, err := conn.Channel()
			if err == nil {
				_, err = ch.QueueDeclare(
					b.cfg.QueueName,
					true,  // durable
					false, // autoDelete
					false, // exclusive
					false, // noWait
					nil,   // args
				)
				if err == nil {
					b.conn = conn
					b.ch = ch
					b.notifyClose = make(chan *amqp.Error, 1)
					conn.NotifyClose(b.notifyClose)
					slog.Info("Connected to RabbitMQ", "host", b.cfg.RabbitMQHost, "queue", b.cfg.QueueName)
					return nil
				}
				_ = ch.Close()
			}
			_ = conn.Close()
		}

		lastErr = err
		slog.Info("RabbitMQ not ready yet. Retrying in 5s...", "attempt", attempt, "max", retries)
		time.Sleep(5 * time.Second)
	}

	return fmt.Errorf("failed to connect to RabbitMQ after %d attempts: %w", retries, lastErr)
}

// Publish dispatches a persistent message to the configured queue
func (b *Broker) Publish(ctx context.Context, body []byte) error {
	b.mu.RLock()
	defer b.mu.RUnlock()

	if b.ch == nil {
		return fmt.Errorf("broker channel is not open")
	}

	return b.ch.PublishWithContext(
		ctx,
		"",              // exchange
		b.cfg.QueueName, // routing key
		false,           // mandatory
		false,           // immediate
		amqp.Publishing{
			DeliveryMode: amqp.Persistent,
			ContentType:  "application/json",
			Body:         body,
		},
	)
}

// NotifyClose returns the error notification channel for connection disconnects
func (b *Broker) NotifyClose() <-chan *amqp.Error {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.notifyClose
}

// Close gracefully closes the channel and connection
func (b *Broker) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.ch != nil {
		_ = b.ch.Close()
		b.ch = nil
	}
	if b.conn != nil {
		_ = b.conn.Close()
		b.conn = nil
	}
}
