import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import MagicMock

import cv2
import numpy as np

# Set dummy environment variables before importing detector modules
os.environ["YOLO_MODEL"] = "yolo11m.pt"
os.environ["DETECTION_CONFIDENCE"] = "0.75"
os.environ["SAVE_QUALITY"] = "80"
os.environ["INFERENCE_SIZE"] = "640"

try:
    from human_detector.human_detector import (
        AlertDispatcher,
        DetectionState,
        start_http_service,
    )
except ModuleNotFoundError:
    from human_detector import (
        AlertDispatcher,
        DetectionState,
        start_http_service,
    )


class TestHumanDetectorEdgeCases(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_captures_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_alert_dispatcher_disk_write_and_publish(self):
        """Edge Case 1: AlertDispatcher saves image to disk and publishes to RabbitMQ."""
        dispatcher = AlertDispatcher(save_quality=80, queue_size=10)

        # Mock rabbitmq connection and channel with is_closed=False
        mock_channel = MagicMock()
        mock_conn = MagicMock()
        mock_conn.is_closed = False
        mock_channel.is_closed = False
        dispatcher.connection = mock_conn
        dispatcher.channel = mock_channel

        # Start background worker
        dispatcher.start()

        # Create synthetic test frame
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(frame, (100, 100), (200, 300), (0, 255, 0), 2)
        test_filename = os.path.join(self.test_dir, "camera_1", "2026-10-03", "det_test.jpg")

        payload = {
            "camera": 1,
            "timestamp": "2026-10-03 02:15:30.000000",
            "filename": test_filename,
            "human_count": 2,
            "max_confidence": 0.94,
            "boxes": [{"box": [100, 100, 200, 300], "confidence": 0.94}],
        }

        dispatcher.submit(frame, test_filename, payload)

        # Wait for background queue to flush
        dispatcher.queue.join()

        # Verify file was written to disk
        self.assertTrue(os.path.exists(test_filename), "Output file was not created by worker")
        read_back = cv2.imread(test_filename)
        self.assertIsNotNone(read_back, "Saved image could not be decoded by OpenCV")
        self.assertEqual(read_back.shape, (480, 640, 3))

        # Verify AMQP message was published
        mock_channel.basic_publish.assert_called_once()
        call_kwargs = mock_channel.basic_publish.call_args[1]
        self.assertEqual(call_kwargs["routing_key"], "alert_queue")
        published_payload = json.loads(call_kwargs["body"])
        self.assertEqual(published_payload["human_count"], 2)
        self.assertEqual(published_payload["max_confidence"], 0.94)
        self.assertEqual(len(published_payload["boxes"]), 1)

    def test_alert_dispatcher_reconnect_retry(self):
        """Edge Case 2: AlertDispatcher recovers from dropped AMQP connection."""
        dispatcher = AlertDispatcher(save_quality=80, queue_size=10)

        mock_failing_channel = MagicMock()
        mock_failing_channel.is_closed = False
        mock_failing_channel.basic_publish.side_effect = Exception("Broken pipe")

        mock_good_channel = MagicMock()
        mock_good_channel.is_closed = False

        # First call uses failing channel, reconnect switches to good channel
        call_count = 0

        def mock_ensure():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                dispatcher.channel = mock_failing_channel
            else:
                dispatcher.channel = mock_good_channel
            dispatcher.connection = MagicMock()
            dispatcher.connection.is_closed = False

        dispatcher._ensure_channel = mock_ensure

        payload = {"camera": 1, "test": True}
        success = dispatcher._publish_with_retry(payload, retries=2)

        self.assertTrue(success, "Publish did not succeed after reconnect retry")
        mock_good_channel.basic_publish.assert_called_once()

    def test_alert_dispatcher_drop_oldest_on_full_queue(self):
        """Edge Case 3: When queue is full, submit evicts oldest frame (head-drop) to preserve newest frame."""
        dispatcher = AlertDispatcher(save_quality=80, queue_size=2)
        # Do not start worker thread so queue stays full
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        # Fill queue to capacity (2)
        dispatcher.submit(frame, "file1.jpg", {"seq": 1})
        dispatcher.submit(frame, "file2.jpg", {"seq": 2})
        self.assertEqual(dispatcher.queue.qsize(), 2)

        # 3rd submit must evict file1.jpg and retain file2.jpg and file3.jpg
        t0 = time.time()
        dispatcher.submit(frame, "file3.jpg", {"seq": 3})
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.05, "Submit blocked when queue was full!")

        self.assertEqual(dispatcher.queue.qsize(), 2)
        item1 = dispatcher.queue.get_nowait()
        item2 = dispatcher.queue.get_nowait()
        self.assertEqual(item1[1], "file2.jpg", "Oldest frame was not evicted!")
        self.assertEqual(item2[1], "file3.jpg", "Newest frame was not preserved!")

    def test_wsgi_healthz_and_metrics(self):
        """Edge Case 4: /healthz reflects initialization and liveness status."""
        state = DetectionState()
        port = 8098

        server = start_http_service(port, state)
        try:
            # 1. Uninitialized: must return 503
            req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 503)

            # 2. Initialized: must return 200 OK
            state.is_ready = True
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read().decode().strip(), "OK")

            # 3. Crash / Exit simulation: state.is_ready = False must return 503
            state.is_ready = False
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 503)

            # 4. /metrics: must return 200
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as resp:
                self.assertEqual(resp.status, 200)
                body = resp.read().decode()
                self.assertIn("human_detector_frames_processed_total", body)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
