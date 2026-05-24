import os
import sys
sys.path.insert(0, os.getcwd())
from api_server import app

def test_upload_filename_double_dot_rejected():
    """
    Test that a filename of '..' is rejected to prevent path traversal.
    The current sanitizer keeps dots, so '..' becomes '..'.
    os.path.join(UPLOAD_DIR, '..') resolves outside UPLOAD_DIR.
    """
    with app.test_client() as client:
        # Filename consisting only of dots should be rejected
        data = {'file': (b'..', b'malicious_content')}
        resp = client.post('/api/file/upload', data=data, content_type='multipart/form-data')
        
        assert resp.status_code == 400, f"Expected 400 for '..' filename, got {resp.status_code}"
        json_resp = resp.get_json()
        assert not json_resp['success'], "Filename '..' should be rejected to prevent path traversal"

def test_image_upload_filename_double_dot_rejected():
    with app.test_client() as client:
        data = {'image': (b'..', b'fake_png_data')}
        resp = client.post('/api/image/upload', data=data, content_type='multipart/form-data')
        
        assert resp.status_code == 400, f"Expected 400 for '..' filename, got {resp.status_code}"
        json_resp = resp.get_json()
        assert not json_resp['success'], "Filename '..' should be rejected to prevent path traversal"