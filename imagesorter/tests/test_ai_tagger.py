import os
import pytest
import numpy as np
from PIL import Image
import piexif
from src.ai_tagger import AITagger, write_metadata

@pytest.fixture
def mock_onnx_session(mocker):
    """Mocks the ONNX InferenceSession for hermetic test execution."""
    mock_session = mocker.MagicMock()
    # Mock get_inputs() to return a mock input object
    mock_input = mocker.MagicMock()
    mock_input.name = "input_tensor"
    mock_session.get_inputs.return_value = [mock_input]

    # Mock run() to return a deterministic dummy prediction
    # Set all logits very low so probabilities sum near 1 for our target classes
    dummy_probs = np.full((1, 1000), -100.0)
    dummy_probs[0, 1] = 5.0 # High logit for class 1
    dummy_probs[0, 2] = 4.0 # High logit for class 2
    mock_session.run.return_value = [dummy_probs]

    mocker.patch('onnxruntime.InferenceSession', return_value=mock_session)
    return mock_session

@pytest.fixture
def test_image(tmp_path):
    """Fixture providing a temporary image for processing."""
    img_path = tmp_path / "test.jpg"
    img = Image.new('RGB', (100, 100), color='red')
    img.save(str(img_path))
    return str(img_path)

def test_ai_tagger_init_and_mocked_inference(tmp_path, mock_onnx_session):
    """Test initializing AITagger with mocked ONNX session and running inference."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()

    # Create fake model and labels files so os.path.exists checks pass
    (model_dir / "mobilenetv2.onnx").write_text("fake_model")
    (model_dir / "labels.txt").write_text("class0\nclass1\nclass2\nclass3")

    tagger = AITagger(model_dir=str(model_dir))

    # Verify session loaded via mock
    assert tagger.session is not None
    assert tagger.labels == ["class0", "class1", "class2", "class3"]

def test_preprocessing_normalization(tmp_path, test_image):
    """Test image preprocessing normalization."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    tagger = AITagger(model_dir=str(model_dir))

    input_data = tagger.preprocess(test_image)

    assert input_data is not None
    assert input_data.shape == (1, 3, 224, 224)
    assert input_data.dtype == np.float32

def test_tag_threshold_filtering(tmp_path, mock_onnx_session, test_image):
    """Test getting tags with simulated inference results and filtering."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "mobilenetv2.onnx").write_text("fake_model")
    (model_dir / "labels.txt").write_text("class0\nclass1\nclass2\nclass3\n" + "\n".join([f"class{i}" for i in range(4, 1000)]))

    tagger = AITagger(model_dir=str(model_dir))

    tags = tagger.get_tags(test_image, top_k=2)

    # Based on dummy_probs, class1 and class2 should have highest probs
    assert "class1" in tags
    assert "class2" in tags
    assert len(tags) == 2

def test_exif_writing(tmp_path, test_image):
    """Test writing tags to EXIF XPKeywords."""
    tags = ["test_tag_1", "test_tag_2"]

    write_metadata(test_image, tags, write_exif=True, write_sidecar=False)

    exif_dict = piexif.load(test_image)

    assert "0th" in exif_dict
    assert 40094 in exif_dict["0th"] # XPKeywords tag ID

    # piexif might return a tuple of ints or bytes depending on the tag type and Python version
    xp_keywords = exif_dict["0th"][40094]
    if isinstance(xp_keywords, tuple):
        xp_keywords_bytes = bytes(xp_keywords)
    else:
        xp_keywords_bytes = xp_keywords

    decoded_tags = xp_keywords_bytes.decode('utf-16le').strip('\x00')
    assert decoded_tags == "test_tag_1;test_tag_2"

def test_sidecar_writing(tmp_path, test_image):
    """Test atomic writing of sidecar .txt file."""
    tags = ["test_tag_1", "test_tag_2"]

    write_metadata(test_image, tags, write_exif=False, write_sidecar=True)

    sidecar_path = test_image + ".txt"
    assert os.path.exists(sidecar_path)

    with open(sidecar_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert content == "test_tag_1, test_tag_2"

def test_corrupted_image_handling(tmp_path, mock_onnx_session):
    """Test handling of zero-byte or corrupted image files during AI preprocessing."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "mobilenetv2.onnx").write_text("fake_model")
    (model_dir / "labels.txt").write_text("class0\nclass1")

    tagger = AITagger(model_dir=str(model_dir))

    # Create zero-byte file
    corrupt_img = tmp_path / "corrupt.jpg"
    corrupt_img.write_bytes(b"")

    tags = tagger.get_tags(str(corrupt_img))

    # Preprocessing should fail gracefully, returning empty tags
    assert tags == []
