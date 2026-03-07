import pika
import os
import time
import logging

logger = logging.getLogger(__name__)


def connect_rabbitmq(queue_names, retries=15, delay=5, frame_max=0):
    """
    Establish a RabbitMQ connection and declare the given queues.
    Improved with healthcheck-aware retry logic and persistent connection settings.
    """
    host = os.getenv("RABBITMQ_HOST", "localhost")
    user = os.getenv("RABBITMQ_USER", "guest")
    password = os.getenv("RABBITMQ_PASS", "guest")

    credentials = pika.PlainCredentials(user, password)
    
    # Connection parameters with heartbeat to prevent connection drops during idle
    kwargs = dict(
        host=host, 
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=300
    )
    if frame_max:
        kwargs["frame_max"] = frame_max
    params = pika.ConnectionParameters(**kwargs)

    if isinstance(queue_names, str):
        queue_names = [queue_names]

    for attempt in range(1, retries + 1):
        try:
            # We no longer silence logs here because Docker healthchecks handle the wait.
            # If an error occurs now, it's a real issue we want to see.
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            for q in queue_names:
                channel.queue_declare(queue=q, durable=True)
            logger.info("Successfully connected to RabbitMQ at %s", host)
            return connection, channel
        except (pika.exceptions.AMQPConnectionError, ConnectionRefusedError) as e:
            logger.info("RabbitMQ not ready yet (Attempt %d/%d). Waiting...", attempt, retries)
            if attempt < retries:
                time.sleep(delay)

    raise ConnectionError(f"Could not connect to RabbitMQ after {retries} attempts")
