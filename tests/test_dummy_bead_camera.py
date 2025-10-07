import unittest
import numpy as np
from scipy import ndimage
from magscope.camera import DummyBeadCamera


class MockVideoBuffer:
    """Simple mock for VideoBuffer to capture camera frames."""
    def __init__(self):
        self.images = []
        self.timestamps = []

    def write_image_and_timestamp(self, image_bytes, timestamp):
        self.images.append(image_bytes)
        self.timestamps.append(timestamp)


def find_bright_spots(image: np.ndarray, threshold_factor=3.0, min_area=5):
    """
    Identify centroids of connected bright regions above mean + Nσ.
    Returns a list of (x, y) centroids.
    """
    mean, std = image.mean(), image.std()
    mask = image > (mean + threshold_factor * std)
    labeled, num = ndimage.label(mask)
    centroids = []
    for label_id in range(1, num + 1):
        coords = np.column_stack(np.where(labeled == label_id))
        if len(coords) < min_area:
            continue
        centroid = coords.mean(axis=0)
        centroids.append(centroid[::-1])  # flip to (x, y)
    return np.array(centroids)


class TestDummyBeadCameraPositions(unittest.TestCase):
    """Functional test to verify that bead images appear in correct positions."""

    def setUp(self):
        self.cam = DummyBeadCamera()
        self.vb = MockVideoBuffer()
        self.cam.connect(self.vb)

        # Override beads with deterministic known positions
        self.cam._fixed = [
            {'x': -10.0, 'y': -10.0, 'z': 0.0, 'I': 1.0},
            {'x': 0.0,   'y':  0.0,  'z': 0.0, 'I': 1.0},
            {'x': 10.0,  'y': 10.0,  'z': 0.0, 'I': 1.0},
        ]
        self.cam._teth = []  # disable tethered motion for clarity

    def test_beads_visible_and_in_position(self):
        """Confirm bright regions exist near expected bead coordinates."""
        self.cam.fetch()
        self.assertGreater(len(self.vb.images), 0, "Camera did not produce an image")

        img = np.frombuffer(self.vb.images[0], dtype=self.cam.dtype).reshape(self.cam.height, self.cam.width)

        # Locate bright regions
        centroids = find_bright_spots(img, threshold_factor=3.0)
        self.assertGreaterEqual(len(centroids), 3, "Expected ≥3 bright regions")

        # Compute expected positions (in px)
        um_per_px = self.cam._um_per_px
        expected_px = [
            np.array([self.cam.width / 2 + b['x'] / um_per_px,
                      self.cam.height / 2 - b['y'] / um_per_px])
            for b in self.cam._fixed
        ]

        # Check each expected bead has a bright centroid nearby
        for exp in expected_px:
            dists = np.linalg.norm(centroids - exp, axis=1)
            min_dist = dists.min() if len(dists) else np.inf
            self.assertLess(
                min_dist, 20,
                f"No bright region within 20px of expected bead at {exp} (nearest {min_dist:.1f})"
            )

        print("Detected centroids:", centroids)
        print("Expected positions:", expected_px)
        print("Bead position test passed ✅")


if __name__ == "__main__":
    unittest.main()
