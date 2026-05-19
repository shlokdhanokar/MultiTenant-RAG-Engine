"""
Phase 4 - Step A1: Encryption Layer
Encrypt and decrypt credentials before storing/reading from MongoDB.
"""
import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


def _get_cipher():
    key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set in .env")
    return Fernet(key.encode())


def encrypt_credential(plaintext):
    """Encrypts a plain token string. Returns encrypted string."""
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext):
    """Decrypts an encrypted token string. Returns plain token."""
    return _get_cipher().decrypt(ciphertext.encode()).decode()
