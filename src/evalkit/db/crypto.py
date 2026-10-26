from cryptography.fernet import Fernet


class SecretBox:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def seal(self, plain: str) -> bytes:
        return self._fernet.encrypt(plain.encode())

    def open(self, blob: bytes) -> str:
        return self._fernet.decrypt(blob).decode()
