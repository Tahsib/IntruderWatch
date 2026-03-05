import pika
import os
import time
import logging

logger = logging.getLogger(__name__)


def connect_rabbitmq(queue_names, retries=5, delay=5, frame_max=0):
    """
    Establish a RabbitMQ connection and declare the given queues.

    Args:
        queue_names: A single queue name (str) or list of queue names to declare.
        retries: Number of connection attempts before raising.
        delay: Seconds to wait between retries.
        frame_max: Max frame size for the connection (0 = no limit).

    Returns:
        (connection, channel) tuple.
    """
    host = os.getenv("RABBITMQ_HOST", "localhost")
    user = os.getenv("RABBITMQ_USER", "guest")
    password = os.getenv("RABBITMQ_PASS", "guest")

    credentials = pika.PlainCredentials(user, password)
    params = pika.ConnectionParameters(
        host=host, credentials=credentials, frame_max=frame_max
    )

    if isinstance(queue_names, str):
        queue_names = [queue_names]

    for attempt in range(1, retries + 1):
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            for q in queue_names:
                channel.queue_declare(queue=q, durable=True)
            logger.info("Connected to RabbitMQ at %s, queues: %s", host, queue_names)
            return connection, channel
        except pika.exceptions.AMQPConnectionError as e:
            logger.warning("RabbitMQ connection attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(delay)

    raise ConnectionError(f"Could not connect to RabbitMQ after {retries} attempts")
