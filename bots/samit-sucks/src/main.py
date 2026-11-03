"""Dispatch draft and gameplay. A failed turn is logged and still yields."""
import traceback

import cambc

import draft
from bot import Bot


def main():
    bot = Bot()
    while True:
        try:
            if cambc.is_draft():
                draft.play()
            else:
                bot.play_turn()
        except Exception:
            print("bot error:\n" + traceback.format_exc(limit=6), flush=True)
        cambc.end_turn()


if __name__ == "__main__":
    main()
