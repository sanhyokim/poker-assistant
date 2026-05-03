"""Tests for capture module."""

from collections.abc import Iterator
import pathlib
import tempfile

import cv2
import numpy as np
import pytest

from capture import create_capture
from capture.base_capture import BaseCapture
from capture.card_capture import CardCapture
from capture.file_capture import FileCapture
from capture.mss_capture import MssCapture


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_path() -> Iterator[pathlib.Path]:
    """Return a temporary directory inside the project workspace."""
    temp_root = PROJECT_ROOT / "data"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
        yield pathlib.Path(temp_dir)


class TestBaseCapture:
    """Verify BaseCapture is abstract and cannot be instantiated."""

    def test_cannot_instantiate_base(self) -> None:
        """BaseCapture raises TypeError on direct instantiation."""
        with pytest.raises(TypeError):
            BaseCapture()  # type: ignore[abstract]

    def test_default_reconnect_returns_false(self, tmp_path: pathlib.Path) -> None:
        """Default reconnect implementation reports unsupported."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        file_path = tmp_path / "test.png"
        cv2.imwrite(str(file_path), img)

        cap = FileCapture(file_path)
        assert cap.reconnect() is False


class TestFileCaptureWithSingleFile:
    """Test FileCapture with a single image file."""

    def test_single_file_returns_bgr_ndarray(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Single file mode returns a BGR numpy array."""
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        img[100, 100] = [255, 0, 0]  # Blue pixel
        file_path = tmp_path / "test.png"
        cv2.imwrite(str(file_path), img)

        cap = FileCapture(file_path)
        frame = cap.get_frame()

        assert frame is not None
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (1080, 1920, 3)
        assert frame.dtype == np.uint8

    def test_single_file_is_open(self, tmp_path: pathlib.Path) -> None:
        """is_open() returns True before read, False after."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        file_path = tmp_path / "test.png"
        cv2.imwrite(str(file_path), img)

        cap = FileCapture(file_path)
        assert cap.is_open() is True

        cap.get_frame()
        assert cap.is_open() is False

    def test_single_file_returns_none_after_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Second get_frame() call returns None for single file."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        file_path = tmp_path / "test.png"
        cv2.imwrite(str(file_path), img)

        cap = FileCapture(file_path)
        cap.get_frame()
        assert cap.get_frame() is None


class TestFileCaptureWithDirectory:
    """Test FileCapture with a directory of images."""

    def test_directory_sequential_read(self, tmp_path: pathlib.Path) -> None:
        """Directory mode reads files in sorted order."""
        for i in range(3):
            img = np.full((100, 100, 3), i * 80, dtype=np.uint8)
            cv2.imwrite(str(tmp_path / f"frame_{i:03d}.png"), img)

        cap = FileCapture(tmp_path)
        frames = []
        while cap.is_open():
            frame = cap.get_frame()
            if frame is not None:
                frames.append(frame)

        assert len(frames) == 3

    def test_directory_returns_none_after_all_read(
        self, tmp_path: pathlib.Path
    ) -> None:
        """get_frame() returns None after all files are read."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / "frame_001.png"), img)

        cap = FileCapture(tmp_path)
        cap.get_frame()
        assert cap.get_frame() is None

    def test_empty_directory(self, tmp_path: pathlib.Path) -> None:
        """Empty directory results in is_open() = False."""
        cap = FileCapture(tmp_path)
        assert cap.is_open() is False
        assert cap.get_frame() is None


