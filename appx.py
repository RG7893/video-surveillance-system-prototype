from flask import Flask, Response, render_template, request, redirect, url_for, session, flash
import boto3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import threading
import cv2
import numpy as np
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import logging
import signal
import sys

# Initialize Flask app first
app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/static')
app.secret_key = os.getenv('SECRET_KEY', 'your_secret_key')

# Configure SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Define User model AFTER db initialization
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

# Create tables
with app.app_context():
    db.create_all()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[
    logging.FileHandler("app.log"),
    logging.StreamHandler()
])

# AWS Configuration
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', '<Enter your Amazon S3 Bucket Name here.>')
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY', '<Enter your AWS IAM Access Key here.>')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY', '<Enter your AWS IAM Secret Key here.>')

# Video capture setup
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    logging.error("Cannot open camera")
    sys.exit(1)
fourcc = cv2.VideoWriter_fourcc(*'XVID')
fps = 15.0
frame_size = (int(cap.get(3)), int(cap.get(4)))
out = None
motion_detected = False
motion_stop_time = 0
record_time = 2  # seconds
current_dir = os.path.dirname(os.path.abspath(__file__))
lock = threading.Lock()
current_frame = None
status = "Idle"

# Graceful shutdown
def release_resources():
    global cap, out
    if cap.isOpened():
        cap.release()
    if out:
        out.release()
    cv2.destroyAllWindows()
    logging.info("Resources released.")

def signal_handler(sig, frame):
    release_resources()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# S3 upload function
def upload_to_s3(file_path, bucket_name, object_name):
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    try:
        s3.upload_file(file_path, bucket_name, object_name)
        logging.info(f"Uploaded {file_path} to S3 bucket {bucket_name} as {object_name}.")
        os.remove(file_path)  # Clean up local file after upload
        return True
    except Exception as e:
        logging.error(f"Error during S3 upload: {e}")
        return False

# Motion detection and recording logic
def motion_detection_and_recording():
    try:
        global current_frame, out, motion_detected, motion_stop_time, status
        prev_frame = None
        grid_size = 10
        filename = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                logging.error("Camera read failed.")
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if prev_frame is not None:
                flow = cv2.calcOpticalFlowFarneback(prev_frame, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                magnitude, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                avg_flow = np.mean(magnitude)

                if avg_flow > 0.5:  # Motion threshold
                    motion_detected = True
                    motion_stop_time = 0
                    status = "Recording"
                    if out is None:
                        filename += 1
                        output_path = os.path.join(current_dir, f'output{filename}.avi')
                        out = cv2.VideoWriter(output_path, fourcc, fps, frame_size)
                        logging.info(f"Start recording: {output_path}")
                else:
                    motion_stop_time += 1
                    if motion_stop_time >= record_time * fps and out is not None:
                        out.release()
                        out = None
                        logging.info("Stop recording")
                        upload_to_s3(output_path, S3_BUCKET_NAME, os.path.basename(output_path))
                        motion_stop_time = 0
                        motion_detected = False
                        status = "Idle"

                # Write to video if recording
                if out is not None:
                    out.write(frame)

            # Update the global current frame for streaming
            with lock:
                current_frame = frame.copy()

            prev_frame = gray
    except Exception as e:
        logging.error(f"Motion detection crashed: {str(e)}")
        release_resources()


threading.Thread(target=motion_detection_and_recording, daemon=True).start()

# Live stream generator
def generate_stream():
    global current_frame
    while True:
        with lock:
            if current_frame is not None:
                frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2RGB)
                _, buffer = cv2.imencode('.jpg', frame)
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

# Add context processor for S3_BUCKET_NAME
@app.context_processor
def inject_s3_bucket():
    return dict(S3_BUCKET_NAME=S3_BUCKET_NAME)

# Flask routes
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return redirect(url_for('auth'))
    try:
        first_name = request.form['first_name']  # Use [] instead of get()
        last_name = request.form['last_name']
        email = request.form['email']
        password = request.form['password']

        if User.query.filter_by(email=email).first():
            flash('Email already exists!', 'error')
            return redirect(url_for('auth'))

        new_user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('auth'))
    except Exception as e:
        db.session.rollback()
        logging.error(f"Signup error: {str(e)}")
        flash('An error occurred during registration', 'error')
        return redirect(url_for('auth'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return redirect(url_for('auth'))
    email = request.form.get('email')
    password = request.form.get('password')

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        flash('Invalid email or password', 'error')
        return redirect(url_for('auth'))

    session['user'] = email
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('auth'))
    user = User.query.filter_by(email=session['user']).first()
    return render_template('dashboard.html', user=user)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('auth'))

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('auth'))

@app.route('/auth')
def auth():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('auth.html')

@app.route('/live-stream')
def live_stream():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('live_stream.html', status=status)

@app.route('/stream')
def stream():
    if 'user' not in session:
        return Response(status=403)
    return Response(generate_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/recordings')
def recordings():
    if 'user' not in session:
        return redirect(url_for('login'))
    try:
        s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
        objects = s3.list_objects_v2(Bucket=S3_BUCKET_NAME).get('Contents', [])
        videos = [obj['Key'] for obj in objects] if objects else []
    except Exception as e:
        videos = []
        logging.error(f"Error fetching recordings: {e}")
    return render_template('recordings.html', videos=videos)

@app.route('/delete-recording/<string:video_key>', methods=['POST'])
def delete_recording(video_key):
    if 'user' not in session:
        return redirect(url_for('login'))
    try:
        s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
        s3.delete_object(Bucket=S3_BUCKET_NAME, Key=video_key)
        return redirect(url_for('recordings'))
    except Exception as e:
        logging.error(f"Delete error: {e}")
        return render_template('recordings.html', error="Delete failed")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
