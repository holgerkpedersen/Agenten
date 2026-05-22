import os
import sys
import pytest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from api_server import app, UPLOAD_DIR

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_upload_file_path_traversal(client):
    """Test that upload_file rejects or sanitizes path traversal attempts."""
    malicious_filename = "../../../etc/passwd.txt"
    data = {
        'file': (BytesIO(b"malicious content"), malicious_filename)
    }
    response = client.post('/api/file/upload', data=data, content_type='multipart/form-data')
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True
    
    saved_path = result['filepath']
    real_upload_dir = os.path.realpath(UPLOAD_DIR)
    real_saved_path = os.path.realpath(saved_path)
    
    # Ensure the file is strictly inside UPLOAD_DIR
    assert real_saved_path.startswith(real_upload_dir), f"File escaped upload directory: {saved_path}"

def test_image_upload_path_traversal(client):
    """Test that image_upload rejects or sanitizes path traversal attempts."""
    malicious_filename = "../../etc/shadow.png"
    data = {
        'image': (BytesIO(b"fake png data"), malicious_filename)
    }
    response = client.post('/api/image/upload', data=data, content_type='multipart/form-data')
    
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True
    
    saved_path = result['filepath']
    real_upload_dir = os.path.realpath(UPLOAD_DIR)
    real_saved_path = os.path.realpath(saved_path)
    
    assert real_saved_path.startswith(real_upload_dir), f"Image escaped upload directory: {saved_path}"
