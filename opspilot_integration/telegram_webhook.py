"""Explicit opt-in Telegram webhook switch; preserves previous Render URL.

Telegram does not provide previous secret_token via getWebhookInfo. The local
secret used for restore MUST match the secret on Render.
"""
import json
import os
import re
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
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/{method}',json=params,timeout=20)
        r.raise_for_status()
        data=r.json()
    except (requests.RequestException, ValueError) as error:
        # HTTP exception URLs can contain the bot token. Never surface them.
        raise RuntimeError('Telegram '+method+' failed ('+type(error).__name__+')') from None
    if not isinstance(data, dict) or data.get('ok') is not True or 'result' not in data:
        raise RuntimeError('Telegram API refused '+method)
    return data['result']


def check_route(url):
    """Verify the exact public route and shared secret without an action."""
    if not isinstance(url, str) or not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', url):
        raise ValueError('Expected an HTTPS Cloudflare quick-tunnel base URL, without a path.')
    try:
        response=requests.get(url+'/health', timeout=10, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Public bridge health returned HTTP '+str(response.status_code))
        health=response.json()
        if not isinstance(health, dict) or health.get('local_bridge') is not True:
            raise ValueError('Public URL is not the expected local bridge.')
        probe=requests.post(url+'/telegram/webhook',
            headers={'X-Telegram-Bot-Api-Secret-Token': secret},
            json={}, timeout=10, allow_redirects=False)
        if probe.status_code != 200 or probe.json().get('status') != 'ignored_non_callback':
            raise ValueError('Public webhook authentication check failed; no webhook change made.')
        return health
    except requests.RequestException as error:
        raise ValueError('Public route unavailable ('+type(error).__name__+'). No webhook change made.') from None


def refresh_route(url, confirmed=False):
    """Explicitly refresh a temporary URL; NEVER replace the restore backup."""
    if not state.is_file():
        raise ValueError('Saved webhook backup is missing. Use the explicit on command for first setup.')
    saved=state.read_bytes()
    check_route(url)
    if not confirmed and input('Type SWITCH to refresh the local Telegram route: ').strip() != 'SWITCH':
        raise ValueError('No webhook change made.')
    call('setWebhook', url=url+'/telegram/webhook', secret_token=secret,
         allowed_updates=['callback_query'], drop_pending_updates=False)
    info=call('getWebhookInfo')
    if info.get('url') != url+'/telegram/webhook':
        raise ValueError('Telegram URL verification failed; saved backup retained.')
    if state.read_bytes() != saved:
        raise ValueError('Restore backup changed unexpectedly. Stop and review it locally.')
    return info


def main():
    if len(sys.argv)<2 or sys.argv[1] not in ('on','off','refresh','status'):
        raise SystemExit('Usage: telegram_webhook.py on <url> | refresh <url> | status | off')
    mode=sys.argv[1]
    if mode=='refresh':
        if len(sys.argv)!=3:
            raise SystemExit('Usage: telegram_webhook.py refresh <tunnel_url>')
        refresh_route(sys.argv[2])
        print('PASS: Telegram linked to the verified new tunnel. Saved backup unchanged.')
        return
    info=call('getWebhookInfo')
    print('Current Telegram webhook:',info.get('url') or '(none)')
    if mode=='status':
        print('Pending updates:',info.get('pending_update_count',0))
        message=str(info.get('last_error_message','none'))
        for value in (token,secret):
            if value: message=message.replace(value,'[REDACTED]')
        print('Last delivery error:',message)
        if info.get('last_error_date'):
            import time
            print('Last error age, seconds:',int(time.time()-info['last_error_date']))
        print('Restore backup present:',state.is_file())
        return
    if mode=='on':
        if len(sys.argv)!=3:
            raise SystemExit('Expected a Cloudflare temporary tunnel URL')
        if state.exists():
            raise SystemExit('Existing saved webhook! Run telegram-off.sh before switching again.')
        check_route(sys.argv[2])
        print('\nWARNING: Telegram can use ONE active webhook. Switching temporarily suspends Render bot callbacks.')
        print('IMPORTANT: restoration reuses your LOCAL webhook secret; it must equal the current Render secret.')
        answer=input('Type SWITCH to temporarily direct Telegram callbacks to Ubuntu: ').strip()
        if answer!='SWITCH':
            print('No webhook change made');return
        previous={'url':info.get('url',''),'allowed_updates':info.get('allowed_updates',[])}
        state.parent.mkdir(parents=True,exist_ok=True)
        descriptor=os.open(state,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as output:
            output.write(json.dumps(previous,indent=2))
        # An API timeout has an uncertain outcome. Keep the ONLY restore backup
        # even on failure; refresh can safely verify and repair the local route.
        call('setWebhook',url=sys.argv[2]+'/telegram/webhook',secret_token=secret,allowed_updates=['callback_query'],drop_pending_updates=False)
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
    try:
        main()
    except (ValueError,RuntimeError) as error:
        raise SystemExit(str(error)) from None
