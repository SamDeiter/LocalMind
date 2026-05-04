import hashlib
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("localmind.security.integrity")

class ContentMinter:
    """
    Provides cryptographic 'minting' of AI outputs to ensure integrity.
    Prevents manipulation of reports, logs, and mission results.
    """
    
    def __init__(self, key_path: str = "data/keys/integrity.key"):
        self.key_path = key_path
        self._ensure_key_exists()

    def _ensure_key_exists(self):
        """Generates a salt/key for HMAC if it doesn't exist."""
        os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
        if not os.path.exists(self.key_path):
            key = secrets.token_hex(32)
            with open(self.key_path, "w") as f:
                f.write(key)
            logger.info("Generated new integrity key for content minting")

    def _get_key(self) -> str:
        with open(self.key_path, "r") as f:
            return f.read().strip()

    def mint(self, request_data: Any, result_data: Any, metadata: Dict[str, Any]) -> str:
        """
        Creates an immutable seal for the data pair.
        Returns a 'Mint Token' (the cryptographic hash + signature).
        """
        payload = {
            "req": request_data,
            "res": result_data,
            "meta": {
                **metadata,
                "ts": datetime.utcnow().isoformat()
            }
        }
        
        # Consistent JSON serialization
        canonical_payload = json.dumps(payload, sort_keys=True)
        
        # Create SHA-256 Hash
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        
        # Create 'Signature' (using HMAC for local verification)
        import hmac
        key = self._get_key().encode()
        signature = hmac.new(key, payload_hash.encode(), hashlib.sha256).hexdigest()
        
        # The Minted Token: VERSION:HASH:SIGNATURE
        return f"LM1:{payload_hash}:{signature}"

    def verify(self, request_data: Any, result_data: Any, metadata: Dict[str, Any], mint_token: str) -> bool:
        """
        Verifies that a piece of content matches its minted token.
        """
        try:
            parts = mint_token.split(":")
            if len(parts) != 3 or parts[0] != "LM1":
                return False
            
            # Reconstruct payload (metadata must contain the original timestamp)
            payload = {
                "req": request_data,
                "res": result_data,
                "meta": metadata # This must be the EXACT meta (including 'ts') from mint time
            }
            
            canonical_payload = json.dumps(payload, sort_keys=True)
            current_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
            
            import hmac
            key = self._get_key().encode()
            expected_sig = hmac.new(key, current_hash.encode(), hashlib.sha256).hexdigest()
            
            return hmac.compare_digest(expected_sig, parts[2])
            
        except Exception as e:
            logger.error(f"Verification failure: {e}")
            return False

    def sign_tool(self, tool_name: str, code: str) -> str:
        """
        Creates an integrity token for a tool's code block.
        Used to ensure that 'Skill' code is authentic and has not been modified.
        """
        payload = {
            "name": tool_name,
            "code": code.strip()
        }
        canonical = json.dumps(payload, sort_keys=True)
        payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
        
        import hmac
        key = self._get_key().encode()
        signature = hmac.new(key, payload_hash.encode(), hashlib.sha256).hexdigest()
        
        return f"TOOL1:{payload_hash}:{signature}"

    def verify_tool(self, tool_name: str, code: str, token: str) -> bool:
        """
        Verifies that the provided code matches the integrity token.
        """
        try:
            parts = token.split(":")
            if len(parts) != 3 or parts[0] != "TOOL1":
                return False

            payload = {
                "name": tool_name,
                "code": code.strip()
            }
            canonical = json.dumps(payload, sort_keys=True)
            current_hash = hashlib.sha256(canonical.encode()).hexdigest()

            import hmac
            key = self._get_key().encode()
            expected_sig = hmac.new(key, current_hash.encode(), hashlib.sha256).hexdigest()

            return hmac.compare_digest(expected_sig, parts[2])
        except Exception:
            return False

# Global instance for the system
minter = ContentMinter()
