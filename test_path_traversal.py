import pytest
from api_server import app
import os

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

class TestPathTraversalVulnerability:
    """
    Tester for SEC-013: Path Traversal Vulnerability.
    Disse tests forventer at path traversal forsøg bliver blokeret (400/403).
    Hvis angrebet lykkes (200 OK med filindhold), fejler testen.
    """

    def test_preview_export_path_traversal(self, client):
        """
        Test at preview_export afviser path traversal forsøg.
        Angriber forsøger at læse en fil uden for export_folder ved hjælp af ../
        """
        # Forsøg på at læse api_server.py via exports endpointet
        malicious_path = "../../api_server.py"
        response = client.get(f'/preview-exports/{malicious_path}')
        
        # Vi forventer at serveren blokerer dette (400 Bad Request eller 403 Forbidden)
        # Hvis den returnerer 200 OK, er path traversal mulig
        assert response.status_code in [400, 403], f"Path traversal ikke blokeret! Status: {response.status_code}"

    def test_read_file_path_traversal(self, client):
        """
        Test at read_file afviser path traversal forsøg.
        Angriber sender en sti der peger uden for den tilladte mappe.
        """
        # Forsøg på at læse /etc/passwd eller api_server.py
        malicious_path = os.path.join(os.path.dirname(app.config.get('BASE_DIR', '.')), '..', 'api_server.py')
        
        response = client.post('/api/file/read', json={"filepath": malicious_path})
        data = response.get_json()
        
        # Vi forventer en fejl fra serveren pga. sikkerhedstjek
        assert response.status_code in [400, 403], f"Path traversal ikke blokeret i read_file! Status: {response.status_code}"
        if data:
            assert 'error' in data or not data.get('success'), "Serveren returnerede succes for en malicious path"

    def test_list_folder_contents_path_traversal(self, client):
        """
        Test at list_folder_contents afviser path traversal forsøg.
        """
        # Forsøg på at liste root mappen eller system mapper
        malicious_path = "/etc"
        
        response = client.post('/api/folder/list', json={"path": malicious_path})
        data = response.get_json()
        
        assert response.status_code in [400, 403], f"Path traversal ikke blokeret i list_folder! Status: {response.status_code}"

    def test_save_to_folder_path_traversal(self, client):
        """
        Test at save_to_folder afviser path traversal forsøg.
        Angriber forsøger at skrive en fil til en uautoriseret placering.
        """
        # Forsøg på at overskrive en systemfil eller skrive uden for mappen
        malicious_filename = "../../../tmp/malicious_test_file.txt"
        
        response = client.post('/api/folder/save', json={
            "filename": malicious_filename,
            "content": "Malicious content",
            "path": "/safe/base/path" # selvom path er safe, filename kan indeholde traversal
        })
        data = response.get_json()
        
        assert response.status_code in [400, 403], f"Path traversal ikke blokeret i save_to_folder! Status: {response.status_code}"
