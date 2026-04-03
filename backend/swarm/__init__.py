"""
Swarm package — Multi-agent parallel processing for LocalMind.
Leverages the Threadripper's 64 cores for CPU-bound work while
gating GPU-bound LLM inference behind a semaphore.
"""
