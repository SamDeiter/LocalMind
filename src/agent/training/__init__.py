"""
Training pipeline for improving tool-calling reliability on small local models.

Provides:
- data_generator: synthetic training data in ShareGPT/ChatML format
- finetune: QLoRA fine-tuning script targeting Qwen2.5-Coder-7B-Instruct
"""

from .data_generator import generate_examples, save_dataset, TrainingExample
from .finetune import train, merge_and_export

__all__ = [
    "generate_examples",
    "save_dataset",
    "TrainingExample",
    "train",
    "merge_and_export",
]
