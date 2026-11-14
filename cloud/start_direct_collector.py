"""Windows launcher for the direct Djak/Planeta collector. Credentials never enter Git."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def protect(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Čuvanje pristupnih podataka podržano je samo na Windowsu.")
    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        success = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        success = crypt32.CryptProtectData(ctypes.byref(source), "Patike direktni sakupljač", None, None, None, 1, ctypes.byref(output))
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.length)
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def main():
    if os.name != "nt":
        raise SystemExit("Ovaj pokretač je za Windows. Na drugim sistemima koristi python -m cloud.direct_collector worker.")
    guard = socket.socket()
    try:
        guard.bind(("127.0.0.1", 53187))
    except OSError:
        raise SystemExit("Sakupljač je već otvoren. Koristi postojeći prozor.")
    missing = [name for name in ("node", "gh") if not shutil.which(name)]
    if missing:
        print("Prvo instaliraj nedostajuće programe u Terminalu:")
        if "node" in missing:
            print("winget install --id OpenJS.NodeJS.LTS -e")
        if "gh" in missing:
            print("winget install --id GitHub.cli -e")
        raise SystemExit("Posle instalacije ponovo otvori pokretač.")
    directory = ROOT / ".venv-collector"
    python = directory / "Scripts" / "python.exe"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(directory)
    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = directory / "requirements.sha256"
    if not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        marker.write_text(digest)
    # Verify direct access before requesting any credentials or advertising readiness.
    print("Proveravam direktno očitavanje Đaka i Planete na ovom računaru...", flush=True)
    subprocess.run([str(python), "-m", "cloud.direct_collector", "probe"], cwd=ROOT, check=True)
    location = Path(os.environ["LOCALAPPDATA"]) / "PatikeCollector" / "credentials.dpapi"
    values = None
    if location.exists():
        try:
            values = json.loads(protect(location.read_bytes(), decrypt=True))
        except (ValueError, OSError):
            print("Sačuvani pristup nije čitljiv. Unesi podatke ponovo.")
    if not values:
        print("Potreban je GitHub fine-grained ključ samo za run-marko/patike:")
        print("Repository permissions: Contents = Read and write, Actions = Read-only.")
        print("Ključ i lozinku unosiš samo ovde, nikada u chat ili kod.")
        values = {"GH_TOKEN": getpass.getpass("GitHub ključ (unos je sakriven): ").strip(),
                  "PAGE_PASSWORD": getpass.getpass("Postojeća lozinka dashboarda (unos je sakriven): ")}
        if not values["GH_TOKEN"].startswith(("github_pat_", "ghp_")) or len(values["PAGE_PASSWORD"]) < 8:
            raise SystemExit("Ključ ili lozinka nisu ispravnog formata.")
        subprocess.run([str(python), "-m", "cloud.direct_collector", "verify"], cwd=ROOT,
                       env={**os.environ, **values}, check=True)
        remember = input("Sačuvati šifrovano za ovaj Windows nalog da ih ne unosiš ponovo? [d/N]: ").strip().lower()
        if remember in {"d", "da", "y", "yes"}:
            location.parent.mkdir(parents=True, exist_ok=True)
            location.write_bytes(protect(json.dumps(values).encode("utf-8")))
    environment = {**os.environ, **values}
    try:
        subprocess.run([str(python), "-u", "-m", "cloud.direct_collector", "worker"], cwd=ROOT, env=environment, check=True)
    finally:
        guard.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSakupljač je zaustavljen.")
    except subprocess.CalledProcessError:
        print("Provera nije uspela. Sakupljač nije aktiviran; pogledaj poruku iznad.")
        raise SystemExit(1)
