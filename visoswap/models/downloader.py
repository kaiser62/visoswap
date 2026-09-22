# Vendored from VisoMaster (https://github.com/visomaster/VisoMaster).
# VisoMaster is licensed GPLv3; this vendored copy inherits that license.
# This file was vendored into VisoSwap and may have been modified from upstream.
# See NOTICE for vendoring provenance and LICENSE for the full GPLv3 text.

import requests
from pathlib import Path
import os

from tqdm import tqdm

from visoswap.models.integrity_checker import check_file_integrity

def download_file(model_name: str, file_path: str, correct_hash: str, url: str) -> bool:
    """
    Downloads a file and verifies its integrity.

    Parameters:
    - model_name (str): Name of the model being downloaded.
    - file_path (str): Path where the file will be saved.
    - correct_hash (str): Expected hash value of the file for integrity check.
    - url (str): URL to download the file from.

    Returns:
    - bool: True if the file is downloaded and verified successfully, False otherwise.
    """
    # Remove the file if it already exists and restart download
    if Path(file_path).is_file():
        if check_file_integrity(file_path, correct_hash):
            print(f"\nSkipping {model_name} as it is already downloaded!")
            return True
        else:  
            print(f"\n{file_path} already exists, but its file integrity couldn't be verified. Re-downloading it!")
            os.remove(file_path)

    # Ensure parent directory exists (e.g. for subfolders like liveportrait_onnx/)
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading {model_name} from {url}")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VisoSwap/1.0"}
    max_attempts = 3
    attempt = 1
    block_size = 65536  # 64KB chunks

    while attempt <= max_attempts:
        try:
            response = requests.get(url, stream=True, timeout=30, headers=headers)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))

            with tqdm(total=total_size, unit="B", unit_scale=True, desc=model_name) as progress_bar:
                with open(file_path, "wb") as file:
                    for data in response.iter_content(chunk_size=block_size):
                        if data:
                            progress_bar.update(len(data))
                            file.write(data)
            
            # Verify file integrity
            if check_file_integrity(file_path, correct_hash):
                print("File integrity verified successfully!")
                print(f"File saved at: {file_path}")
                return True
            else:
                print(f"Integrity check failed for {file_path}. Retrying download (Attempt {attempt}/{max_attempts})...")
                os.remove(file_path)
                attempt += 1
        except requests.exceptions.Timeout:
            print("Connection timed out! Retrying download...")
            attempt += 1
        except Exception as e:
            print(f"An error occurred during download: {e}")
            attempt += 1

    print(f"Failed to download {model_name} after {max_attempts} attempts.")
    return False