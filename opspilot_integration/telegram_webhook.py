"""Explicit opt-in Telegram webhook switch; preserves previous Render URL.

Telegram does not provide previous secret_token via getWebhookInfo. The local
secret used for restore MUST match the secret on Render.
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
load_dotenv(ROOT/'.env', override=False)
load_dotenv(ROOT/'ai_service/.env', override=False)
state=HERE/'runtime'/'telegram_previous_webhook.json'
token=os.getenv('TELEGRAM_BOT_TOKEN','')
secret=os.getenv('TELEGRAM_WEBHOOK_SECRET','')
if not token or not secret:
    raise SystemExit('Missing TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET in local env. No change made.')


def call(method, **params):
    r=requests.post(f'https://api.telegram.org/bot{token}/{method}',json=params,timeout=20)
    r.raise_for_status()
    data=r.json()
    if not data.get('ok'): raise RuntimeError('Telegram API refused '+method)
    return data['result']


def main():
    if len(sys.argv)<2 or sys.argv[1] not in ('on','off'):
        raise SystemExit('Usage: telegram_webhook.py on <tunnel_url> | off')
    mode=sys.argv[1]
    info=call('getWebhookInfo')
    print('Current Telegram webhook:',info.get('url') or '(none)')
    if mode=='on':
        if len(sys.argv)!=3 or not sys.argv[2].startswith('https://') or not sys.argv[2].endswith('.trycloudflare.com'):
            raise SystemExit('Expected a Cloudflare temporary tunnel URL')
        if state.exists():
            raise SystemExit('Existing saved webhook! Run telegram-off.sh before switching again.')
        print('\nWARNING: Telegram can use ONE active webhook. Switching temporarily suspends Render bot callbacks.')
        print('IMPORTANT: restoration reuses your LOCAL webhook secret; it must equal the current Render secret.')
        answer=input('Type SWITCH to temporarily direct Telegram callbacks to Ubuntu: ').strip()
        if answer!='SWITCH':
            print('No webhook change made');return
        previous={'url':info.get('url',''),'allowed_updates':info.get('allowed_updates',[])}
        state.write_text(json.dumps(previous,indent=2))
        os.chmod(state,0o600)
        try:
            call('setWebhook',url=sys.argv[2].rstrip('/')+'/telegram/webhook',secret_token=secret,allowed_updates=['callback_query'],drop_pending_updates=False)
        except Exception:
            state.unlink(missing_ok=True)
            raise
        print('PASS: Telegram callback webhook is now linked to Ubuntu through Cloudflare Tunnel.')
    else:
        if not state.exists():
            raise SystemExit('No saved previous webhook. Refusing to guess Render URL.')
        previous=json.loads(state.read_text())
        print('\nRestoring former webhook. Its secret must match your local secret for callbacks to work.')
        answer=input('Type RESTORE to restore previous Telegram webhook: ').strip()
        if answer!='RESTORE': raise SystemExit('No change made; leaving Cloudflare tunnel running.')
        if previous['url']:
            params={'url':previous['url'],'secret_token':secret,'drop_pending_updates':False}
            # Explicitly restore message delivery. Telegram otherwise preserves
            # the callback-only filter from the temporary webhook.
            params['allowed_updates']=previous.get('allowed_updates') or ['message','callback_query']
            call('setWebhook',**params)
        else:
            call('deleteWebhook',drop_pending_updates=False)
        state.unlink()
        print('PASS: Previous Telegram webhook URL restored.')

if __name__=='__main__':
    main()
