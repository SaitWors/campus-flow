"""Derive a separate, non-reversible owner activation code from the bot token."""
import getpass
import hashlib
import hmac


def activation_code(token):
    return hmac.new(token.encode(), b'campus-flow/mandarin-habits/owner/v1', hashlib.sha256).hexdigest()[:32]


def main():
    token = getpass.getpass('Токен BotFather (ввод скрыт): ').strip()
    if not token:
        raise SystemExit('Токен не введён.')
    print('Отправь ТОЛЬКО своему боту в личном чате:')
    print('/claim ' + activation_code(token))
    print('Это отдельный код активации. Сам токен в Telegram-чат не отправляй.')


if __name__ == '__main__':
    main()
