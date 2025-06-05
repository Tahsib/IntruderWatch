import cv2
import os
import time
import requests
from datetime import datetime
import numpy as np
from twilio.rest import Client
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Function to send an SMS alert using Twilio's API via HTTP POST request
def send_alert(message, to_phone_number):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        'To': to_phone_number,
        'From': TWILIO_PHONE_NUMBER,
        'Body': message
    }
    response = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))

    
    if response.status_code == 201 or response.status_code == 200:
        logging.info("Alert sent to your mobile!")
    else:
        logging.error(f"Failed to send alert: {response.status_code} - {response.text}")

def call_alert(message, to_phone_number):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    call = client.calls.create(
        url="http://demo.twilio.com/docs/voice.xml",
        to=to_phone_number,
        from_=TWILIO_PHONE_NUMBER,
    )

    print(call.sid)

# Load the MobileNet-SSD model
def load_mobilenet_ssd():
    # Paths to the pre-trained model and prototxt
    prototxt_path = "deploy.prototxt"
    model_path = "mobilenet_iter_73000.caffemodel"
    
    # Load the model
    net = cv2.dnn.readNetFromCaffe(prototxt_path, model_path)
    
    return net

# Detect humans using MobileNet-SSD
def detect_human_mobilenet_ssd(net, frame):
    # Pre-process the frame (resize, mean subtraction, scaling)
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    
    # Perform forward pass and get detections
    detections = net.forward()

    # Get the height and width of the frame
    (h, w) = frame.shape[:2]
    human_detected = False
    
    # Need to implement half body detections
    half_body_detected = False
    
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        
        # Only consider detections with high confidence
        if confidence > 0.8:  # Lowered threshold for testing
            idx = int(detections[0, 0, i, 1])
            
            # Class ID 15 is 'person' in the COCO dataset for MobileNet-SSD
            if idx == 15:
                # Get the coordinates of the bounding box
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                (startX, startY, endX, endY) = box.astype("int")
                
                # Draw a bounding box around the detected person
                cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 255, 0), 2)
                human_detected = True
                logging.info(f"Human detected: Box = ({startX}, {startY}, {endX}, {endY})")
                logging.info(f"Detection {i}: Confidence = {confidence}")
    
    return human_detected, frame


# Capture video stream from DVR and detect humans
def capture_stream(ip, channel, stream, username, password, alert_cooldown, start_time, end_time, to_phone_numbers):
    rtsp_url = f"rtsp://{ip}:554/user={username}&password={password}&channel={channel}&stream={stream}.sdp"
    logging.info(f"Attempting to connect to: {rtsp_url}")
    
    if not os.path.exists('captures'):
        os.makedirs('captures')
    
    cap = cv2.VideoCapture(rtsp_url)
    
    if not cap.isOpened():
        logging.error("Failed to open stream.")
    
    logging.info("Successfully connected to stream!")
    
    # Load MobileNet-SSD model
    net = load_mobilenet_ssd()
    
    # Time management for sending alerts
    last_alert_time = 0
    last_log_time = 0  # Add variable to track last log time
    
    try:
        while True:
            # Get the current time
            current_time = datetime.now()
            current_hour = current_time.hour

            if current_hour >= start_time and current_hour < end_time:
                ret, frame = cap.read()
                if ret:
                    # Detect human presence
                    detected, processed_frame = detect_human_mobilenet_ssd(net, frame)
                    
                    # Display the processed frame with bounding boxes
                    # cv2.imshow('DVR Stream - Human Detection', processed_frame)
                    
                    # If human is detected and cooldown period has passed, send alert
                    current_time = time.time()
                    if detected and (current_time - last_alert_time) > alert_cooldown:
                        logging.info("Human detected! Sending alert...")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for number in to_phone_numbers.split(":"):
                            # send_alert(f"Human detected in channel-{channel} at {timestamp}", number)
                            call_alert("Intruder detected!!", number)
                        last_alert_time = current_time
                        
                        date_only = timestamp.split()[0]
                        date_directory = os.path.join('captures', date_only)
                        if not os.path.exists(date_directory):
                            os.makedirs(date_directory)
                        
                        # Save the frame with detected human
                        filename = f'{date_directory}/detection_{channel}_{timestamp}.jpg'
                    
                    if detected:
                        cv2.imwrite(filename, processed_frame)
                        logging.info(f"Saved detection frame to {filename}")
                    
                    # Break the loop on 'q' key press
                    # key = cv2.waitKey(1) & 0xFF
                    # if key == ord('q'):
                    #     break
                else:
                    logging.error("Lost connection. Attempting to reconnect...")
                    cap = cv2.VideoCapture(rtsp_url)
                    if not cap.isOpened():
                        logging.error("Failed to open stream.")
                    time.sleep(1)
            else:
                 # Check if 30 minutes have passed since last log
                current_time = time.time()
                if current_time - last_log_time >= 30 * 60:  # 30 minutes
                    logging.info(f"Outside of active hours ({start_time} to {end_time}). Waiting...")
                    last_log_time = current_time  # Update last log time
                time.sleep(60)  # Wait for 1 minute before checking again
                
    except KeyboardInterrupt:
        logging.info("\nStopping capture...")
    finally:
        cap.release()
        # cv2.destroyAllWindows()

if __name__ == "__main__":
    # You can modify these parameters as needed
    stream_ip = os.getenv("STREAM_IP")
    stream_username = os.getenv("STREAM_USERNAME")
    stream_password = os.getenv("STREAM_PASSWORD")
    channel = os.getenv("CHANNEL")
    start = os.getenv("START_TIME")
    end = os.getenv("END_TIME")
    phone_numbers = os.getenv("PHONE_NUMBERS", "")
    
    # Twilio API configuration (use your actual account SID, Auth Token, and phone numbers)
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

    
    capture_stream(
        ip=stream_ip,
        channel=int(channel),  
        stream=0,   
        username=stream_username,
        password=stream_password, 
        alert_cooldown=90,  # Set cooldown period to 90 seconds between alerts
        start_time=int(start),
        end_time=int(end),
        to_phone_numbers=phone_numbers
    )
