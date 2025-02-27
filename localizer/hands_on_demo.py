import datetime
import enum
import json
import os
import shutil
import sys
import traceback

import cv2
import matplotlib.pyplot as plt
import numpy as np

from localizer import train
from localizer import predict
from localizer import utils


class HandsOnDemo:
    class Mode(enum.Enum):
        DETECT = 0  # Detect using current model
        NEW_MODEL = 1  # Create a new dataset and train a new model

    def __init__(self, camera_id):
        self._template_cfg_path = os.path.join(os.path.dirname(__file__), 'hands_on_demo.json')

        with open(self._template_cfg_path) as f:
            cfg = json.load(f)

        self._model_dir = os.path.join('.temp', 'hands_on_demo_model')
        self._dataset_path = os.path.join(self._model_dir, 'dataset.json')
        self._cfg_path = os.path.join(self._model_dir, 'config.json')

        self._dataset = None
        self._scale_factor = None
        self._mode = self.__class__.Mode.DETECT
        self._localizer = None
        self._object_size = cfg['object_size']
        self._camera_image = None
        self._view_image = None
        self._image_idx = 0
        self._key = -1
        os.makedirs(self._model_dir, exist_ok=True)
        shutil.copyfile(self._template_cfg_path, self._cfg_path)

        self._camera = cv2.VideoCapture(camera_id)
        self._load_model()

    def run(self):
        while True:
            self._key = cv2.waitKey(1)
            if self._key == ord('q'):
                break
            elif self._key == ord('n'):
                self._mode = self.__class__.Mode.NEW_MODEL
                self._image_idx = 0
                self._dataset = []
            elif self._key == ord('r'):
                self._train()  # Retrain model on existing data, useful for tests.

            ret, camera_frame = self._camera.read()
            if camera_frame is None:
                print('Cannot read camera frame')
                continue

            if camera_frame.ndim != 3 or camera_frame.shape[2] != 3:
                print('Must be a color image')
                sys.exit(0)

            camera_frame = np.fliplr(camera_frame)

            if self._scale_factor is None:
                actual_length = np.max(camera_frame.shape[:2])
                desired_length = self._object_size * 6
                self._scale_factor = desired_length / actual_length
                self._view_shape = (int(self._scale_factor * camera_frame.shape[0] + 100),
                                    min(int(self._scale_factor * camera_frame.shape[1]) + 1, 640), 3)

            self._camera_image = cv2.resize(camera_frame, (0, 0), fx=self._scale_factor, fy=self._scale_factor)
            self._view_image = np.zeros(self._view_shape, dtype=self._camera_image.dtype)
            self._view_image[:self._camera_image.shape[0], :self._camera_image.shape[1], :] = self._camera_image

            if self._mode == self.__class__.Mode.DETECT:
                self._put_text('Press n to train new model, q to quit', 50)
                self._detect()
            else:
                self._new_model()

            cv2.imshow('camera', self._view_image)

    def _draw_pose(self, x, y, angle, color=(0, 255, 0)):
        arrow = np.array([0, 0, 0, -1, 0, -1, .1, -.8, 0, -1, -.1, -0.8]).reshape(-1, 2)
        t = utils.make_transform2(self._object_size / 2, angle, x, y)
        arrow = np.dot(np.append(arrow, np.ones((arrow.shape[0], 1)), axis=1), t.T)[:, :2].astype(int)
        for i in range(0, len(arrow), 2):
            cv2.line(self._view_image, tuple(arrow[i]), tuple(arrow[i + 1]), color, thickness=2)
        cv2.circle(self._view_image, (int(x), int(y)), self._object_size // 2, color, thickness=2)

    def _put_text(self, text, y, color=(0, 255, 0)):
        cv2.putText(self._view_image,
                    text,
                    (10, self._camera_image.shape[0] + y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    color,
                    1)

    def _detect(self):
        try:
            if self._localizer:
                input = self._make_input(self._camera_image)
                objects = self._localizer.predict(input)
                for obj in objects:
                    self._draw_pose(obj.origin[0], obj.origin[1], obj.angle)
                self._put_text('Detecting. Show object to the camera.', 20)
        except Exception:
            traceback.print_exc()
            self._localizer = None
        if not self._localizer:
            self._put_text('No model loaded', 80, (0, 0, 255))

    def _make_input(self, image):
        image = image.astype(np.float32) / 255
        image = np.power(image, 0.5)  # A slight gamma-correction
        return image

    def _load_model(self):
        try:
            self._localizer = predict.Localizer(self._cfg_path)
            self._mode = self.__class__.Mode.DETECT
        except Exception:
            traceback.print_exc()
            self._localizer = None

    def _new_model(self):
        s = self._camera_image.shape
        positions = [
            [s[1] / 2, s[0] / 2],
            [self._object_size, self._object_size],
            [s[1] - self._object_size, self._object_size],
            [s[1] - self._object_size, s[0] - self._object_size],
            [self._object_size, s[0] - self._object_size],
        ]

        angle = 2 * np.pi / len(positions) * self._image_idx
        self._draw_pose(*positions[self._image_idx], angle)

        if self._key == ord(' '):
            image_file = datetime.datetime.now().strftime(f'image-{self._image_idx}.png')
            image_path = os.path.join(self._model_dir, image_file)
            input_image = self._make_input(self._camera_image) * 255
            cv2.imwrite(image_path, input_image)
            data_element = {
                "image": image_file,
                "objects": [
                    {
                        "category": 0,
                        "origin": {
                            "x": positions[self._image_idx][0],
                            "y": positions[self._image_idx][1],
                            "angle": angle
                        }
                    }
                ]
            }
            self._dataset.append(data_element)
            self._image_idx += 1
            if self._image_idx == len(positions):
                with open(self._dataset_path, 'w') as f:
                    json.dump(self._dataset, f, indent=' ')
                self._train()
        else:
            self._put_text(f'Image #{self._image_idx + 1} of {len(positions)}', 20)
            self._put_text('Place object to shown position and orientation', 50)
            self._put_text('and press space', 80)

    def _train(self):
        self._put_text('Training, please wait...', 20)
        cv2.imshow('camera', self._view_image)
        cv2.waitKey(1)
        train.configure_logging(self._cfg_path)
        trainer = train.Trainer(self._cfg_path)
        trainer.run()
        plt.close()
        self._load_model()
        print('Training done!')


if __name__ == '__main__':
    camera_id = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    app = HandsOnDemo(camera_id)
    app.run()