class TestFileCaptureRelease:
    """Test FileCapture release and reset."""

    def test_release(self, tmp_path: pathlib.Path) -> None:
        """release() closes the capture source."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imwrite(str(tmp_path / "test.png"), img)

        cap = FileCapture(tmp_path)
        assert cap.is_open() is True
        cap.release()
        assert cap.is_open() is False
        assert cap.get_frame() is None

    def test_reset(self, tmp_path: pathlib.Path) -> None:
        """reset() allows re-reading from the beginning."""
        for i in range(2):
            img = np.full((100, 100, 3), i * 100, dtype=np.uint8)
            cv2.imwrite(str(tmp_path / f"frame_{i:03d}.png"), img)

        cap = FileCapture(tmp_path)
        cap.get_frame()
        cap.get_frame()
        assert cap.is_open() is False

        cap.reset()
        assert cap.is_open() is True
        frame = cap.get_frame()
        assert frame is not None


class TestFileCaptureInvalidPath:
    """Test FileCapture with invalid paths."""

    def test_nonexistent_path(self) -> None:
        """Non-existent path results in is_open() = False."""
        cap = FileCapture("/nonexistent/path")
        assert cap.is_open() is False
        assert cap.get_frame() is None


class TestCardCapture:
    """Test CardCapture instantiation when device may not be available."""

    def test_card_capture_no_device(self) -> None:
        """CardCapture with invalid device index has is_open()=False."""
        cap = CardCapture(device_index=99)
        assert cap.is_open() is False
        assert cap.get_frame() is None
        cap.release()

    def test_card_capture_release_safe(self) -> None:
        """release() is safe to call even if device was not opened."""
        cap = CardCapture(device_index=99)
        cap.release()
        assert cap.is_open() is False

    def test_card_capture_reconnect_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reconnect() releases and reopens the configured capture device."""

        class FakeVideoCapture:
            """OpenCV VideoCapture test double."""

            instances: list["FakeVideoCapture"] = []

            def __init__(self, device_index: int, backend: int) -> None:
                self.device_index = device_index
                self.backend = backend
                self.released = False
                self.set_calls: list[tuple[int, float]] = []
                FakeVideoCapture.instances.append(self)

            def isOpened(self) -> bool:
                """Return opened state."""
                return not self.released

            def set(self, prop: int, value: float) -> bool:
                """Record property writes."""
                self.set_calls.append((prop, value))
                return True

            def release(self) -> None:
                """Mark the fake device released."""
                self.released = True

        monkeypatch.setattr(cv2, "VideoCapture", FakeVideoCapture)
        monkeypatch.setattr("capture.card_capture.time.sleep", lambda _seconds: None)

        cap = CardCapture(device_index=2, width=1280, height=720, fps=30)
        first = FakeVideoCapture.instances[0]

        assert cap.reconnect() is True

        assert first.released is True
        assert len(FakeVideoCapture.instances) == 2
        assert FakeVideoCapture.instances[1].device_index == 2
        assert cap.is_open() is True

    def test_card_capture_reconnect_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reconnect() returns False when the device cannot be opened."""

        class ClosedVideoCapture:
            """VideoCapture fake that never opens."""

            def __init__(self, _device_index: int, _backend: int) -> None:
                self.released = False

            def isOpened(self) -> bool:
                """Return closed state."""
                return False

            def set(self, _prop: int, _value: float) -> bool:
                """Accept property writes."""
                return True

            def release(self) -> None:
                """Mark release."""
                self.released = True

        monkeypatch.setattr(cv2, "VideoCapture", ClosedVideoCapture)
        monkeypatch.setattr("capture.card_capture.time.sleep", lambda _seconds: None)

        cap = CardCapture(device_index=2)

        assert cap.reconnect() is False
        assert cap.is_open() is False


class TestMssCapture:
    """Test MssCapture basic functionality."""

    def test_mss_capture_is_open(self) -> None:
        """MssCapture should be open after initialization."""
        cap = MssCapture()
        assert cap.is_open() is True
        cap.release()

    def test_mss_capture_returns_ndarray(self) -> None:
        """MssCapture.get_frame() returns a BGR numpy array."""
        cap = MssCapture()
        frame = cap.get_frame()
        assert frame is not None
        assert isinstance(frame, np.ndarray)
        assert frame.ndim == 3
        assert frame.shape[2] == 3
        cap.release()

    def test_mss_capture_release(self) -> None:
        """release() closes the capture source."""
        cap = MssCapture()
        cap.release()
        assert cap.is_open() is False
        assert cap.get_frame() is None


class TestCreateCapture:
    """Test the create_capture factory function."""

    def test_create_file_capture(self, tmp_path: pathlib.Path) -> None:
        """'file' method creates FileCapture instance."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        file_path = tmp_path / "test.png"
        cv2.imwrite(str(file_path), img)

        config = {
            "capture": {
                "method": "file",
                "file_path": str(file_path),
            }
        }
        cap = create_capture(config)
        assert isinstance(cap, FileCapture)
        assert cap.is_open() is True
        cap.release()

    def test_create_mss_capture(self) -> None:
        """'mss' method creates MssCapture instance."""
        config = {"capture": {"method": "mss"}}
        cap = create_capture(config)
        assert isinstance(cap, MssCapture)
        assert cap.is_open() is True
        cap.release()

    def test_create_card_capture(self) -> None:
        """'capture_card' method creates CardCapture instance."""
        config = {
            "capture": {
                "method": "capture_card",
                "device_index": 99,
            }
        }
        cap = create_capture(config)
        assert isinstance(cap, CardCapture)
        cap.release()

    def test_create_unknown_method_raises(self) -> None:
        """Unknown method raises ValueError."""
        config = {"capture": {"method": "unknown"}}
        with pytest.raises(ValueError, match="Unknown capture method"):
            create_capture(config)

    def test_create_file_without_path_raises(self) -> None:
        """'file' method without file_path raises ValueError."""
        config = {"capture": {"method": "file"}}
        with pytest.raises(ValueError, match="file_path is required"):
            create_capture(config)
