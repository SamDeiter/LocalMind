"""
formatter.py - Structured Report Formatter (Phase F2)
=====================================================
Raw notes -> MedGemma -> structured report.
"""
class ReportFormatter:
    def format_notes(self, raw_notes: str) -> str:
        return f"# Structured Report\n\n{raw_notes}"
