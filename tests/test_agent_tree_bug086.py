import pytest
from unittest.mock import patch, MagicMock

def test_record_outcome_catches_io_error():
    """BUG-086: record_outcome should catch IOError/disk-full errors gracefully."""
    from agent_tree import record_outcome
    
    mock_agent = MagicMock()
    mock_agent._active_skills = []
    mock_agent.active_template = ""
    
    mock_task_node = MagicMock()
    mock_task_node.name = "test_task"
    mock_task_node.status = "done"
    
    # Simulate disk-full error during tracking
    with patch('skill_tracker.tracker') as m_tracker:
        m_tracker.record.side_effect = IOError("Disk full")
        
        # Should not crash the task execution
        record_outcome(mock_agent, mock_task_node)

def test_evolve_if_needed_catches_io_error():
    """BUG-086: evolve_if_needed should catch IOError/disk-full errors gracefully."""
    from agent_tree import evolve_if_needed
    
    mock_agent = MagicMock()
    
    # Simulate disk-full error during evolution
    with patch('skill_evolution.evolve_if_needed') as m_evo:
        m_evo.side_effect = IOError("Disk full")
        
        # Should not crash the task execution
        evolve_if_needed(mock_agent)
