# LocalMind Backend Package
from . import db, server

def set_ai_name(name):
    global _ai_name
    _ai_name = name

def get_ai_name():
    return _ai_name if '_ai_name' in globals() else 'LocalMind'
