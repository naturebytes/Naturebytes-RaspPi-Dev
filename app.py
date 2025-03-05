import io
import os
import cv2
import time
import math
import binascii

from flask              import Flask, Response, request, render_template, send_from_directory, url_for, jsonify
from threading          import Condition
from multiprocessing    import Process, Value
from picamera2          import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs  import FileOutput

# Local
from log import log
from main import camera


app = Flask("Image Gallery")
app.config['IMAGE_EXTS'] = [".png", ".jpg", ".jpeg", ".gif", ".tiff"]

photo_mode = Value('i', 1)
photos = Process(target=camera, args=( photo_mode, os.path.join(app.config.root_path, "static/photos/"), False))
photos.run()

"""
User a shared memory byte to control how the camera app takes pictures:
    0 - turn of pictures
    1 - still pictures
    2 - video clips of 10 seconds
    3 - live stream
"""
TURN_OFF_PICTURES = 0
STILL_PICTURES = 1
VIDEO_CLIPS = 2
LIVE_STREAM = 3



def encode(x):
    return binascii.hexlify(x.encode('utf-8')).decode()


def decode(x):
    return binascii.unhexlify(x.encode('utf-8')).decode()


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


def get_photo_paths(limit=6, page=0):
    """
        @limit - number of images per page
        @page - current page number
        :return a list of image paths in the photo directory.
    """
    photo_dir = os.path.join(app.config.root_path, "static/photos/")
    image_paths = []
    for root,dirs,files in os.walk(photo_dir):
        for file in files:
            if any(file.endswith(ext) for ext in app.config['IMAGE_EXTS']):
                image_paths.append(url_for('static', filename=f'photos/{file}', _external=True))

    start = page * limit
    end = start + limit

    # sort
    image_paths.sort()
    # reverse
    image_paths = image_paths[::-1]
    
    return image_paths[start:end], len(image_paths)


@app.route('/')
def home():
    global photo_mode
    image_paths, _ = get_photo_paths(6, 0)

    if photo_mode == TURN_OFF_PICTURES:
        camera_state = 2
    elif photo_mode == STILL_PICTURES or  photo_mode == VIDEO_CLIPS:
        camera_state = 1
    else:
        camera_state = 0
    
    motion_state = 1
    if photo_mode == STILL_PICTURES:
        motion_state = 1
    elif photo_mode == VIDEO_CLIPS:
        motion_state = 2

    return render_template('index.html', images=image_paths, camera_state=camera_state, motion_state=motion_state)


@app.route('/gallery/<int:page>')
def gallery(page):
    per_page = 18
    image_paths, len_image_paths = get_photo_paths(per_page, page)
    return render_template(
        'gallery.html',
        images=image_paths,
        page=page,
        total_pages=math.ceil(len_image_paths / per_page),
        per_page=per_page,
        total_images=len_image_paths
    )


@app.route('/cdn/<path:filepath>')
def download_file(filepath):
    dir,filename = os.path.split(decode(filepath))
    return send_from_directory(dir, filename, as_attachment=False)


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = "naturebytes-photo-" + time.strftime("%Y-%m-%dT%H-%M-%S") + ".png"
    file_path = os.path.join(app.config.root_path, "static/photos", filename)
    file.save(file_path)
    
    return jsonify({'message': 'File uploaded successfully'}), 201


@app.route('/recent-photos')
def recent_photos():
    image_paths, _ = get_photo_paths(6, 0)
    return jsonify(image_paths)


@app.route('/delete-image', methods=['DELETE'])
def delete_image():
    data = request.get_json()
    image_path = data.get('image').split("photos/")[1]
    abs_path = os.path.join(app.config.root_path, os.path.join("static", "photos", image_path))
    os.remove(abs_path)

    return "Success", 204

rpi_cam_available = True   # Michal's variable meaning that he has no Pi Camera at hand
def gen():
    """Video streaming generator function."""

    if rpi_cam_available:
        with Picamera2() as picam2:
            picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
            output = StreamingOutput()
            picam2.start_recording(JpegEncoder(), FileOutput(output))
            while True:
                with output.condition:
                    output.condition.wait()
                    frame = output.frame
                    yield (b'--frame\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: ' + str(len(frame)).encode() + b'\r\n'
                        b'\r\n' + frame + b'\r\n')

    else:
        """ Simulate the Pi Camera with a regular webcam """
        video_path = os.path.join("static", "assets", "random_video.mp4")
        cap = cv2.VideoCapture(video_path)  # Use a webcam or replace 0 with a video file path

        if not cap.isOpened():
            print("Error: Could not open video stream.")
            return

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            _, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()

            yield (b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n'
                b'Content-Length: ' + str(len(frame_bytes)).encode() + b'\r\n'
                b'\r\n' + frame_bytes + b'\r\n')
            
            # Simulating frame rate (30 FPS)
            time.sleep(1/30)

        cap.release()


@app.route('/video_feed')
def video_feed():
    """Video streaming route. Put this in the src attribute of an img tag."""
    global photo_mode

    photo_mode = TURN_OFF_PICTURES
    log.info(f"SM:{photo_mode}")
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/capture_video')
def capture_video():
    """
    Start to capture short video instead of still images
    :return: Success
    """
    global photo_mode

    photo_mode = VIDEO_CLIPS
    log.info(f"SM:{photo_mode}")
    return jsonify({'message': 'Video capture started'}), 201


@app.route('/capture_image')
def capture_image():
    global photo_mode

    photo_mode = STILL_PICTURES
    log.info(f"SM:{photo_mode}")
    return "Success", 201


@app.route('/stop_camera')
def stop_camera():
    global photo_mode

    photo_mode = TURN_OFF_PICTURES
    log.info(f"SM:{photo_mode}")
    return "Success", 201


@app.route('/watch_live')
def watch_live():
    global photo_mode

    photo_mode = TURN_OFF_PICTURES
    log.info(f"SM:{photo_mode}")
    return jsonify({'message': 'Live stream started'}), 201


if __name__=="__main__":
    '''
    parser = argparse.ArgumentParser('Usage: %prog [options]')
    parser.add_argument('root_dir', help='Gallery root directory path')
    parser.add_argument('-l', '--listen', dest='host', default='0.0.0.0', \
                        help='address to listen on [0.0.0.0]')
    parser.add_argument('-p', '--port', metavar='PORT', dest='port', type=int, \
                        default=5000, help='port to listen on [5000]')
    args = parser.parse_args()
    '''
    #app.config[]
    app.run(host='0.0.0.0', debug=True)