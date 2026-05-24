import unittest
from unittest.mock import patch, MagicMock
import model_manager

class TestSecuritySEC004(unittest.TestCase):
    @patch('model_manager.subprocess.run')
    @patch('model_manager.resolve_model_key')
    def test_load_model_command_injection(self, mock_resolve, mock_subprocess_run):
        """
        SEC-004: Command injection via model key.
        Verifies that user input is not passed directly to subprocess without validation.
        """
        # Simulate a malicious model key containing shell metacharacters
        malicious_key = "test_model.gguf; echo pwned"
        
        # Mock resolve_model_key to return the malicious key (simulating lack of sanitization)
        mock_resolve.return_value = malicious_key
        
        # Call load_model with the malicious key
        success, message = model_manager.load_model(malicious_key)
        
        # Verify subprocess.run was called
        self.assertTrue(mock_subprocess_run.called)
        
        # Get the command list passed to subprocess.run
        call_args = mock_subprocess_run.call_args
        args_list = call_args[0][0]  # The first positional argument is the command list
        
        # Check if the malicious key is present in the command arguments
        # If it is, the bug exists (Red phase)
        self.assertNotIn(malicious_key, args_list, 
                         "SEC-004 Bug confirmed: Malicious model key passed directly to subprocess")

if __name__ == '__main__':
    unittest.main()
