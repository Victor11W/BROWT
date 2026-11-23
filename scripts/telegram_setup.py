"""Configure Telegram using the Windows clipboard without exposing the bot token."""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request


def is_windows() -> bool:
    return sys.platform == "win32"


def powershell_clipboard(command: str, *, value: str | None = None) -> str:
    """Keep clipboard text out of process arguments, logs and files."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            input=value,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Accès au presse-papiers Windows indisponible.") from None
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sur Windows, lit le jeton BotFather copié dans le presse-papiers, "
            "puis y copie l'identifiant Telegram si une seule conversation est trouvée."
        )
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Saisir le jeton masqué au clavier, sans utiliser le presse-papiers.",
    )
    args = parser.parse_args(argv)
    clipboard_mode = is_windows() and not args.manual
    print("Envoyez /start à votre bot dans une conversation Telegram privée.")
    if clipboard_mode:
        try:
            input("Copiez le jeton BotFather, puis appuyez sur Entrée ici, sans le coller : ")
        except (EOFError, KeyboardInterrupt):
            print("\nPréparation interrompue.")
            return 1
        print("Lecture du presse-papiers Windows (sans afficher le jeton).")
        try:
            token = powershell_clipboard("Get-Clipboard -Raw")
        except RuntimeError as error:
            print(str(error))
            return 1
    else:
        try:
            token = getpass.getpass("Jeton BotFather (saisie masquée) : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaisie interrompue.")
            return 1

    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        if clipboard_mode:
            print("Copiez uniquement le jeton fourni par BotFather, puis relancez le script.")
        elif "\x16" in token:
            print("Le terminal a reçu Ctrl+V au lieu du jeton. Sur Windows, retirez --manual.")
        else:
            print("Le jeton n'a pas le format attendu. Recopiez le jeton complet depuis BotFather.")
        return 1

    try:
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/getUpdates",
            data=json.dumps({"timeout": 0, "allowed_updates": ["message"]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        print(f"Telegram a refusé la requête (HTTP {error.code}). Vérifiez le jeton et le bot.")
        return 1
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        print("Telegram est inaccessible ou a renvoyé une réponse invalide.")
        return 1
    if not isinstance(result, dict) or result.get("ok") is not True:
        print("Telegram n'a pas confirmé la requête.")
        return 1
    if not isinstance(result.get("result"), list):
        print("Telegram a renvoyé une réponse inattendue.")
        return 1

    chats = set()
    for item in result["result"]:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        text = message.get("text")
        if (
            isinstance(chat, dict)
            and chat.get("type") == "private"
            and type(chat.get("id")) is int
            and isinstance(text, str)
            and text.split(maxsplit=1)[0:1] == ["/start"]
        ):
            chats.add(chat["id"])
    if not chats:
        print("Aucun /start privé trouvé. Envoyez /start à votre bot, puis relancez le script.")
        return 1

    print("Identifiants des conversations privées ayant envoyé /start :")
    for chat_id in sorted(chats):
        print(chat_id)
    if clipboard_mode and len(chats) == 1:
        try:
            powershell_clipboard(
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
                value=str(next(iter(chats))),
            )
        except RuntimeError:
            print(
                "Identifiant trouvé ; la copie automatique a échoué. Utilisez le nombre ci-dessus."
            )
        else:
            print(
                "Identifiant copié : collez-le directement dans le secret GitHub TELEGRAM_CHAT_ID."
            )
    else:
        print(
            "Choisissez votre conversation et enregistrez son ID dans le secret TELEGRAM_CHAT_ID."
        )
    print("Le jeton n'a été ni affiché ni enregistré. Aucun message Telegram n'a été envoyé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
