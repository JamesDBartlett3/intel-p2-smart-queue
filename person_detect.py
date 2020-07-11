import os
import cv2
import sys
import time
import argparse
import traceback
import numpy as np
from openvino.inference_engine import IECore


def layer_support_checker(core, net, dev):
    """
    Check if all layers in the given network are supported
    on the given device and return the answer as boolean value
    """
    all_layers = net.layers.keys()
    supported_layers = core.query_nework(net, dev)
    return_value = True
    for l in all_layers:
        if l not in supported_layers:
            return_value = False
            print("Layer ", l, " support not available for ", dev)
    if return_value:
        print(dev, " supports all layers for this model!")
    return return_value


class Queue:
    """
    Class for dealing with queues
    """

    def __init__(self):
        self.queues = []

    # Points in the frame where the queues should be
    def add_queue(self, points):
        self.queues.append(points)

    # Get bounding area of queues based on the queue points
    def get_queues(self, image):
        for q in self.queues:
            x_min, y_min, x_max, y_max = q
            frame = image[y_min:y_max, x_min:x_max]
            yield frame

    # Check if incoming coordinates are within the queue
    def check_coords(self, coords):
        d = {k + 1: 0 for k in range(len(self.queues))}
        for coord in coords:
            for i, q in enumerate(self.queues):
                if coord[0] > q[0] and coord[2] < q[2]:
                    d[i + 1] += 1
        return d


class PersonDetect:
    """
    Class for the Person Detection Model.
    """

    # Setup colors for drawing bounding boxes and text on output image
    a_red = [0, 0, 185]
    a_green = [0, 185, 0]
    a_blue = [185, 0, 0]
    a_magenta = [185, 0, 245]
    l_red = list(a_red)
    l_green = list(a_green)
    l_blue = list(a_blue)
    l_magenta = list(a_magenta)

    def __init__(self, model_name, device, threshold=0.60):
        self.core = IECore()
        self.model_weights = model_name + ".bin"
        self.model_structure = model_name + ".xml"
        self.device = device
        self.threshold = threshold

        try:
            # Create IENetwork object from IR model
            self.model = self.core.read_network(
                self.model_structure, self.model_weights
            )
        except Exception:
            raise ValueError(
                "Could not Initialise the network. Have you enterred the correct model path?"
            )

        self.input_name = next(iter(self.model.inputs))
        self.input_shape = self.model.inputs[self.input_name].shape
        self.output_name = next(iter(self.model.outputs))
        self.output_shape = self.model.outputs[self.output_name].shape
        self.net = None

    # Load the model
    def load_model(self, device):
        layer_support_checker(self.core, self.model, self.device)
        self.network = self.core.load_network(self.model, self.device, 1)

    # Get frame, run inference, return boxes with detected people
    def predict(self, image, w, h):
        _input = self.preprocess_input(image)
        start_time = time.time()
        request = self.network.start_async(
            request_id=0, inputs={self.input_name: _input}
        )
        if request.wait() == 0:
            inf_time = time.time() - start_time
            outs = request.outputs[self.output_name]
            bb_xy = self.preprocess_outputs(outs, image)
            bounding_boxes, output_image = self.draw_outputs(bb_xy, image)
            timer_text = "Inference Time: {:.2f} milliseconds".format(inf_time / 0.001)
            cv2.putText(
                image,
                timer_text,
                (30, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                self.l_blue,
                2,
                None,
                None,
            )
        return bounding_boxes, output_image

    def draw_outputs(self, coords, image):
        copy = image.copy()
        for c in coords:
            cv2.rectangle(copy, c[:2], c[2:], self.l_green, 1)
        return copy

    def preprocess_outputs(self, outputs, image):
        h, w = image.shape[0:2]
        coords = []
        for b in outputs[0][0]:
            if b[2] >= self.threshold:
                xmin = int(b[3] * w)
                ymin = int(b[4] * h)
                xmax = int(b[5] * w)
                ymax = int(b[6] * h)
                coords.append((xmin, ymin, xmax, ymax))
        return coords

    def preprocess_input(self, image):
        copy = image
        copy = cv2.resize(copy, (self.input_shape[3], self.input_shape[2]))
        copy = copy.transpose((2, 0, 1))
        copy = copy.reshape(1, 3, self.input_shape[2], self.input_shape[3])
        return copy


def main(args):
    model = args.model
    device = args.device
    video_file = args.video
    max_people = args.max_people
    threshold = args.threshold
    output_path = args.output_path

    start_model_load_time = time.time()
    pd = PersonDetect(model, device, threshold)
    pd.load_model(device)
    total_model_load_time = time.time() - start_model_load_time

    queue = Queue()

    try:
        queue_param = np.load(args.queue_param)
        for q in queue_param:
            queue.add_queue(q)
    except Exception:
        print("error loading queue param file")

    try:
        cap = cv2.VideoCapture(video_file)
    except FileNotFoundError:
        print("Cannot locate video file: " + video_file)
    except Exception as e:
        print("Something else went wrong with the video file: ", e)

    initial_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    initial_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    out_video = cv2.VideoWriter(
        os.path.join(output_path, "output_video.mp4"),
        cv2.VideoWriter_fourcc(*"avc1"),
        fps,
        (initial_w, initial_h),
        True,
    )

    counter = 0
    start_inference_time = time.time()

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            counter += 1

            coords, image = pd.predict(frame, initial_w, initial_h)
            num_people = queue.check_coords(coords)
            print(f"Total People in frame = {len(coords)}")
            print(f"Number of people in queue = {num_people}")
            out_text = ""
            y_pixel = 25

            for k, v in num_people.items():
                out_text += f"No. of People in Queue {k} is {v} "
                if v >= int(max_people):
                    out_text += f" Queue full; Please move to next Queue "
                cv2.putText(
                    image,
                    out_text,
                    (15, y_pixel),
                    cv2.FONT_HERSHEY_COMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                )
                out_text = ""
                y_pixel += 40
            out_video.write(image)

        total_time = time.time() - start_inference_time
        total_inference_time = round(total_time, 1)
        fps = counter / total_inference_time

        with open(os.path.join(output_path, "stats.txt"), "w") as f:
            f.write(str(total_inference_time) + "\n")
            f.write(str(fps) + "\n")
            f.write(str(total_model_load_time) + "\n")

        cap.release()
        cv2.destroyAllWindows()
    except Exception as e:
        print("Could not run Inference: ", e)
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="CPU")
    parser.add_argument("--video", default=None)
    parser.add_argument("--queue_param", default=None)
    parser.add_argument("--output_path", default="/results")
    parser.add_argument("--max_people", default=2)
    parser.add_argument("--threshold", default=0.60)

    args = parser.parse_args()

    main(args)
