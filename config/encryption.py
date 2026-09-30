# config/encryption.py
import base64
import json
import os
from Crypto.Cipher import AES
from django.core.serializers.json import DjangoJSONEncoder

def encrypt_aes_gcm(plain_data_dict, key_hex):
    """
    Encrypts a dictionary using AES-256-GCM.
    Returns: JSON string containing {iv, ciphertext, tag}
    """
    try:
        # Convert hex key to bytes
        key = bytes.fromhex(key_hex)
        
        # Prepare data
        json_data = json.dumps(plain_data_dict, cls=DjangoJSONEncoder).encode('utf-8')
        
        # Generate random IV (Nonce) - GCM standard is 12 bytes
        iv = os.urandom(12) 
        
        # Encrypt
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        ciphertext, tag = cipher.encrypt_and_digest(json_data)
        
        # Return all parts needed for decryption
        return json.dumps({
            "iv": base64.b64encode(iv).decode('utf-8'),
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
            "tag": base64.b64encode(tag).decode('utf-8')
        })
    except Exception as e:
        print(f"Encryption Error: {e}")
        return None

def decrypt_aes_gcm(encrypted_json_str, key_hex):
    """
    Decrypts the {iv, ciphertext, tag} JSON using the Key.
    """
    try:
        data = json.loads(encrypted_json_str)
        key = bytes.fromhex(key_hex)
        
        iv = base64.b64decode(data['iv'])
        ciphertext = base64.b64decode(data['ciphertext'])
        tag = base64.b64decode(data['tag'])
        
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        
        # Verify Tag and Decrypt (This fails if data was tampered with)
        decrypted_data = cipher.decrypt_and_verify(ciphertext, tag)
        
        return json.loads(decrypted_data.decode('utf-8'))
    except Exception as e:
        print(f"Decryption/Tamper Error: {e}")
        return None