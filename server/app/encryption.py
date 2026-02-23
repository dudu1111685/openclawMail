from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_fernet = Fernet(settings.mailbox_encryption_key.encode())


def encrypt_content(plaintext: str) -> str:
    """Encrypt a string and return the Fernet token as a UTF-8 string."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_content(ciphertext: str) -> str:
    """Decrypt a Fernet token string back to plaintext."""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Legacy plaintext messages stored before encryption was enabled
        return ciphertext
