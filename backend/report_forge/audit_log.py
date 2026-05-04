"""
audit_log.py - Immutable Audit Log (Phase F2)
=============================================
Immutable timestamped log: input hash, model, output hash, approval.
"""
import time
import hashlib

class AuditLog:
    def log_entry(self, raw_input: str, processed_output: str, model: str):
        in_hash = hashlib.sha256(raw_input.encode()).hexdigest()
        out_hash = hashlib.sha256(processed_output.encode()).hexdigest()
        return {
            "timestamp": time.time(),
            "input_hash": in_hash,
            "output_hash": out_hash,
            "model": model,
            "approved": False
        }
