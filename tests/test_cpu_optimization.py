import pytest
from unittest.mock import patch, MagicMock
from backend.routes.system import hardware_status

@pytest.mark.asyncio
async def test_hardware_status_non_blocking_cpu():
    """Verify that hardware_status calls psutil.cpu_percent with interval=None."""
    with patch("psutil.cpu_percent") as mock_cpu:
        mock_cpu.return_value = 5.0

        # We also need to mock virtual_memory to avoid actual system calls
        with patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value = MagicMock(used=8*1024**3, total=16*1024**3, percent=50.0)

            # Execute the endpoint function
            result = await hardware_status()

            # Verify psutil.cpu_percent was called with interval=None
            mock_cpu.assert_called_with(interval=None)
            assert result["system"]["cpu_percent"] == 5.0
