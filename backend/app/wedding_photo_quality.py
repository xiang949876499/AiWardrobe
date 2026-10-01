"""Local portrait quality signals, never biometric identity recognition."""
import cv2
import numpy as np

# Bound local CPU use; API/worker concurrency must not multiply OpenCV pools.
cv2.setNumThreads(1)


def detect_faces(image):
    face = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eye = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye_tree_eyeglasses.xml')
    if face.empty() or eye.empty():
        raise RuntimeError('PHOTO_DETECTOR_UNAVAILABLE')
    boxes = face.detectMultiScale(image, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    eyes = sum(len(eye.detectMultiScale(image[y:y+h, x:x+w])) for x,y,w,h in boxes)
    return boxes, eyes


def analyze_photo(data, *, demo=False, detector=None):
    # This detector is CPU-only. OpenCL context initialization from an API
    # thread can hang native Windows shutdown after Python has already exited.
    # Set this on each calling thread; OpenCV's OpenCL preference is thread-local.
    cv2.ocl.setUseOpenCL(False)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return dict(usable=False, warnings=['decode_failed'], demo_bypass=False, detector='opencv-haar-v1')
    # Bound detector work on large decoded images.
    if max(image.shape) > 1600:
        image = cv2.resize(image, None, fx=1600/max(image.shape), fy=1600/max(image.shape))
    boxes, eyes = (detector or detect_faces)(image)
    count = len(boxes)
    blur = float(cv2.Laplacian(image, cv2.CV_64F).var())
    ratio = max((w*h / image.size for x,y,w,h in boxes), default=0)
    warnings = []
    if count != 1:
        warnings.append('multiple_faces' if count > 1 else 'no_face_detected')
    if blur < 80:
        warnings.append('blur')
    if ratio < .04:
        warnings.append('small_face')
    if count == 1 and eyes < 2:
        warnings.append('possible_occlusion')
    return dict(usable=count == 1 or (demo and count == 0), warnings=warnings,
        demo_bypass=demo and count == 0, face_count=count, blur_score=round(blur,2),
        face_area_ratio=round(ratio,4), detector='opencv-haar-v1',
        limitation='Quality signals only; human inspection required; no identity matching.')
